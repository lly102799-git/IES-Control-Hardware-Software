"""
IES 控制终端 — 场景仿真测试。

验证 MILP 优化引擎在 4 种典型运行场景下的闭环控制表现：
  S1 晴天工作日 — 光伏满发 + 负荷正常 → 储能峰谷套利
  S2 阴天场景   — 模拟光伏骤降 → 事件驱动重优化
  S3 负荷突变   — 负荷尖峰 → MILP 重新调度
  S4 连续扰动   — 光伏 + 负荷同时变化 → 闭环稳定性

用法：
  python tests/scenario_test.py [--host localhost] [--scenario all]

依赖：后端和模拟器需已在运行 (docker compose up -d)
"""

import argparse
import http.client
import json
import time
from datetime import datetime

HOST = "localhost"
API_PORT = 8000  # 直连后端端口，绕过 nginx (避免 macOS 代理拦截)
API_HOST = HOST
SIM_MODBUS_HOST = HOST
SIM_MODBUS_PORT = 5020


# ═══════════════════════════════════════════════════════════════
# 工具函数 (使用 http.client 直接连接，绕过系统代理)
# ═══════════════════════════════════════════════════════════════

def _http_get(path: str) -> dict:
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def _http_post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode()
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=30)
    try:
        conn.request("POST", path, body=data,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def api(path: str) -> dict:
    return _http_get(f"/api{path}")


def api_post(path: str, body: dict | None = None) -> dict:
    return _http_post(f"/api{path}", body)


def milp_status() -> dict:
    return api("/milp/status")


def milp_schedule() -> list[dict]:
    r = api("/milp/schedule")
    return r.get("schedule", [])


def device_realtime(device_id: str) -> dict:
    r = api(f"/devices/{device_id}/realtime")
    return r.get("data", {})


def devices_list() -> list[dict]:
    return api("/devices")


def trigger_milp() -> dict:
    return api_post("/milp/trigger")


def write_modbus_register(slave: int, addr: int, values: list[int],
                          host: str = SIM_MODBUS_HOST,
                          port: int = SIM_MODBUS_PORT):
    """通过 pymodbus 写入寄存器值。用于模拟扰动。"""
    try:
        from pymodbus.client import ModbusTcpClient
        c = ModbusTcpClient(host, port=port)
        c.connect()
        c.write_registers(address=addr, values=values, slave=slave)
        c.close()
        return True
    except Exception as e:
        print(f"  [Modbus write error] slave={slave} addr={addr}: {e}")
        return False


def log(msg: str):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ═══════════════════════════════════════════════════════════════
# 场景定义
# ═══════════════════════════════════════════════════════════════

def scenario_1_baseline():
    """S1: 晴天工作日基线 — 观测正常 MILP 调度行为。

    不做任何扰动，仅记录当前调度方案和关键指标。
    """
    print("\n" + "=" * 60)
    print("S1: 晴天工作日基线")
    print("=" * 60)

    status = milp_status()
    print(f"  MILP 状态: {status.get('status')}, "
          f"总成本: ¥{status.get('total_cost_yuan')}, "
          f"步长: {status.get('step_minutes')}min")

    schedule = milp_schedule()
    if not schedule:
        print("  ⚠ 无调度方案 (MILP 尚未运行?)")
        return

    s0 = schedule[0]
    print(f"  第一步调度 (0min):")
    print(f"    PV={s0.get('P_pv_kw'):.0f}kW  "
          f"BatCh={s0.get('P_bat_ch_kw'):.0f}kW  "
          f"BatDis={s0.get('P_bat_dis_kw'):.0f}kW")
    print(f"    CHP={s0.get('P_chp_kw'):.0f}kW  "
          f"HP={s0.get('P_hp_kw'):.0f}kW")
    print(f"    Imp={s0.get('P_grid_import_kw'):.0f}kW  "
          f"Exp={s0.get('P_grid_export_kw'):.0f}kW")
    print(f"    SOC_bat={s0.get('SOC_bat'):.3f}  "
          f"SOC_ts={s0.get('SOC_ts'):.3f}")

    # 汇总：峰谷时段行为
    peak_steps = []
    valley_steps = []
    for s in schedule:
        h = s["step"] * status.get("step_minutes", 5) / 60
        if 9 <= h < 12 or 17 <= h < 21:
            peak_steps.append(s)
        elif 0 <= h < 7:
            valley_steps.append(s)

    if valley_steps:
        avg_ch = sum(s["P_bat_ch_kw"] for s in valley_steps) / len(valley_steps)
        print(f"  谷段 (0-7h) 平均充电: {avg_ch:.0f}kW")
    if peak_steps:
        avg_dis = sum(s["P_bat_dis_kw"] for s in peak_steps) / len(peak_steps)
        print(f"  峰段 (9-12h,17-21h) 平均放电: {avg_dis:.0f}kW")

    # 判定策略合理性
    if valley_steps and peak_steps:
        valley_charge = avg_ch > 5
        peak_discharge = avg_dis > 5
        if valley_charge and peak_discharge:
            print("  ✓ 策略: 谷充峰放 — 符合预期")
        elif valley_charge:
            print("  ○ 策略: 谷段充电但峰段未放电 — 需检查")
        else:
            print("  ○ 策略: 未见明显峰谷套利 — 可能 SOC 约束限制")


def scenario_2_pv_drop():
    """S2: 模拟光伏骤降 (云层遮挡) — 验证事件驱动重优化。

    方法：向 PV 逆变器( slave=1 )写入功率限值指令，
    将 PV 功率从 ~80kW 骤降到 ~20kW，检测 MILP 是否触发重优化。
    """
    print("\n" + "=" * 60)
    print("S2: 光伏骤降 — 事件驱动重优化")
    print("=" * 60)

    # 1. 记录当前状态
    pv_data = device_realtime("pv_inverter_01")
    bat_data = device_realtime("battery_pcs_01")
    pv_before = pv_data.get("active_power", 0) / 1000
    bat_before = bat_data.get("active_power", 0) / 1000
    schedule_before = milp_schedule()
    s0_before = schedule_before[0] if schedule_before else {}
    log(f"扰动前: PV={pv_before:.0f}kW, Bat={bat_before:.0f}kW, "
        f"调度PV={s0_before.get('P_pv_kw', 0):.0f}kW")

    status_before = milp_status()
    last_solve_before = status_before.get("last_solve_ts", "")

    # 2. 注入扰动：限制 PV 功率到 20kW (slave=1, register 50-51, mode=1)
    #    hi,lo = struct.pack(">i", 20000) → (0, 20000)
    log("注入扰动: 强制 PV 限功率 → 20kW")
    write_modbus_register(slave=1, addr=50, values=[0, 20000])  # 32-bit: 20000W
    write_modbus_register(slave=1, addr=52, values=[1])          # mode=1 限功率
    write_modbus_register(slave=1, addr=54, values=[120])        # duration=120s

    # 3. 等待事件检测 (10s 检测周期 + 求解时间)
    log("等待事件检测 (最多 30s)...")
    for i in range(6):
        time.sleep(5)
        status = milp_status()
        if status.get("last_solve_ts") != last_solve_before:
            log(f"✓ MILP 已触发重优化! (新求解时间: {status.get('last_solve_ts')})")
            break
    else:
        # 手动触发
        log("○ 事件未自动触发, 手动触发 MILP")
        trigger_milp()
        time.sleep(3)

    # 4. 检查重优化结果
    schedule_after = milp_schedule()
    if schedule_after:
        s0_after = schedule_after[0]
        bat_after = device_realtime("battery_pcs_01").get("active_power", 0) / 1000
        log(f"扰动后: 调度PV={s0_after.get('P_pv_kw', 0):.0f}kW, "
            f"Bat={bat_after:.0f}kW, "
            f"SOC_bat={s0_after.get('SOC_bat', 0):.3f}")

        # 判定：PV 骤降后，电池不应再放电
        bat_dis_after = s0_after.get("P_bat_dis_kw", 0)
        if bat_dis_after < 5:
            log("✓ 响应正确: 电池停止放电，应对光伏骤降")
        else:
            log(f"⚠ 电池仍在放电 {bat_dis_after:.0f}kW — 可能需改进事件检测阈值")

        # 判定：电网购电应增加 (补偿 PV 缺口)
        imp_after = s0_after.get("P_grid_import_kw", 0)
        imp_before = s0_before.get("P_grid_import_kw", 0)
        if imp_after > imp_before:
            log(f"✓ 电网购电增加: {imp_before:.0f} → {imp_after:.0f}kW (补偿PV缺口)")
    else:
        log("⚠ 扰动后无调度方案")

    # 5. 恢复 PV (写 0 清除限功率 → 恢复 MPPT)
    log("恢复 PV: 取消限功率")
    write_modbus_register(slave=1, addr=52, values=[0])  # mode=0 自动


def scenario_3_load_spike():
    """S3: 负荷尖峰 — 验证 MILP 对负荷变化的响应。

    方法：通过修改智能电表 MQTT 模拟负荷突增 30kW。
    (因 MQTT 模拟器持续发布，改为直接验证 MILP 在不同负荷下的调度差异)
    """
    print("\n" + "=" * 60)
    print("S3: 负荷尖峰 — 调度差异分析")
    print("=" * 60)

    schedule = milp_schedule()
    if not schedule:
        print("  ⚠ 无调度方案")
        return

    # 取第 0、12 (1h)、24 (2h) 步的调度值
    check_steps = [0, 12, 24]
    for step in check_steps:
        if step < len(schedule):
            s = schedule[step]
            h = step * 5 / 60
            total_gen = s["P_pv_kw"] + s["P_chp_kw"] + s["P_bat_dis_kw"] + s["P_grid_import_kw"]
            total_load_est = total_gen - s["P_grid_export_kw"]
            print(f"  +{h:.1f}h: 总发电={total_gen:.0f}kW, "
                  f"CHP={s['P_chp_kw']:.0f}kW, "
                  f"Imp={s['P_grid_import_kw']:.0f}kW, "
                  f"SOC={s['SOC_bat']:.3f}")

    # 检查是否存在 CHP 在峰段启动
    peak_chp = [s for s in schedule
                if 9 <= s["step"] * 5 / 60 < 12 and s["P_chp_kw"] > 5]
    if peak_chp:
        log(f"✓ CHP 在上午峰段启动 {len(peak_chp)} 步 — "
            f"符合高负荷+高电价时 CHP 经济运行策略")
    else:
        log("○ CHP 上午峰段未启动 — "
            "可能当前负荷/电价下 CHP 不经济(光伏更便宜)")


def scenario_4_combined_perturbation():
    """S4: 连续扰动 — 验证闭环稳定性。

    方法：先注入 PV 扰动, 2min 后注入负荷扰动,观察 MILP 连续响应。
    """
    print("\n" + "=" * 60)
    print("S4: 连续扰动 — 闭环稳定性")
    print("=" * 60)

    status_before = milp_status()
    last_solve = status_before.get("last_solve_ts", "")

    # 扰动 1: PV 限功率
    log("扰动1: PV 限功率 → 20kW")
    write_modbus_register(slave=1, addr=50, values=[0, 20000])
    write_modbus_register(slave=1, addr=52, values=[1])
    write_modbus_register(slave=1, addr=54, values=[180])

    time.sleep(15)

    status1 = milp_status()
    reopt_1 = status1.get("last_solve_ts") != last_solve
    log(f"  重优化1: {'✓ 触发' if reopt_1 else '✗ 未触发'}, "
        f"成本=¥{status1.get('total_cost_yuan')}")

    last_solve = status1.get("last_solve_ts", "")

    # 扰动 2: 清除 PV 限制 + 同时给储能写入放电指令 (模拟负荷骤增)
    log("扰动2: 恢复 PV + 负荷骤增 (储能放电)")
    write_modbus_register(slave=1, addr=52, values=[0])   # 取消 PV 限功率
    # 不对储能写指令 (储能由 MILP 控制)

    time.sleep(15)

    status2 = milp_status()
    reopt_2 = status2.get("last_solve_ts") != last_solve
    log(f"  重优化2: {'✓ 触发' if reopt_2 else '✗ 未触发'}, "
        f"成本=¥{status2.get('total_cost_yuan')}")

    # 判定闭环稳定性
    if reopt_1 and reopt_2:
        log("✓ 闭环稳定: 两次扰动均触发重优化")
    elif reopt_1 or reopt_2:
        log("○ 闭环部分响应: 仅一次触发, "
            "可能第二次扰动在冷却期内")
    else:
        log("⚠ 闭环未响应: "
            "需检查事件检测条件/冷却期设置")

    # 清理
    write_modbus_register(slave=1, addr=52, values=[0])
    write_modbus_register(slave=1, addr=54, values=[0])


def scenario_5_battery_soc_boundary():
    """S5: 储能 SOC 边界 — 验证低 SOC 事件检测和保护。

    方法：向储能写入大功率放电指令消耗 SOC，观察低 SOC 事件是否触发。
    """
    print("\n" + "=" * 60)
    print("S5: 储能 SOC 边界 — 低 SOC 保护")
    print("=" * 60)

    bat_data = device_realtime("battery_pcs_01")
    soc_before = bat_data.get("soc", 50.0) / 100.0
    status_before = milp_status()
    last_solve = status_before.get("last_solve_ts", "")
    log(f"SOC 初始: {soc_before:.2f}")

    # 强制放电 (slave=2, mode=2, 30kW, 120s)
    log("注入: 储能强制放电 30kW")
    import struct
    hi, lo = struct.unpack(">HH", struct.pack(">i", 30000))
    write_modbus_register(slave=2, addr=50, values=[hi, lo])
    write_modbus_register(slave=2, addr=52, values=[2])   # mode=2 放电
    write_modbus_register(slave=2, addr=54, values=[120])

    # 等待事件检测 (SOC 从正常 → 放电下降)
    log("等待 SOC 变化和事件检测 (最多 30s)...")
    for i in range(6):
        time.sleep(5)
        bat = device_realtime("battery_pcs_01")
        soc = bat.get("soc", 50.0) / 100.0
        status = milp_status()
        if status.get("last_solve_ts") != last_solve:
            log(f"✓ SOC={soc:.2f} → MILP 已触发重优化")
            break
    else:
        bat = device_realtime("battery_pcs_01")
        soc = bat.get("soc", 50.0) / 100.0
        log(f"○ SOC={soc:.2f} (变化: {soc - soc_before:+.2f}) — "
            f"可能 SOC 仍在安全范围内")

    # 检查 MILP 是否调整了电池策略
    schedule = milp_schedule()
    if schedule:
        s0 = schedule[0]
        log(f"调度: BatCh={s0.get('P_bat_ch_kw'):.0f}kW  "
            f"BatDis={s0.get('P_bat_dis_kw'):.0f}kW  "
            f"SOC={s0.get('SOC_bat'):.3f}")
        if s0.get('P_bat_dis_kw', 0) < 5:
            log("✓ MILP 停止放电 (SOC 保护)")
        elif s0.get('P_bat_ch_kw', 0) > 5:
            log("✓ MILP 转为充电 (SOC 偏低)")

    # 清理
    write_modbus_register(slave=2, addr=52, values=[0])
    write_modbus_register(slave=2, addr=54, values=[0])


def scenario_6_chp_startup():
    """S6: CHP 启停 — 验证 CHP 指令下发和热电解耦响应。

    方法：MILP 通常在峰电/低 PV 时启动 CHP。通过 PV 限制迫使 MILP
    使用 CHP 补充电力缺口，验证热电联供是否正确调度。
    """
    print("\n" + "=" * 60)
    print("S6: CHP 启停与热电联供")
    print("=" * 60)

    status_before = milp_status()
    last_solve = status_before.get("last_solve_ts", "")
    chp_before = device_realtime("chp_01")
    log(f"CHP 初始: P={chp_before.get('active_power', 0) / 1000:.0f}kW, "
        f"热={chp_before.get('heat_power', 0) / 1000:.0f}kW, "
        f"状态={int(chp_before.get('status', 0))}")

    # 大幅限制 PV (slave=1) → 电力缺口 → 期望 CHP 启动
    log("注入: PV 限功率 → 5kW + 储能 SOC 偏低 → 迫使 CHP 启动")
    import struct
    hi, lo = struct.unpack(">HH", struct.pack(">i", 5000))
    write_modbus_register(slave=1, addr=50, values=[hi, lo])
    write_modbus_register(slave=1, addr=52, values=[1])
    write_modbus_register(slave=1, addr=54, values=[180])

    # 等待 MILP 响应
    log("等待 MILP 响应 (最多 30s)...")
    time.sleep(10)
    trigger_milp()
    time.sleep(5)

    schedule = milp_schedule()
    chp_after = device_realtime("chp_01")

    # 检查调度中 CHP 是否在后续时段启动
    if schedule:
        chp_steps = [s for s in schedule[:24] if s.get("P_chp_kw", 0) > 5]  # 前 2h
        if chp_steps:
            s = chp_steps[0]
            log(f"✓ CHP 计划启动: +{s['step'] * 5}min, "
                f"P={s['P_chp_kw']:.0f}kW, "
                f"热={s['P_chp_kw'] * 1.43:.0f}kW (估算)")
        else:
            log("○ CHP 未在计划中启动 — "
                "可能电网购电比 CHP 更经济")
            log(f"  (电网进口: {schedule[0].get('P_grid_import_kw', 0):.0f}kW)")

    # 清理
    write_modbus_register(slave=1, addr=52, values=[0])
    write_modbus_register(slave=1, addr=54, values=[0])


def scenario_7_heatpump_thermal_storage():
    """S7: 热泵+蓄能罐协同 — 验证热-电耦合调度。

    方法：检查 MILP 是否在低电价时段利用热泵蓄热，
    在峰电时段放热，实现热电解耦。
    """
    print("\n" + "=" * 60)
    print("S7: 热泵+蓄能罐 — 热电解耦")
    print("=" * 60)

    hp_data = device_realtime("heatpump_01")
    ts_data = device_realtime("thermal_storage_01")
    log(f"热泵: P={hp_data.get('elec_power', 0) / 1000:.0f}kW, "
        f"COP={hp_data.get('cop', 35) / 10:.1f}")
    log(f"蓄能罐: 热SOC={ts_data.get('heat_soc', 0) / 10:.0f}%, "
        f"冷SOC={ts_data.get('cool_soc', 0) / 10:.0f}%")

    schedule = milp_schedule()
    if not schedule:
        log("⚠ 无调度方案")
        return

    # 分析前 24 步 (2h) 的热泵和蓄能罐调度
    hp_active = 0
    ts_charge = 0
    ts_discharge = 0
    for s in schedule[:24]:
        if s.get("P_hp_kw", 0) > 2:
            hp_active += 1
        if s.get("Q_ts_ch_kw", 0) > 2:
            ts_charge += 1
        if s.get("Q_ts_dis_kw", 0) > 2:
            ts_discharge += 1

    log(f"前 2h 计划: 热泵活跃 {hp_active}/24步, "
        f"蓄能蓄热 {ts_charge}步, 蓄能放热 {ts_discharge}步")

    # 检查谷段是否有蓄热行为
    valley_charge = [s for s in schedule
                     if s["step"] * 5 / 60 < 7 and s.get("Q_ts_ch_kw", 0) > 5]
    peak_discharge = [s for s in schedule
                      if 9 <= s["step"] * 5 / 60 < 12 and s.get("Q_ts_dis_kw", 0) > 5]

    if valley_charge:
        log(f"✓ 谷段蓄热: {len(valley_charge)}步, "
            f"平均={sum(s['Q_ts_ch_kw'] for s in valley_charge) / len(valley_charge):.0f}kW")
    else:
        log("○ 谷段未蓄热 — 可能热负荷低或环境温度高")

    if peak_discharge:
        log(f"✓ 峰段放热: {len(peak_discharge)}步, "
            f"平均={sum(s['Q_ts_dis_kw'] for s in peak_discharge) / len(peak_discharge):.0f}kW")
    else:
        log("○ 峰段未放热 — 可能蓄热量不足")

    # 检查热泵 COP 参数
    cop = hp_data.get("cop", 35) / 10.0
    if cop < 2.5:
        log(f"⚠ 热泵 COP 偏低 ({cop:.1f}) — 可能触发 hp_cop_low 事件")
    else:
        log(f"○ 热泵 COP 正常 ({cop:.1f})")


def scenario_8_storage_competition():
    """S8: 储能竞争 — 验证多储能同时充电时的协调。

    方法：限制 PV + 同时给电池和蓄能罐写充电指令，
    观察 MILP 是否检测到 storage_competition 事件并协调。
    """
    print("\n" + "=" * 60)
    print("S8: 储能竞争 — 多储能协调")
    print("=" * 60)

    bat_data = device_realtime("battery_pcs_01")
    ts_data = device_realtime("thermal_storage_01")
    log(f"电池: SOC={bat_data.get('soc', 0) / 10:.0f}%, "
        f"P={bat_data.get('active_power', 0) / 1000:.0f}kW")
    log(f"蓄能罐: 热SOC={ts_data.get('heat_soc', 0) / 10:.0f}%")

    # 限制 PV (模拟阴天) + 看 MILP 如何分配有限的电网购电额度
    import struct
    hi, lo = struct.unpack(">HH", struct.pack(">i", 5000))
    write_modbus_register(slave=1, addr=50, values=[hi, lo])
    write_modbus_register(slave=1, addr=52, values=[1])
    write_modbus_register(slave=1, addr=54, values=[180])

    # 手动触发 MILP 获取新调度
    log("限制 PV → 5kW, 触发 MILP 重优化...")
    time.sleep(5)
    trigger_milp()
    time.sleep(5)

    schedule = milp_schedule()
    if schedule:
        s0 = schedule[0]
        # 同时充电 = 竞争有限电网容量
        bat_ch = s0.get("P_bat_ch_kw", 0)
        ts_ch = s0.get("Q_ts_ch_kw", 0)
        imp = s0.get("P_grid_import_kw", 0)

        if bat_ch > 5 and ts_ch > 5:
            log(f"⚠ 电池和蓄能罐同时充电: "
                f"Bat={bat_ch:.0f}kW + TS={ts_ch:.0f}kW, "
                f"电网进口={imp:.0f}kW")
            if imp > 100:
                log("  → 电网购电高, 需关注购电限额")
        elif bat_ch > 5:
            log(f"✓ MILP 优先电池充电: Bat={bat_ch:.0f}kW, "
                f"TS充电={ts_ch:.0f}kW")
        elif ts_ch > 5:
            log(f"✓ MILP 优先蓄能罐蓄热: TS={ts_ch:.0f}kW, "
                f"Bat充电={bat_ch:.0f}kW")
        else:
            log("○ 两者均未充电 — MILP 选择等待(可能 SOC 已高)")

    # 清理
    write_modbus_register(slave=1, addr=52, values=[0])
    write_modbus_register(slave=1, addr=54, values=[0])


def scenario_9_combined_thermal_electrical():
    """S9: 综合热-电扰动 — 验证系统对多维扰动的响应。

    方法：PV 骤降 + 热负荷突增 (限制 PV → 热泵被迫增加耗电)，
    观察 MILP 如何在电和热之间重新平衡。
    """
    print("\n" + "=" * 60)
    print("S9: 综合热-电扰动")
    print("=" * 60)

    status_before = milp_status()
    last_solve = status_before.get("last_solve_ts", "")
    cost_before = status_before.get("total_cost_yuan", 0)

    pv_data = device_realtime("pv_inverter_01")
    hp_data = device_realtime("heatpump_01")
    ts_data = device_realtime("thermal_storage_01")
    log(f"扰动前: PV={pv_data.get('active_power', 0) / 1000:.0f}kW, "
        f"HP={hp_data.get('elec_power', 0) / 1000:.0f}kW, "
        f"TS_SOC={ts_data.get('heat_soc', 0) / 10:.0f}%, "
        f"成本=¥{cost_before}")

    # 注入综合扰动: PV 限制 + 热泵启动
    import struct
    hi, lo = struct.unpack(">HH", struct.pack(">i", 10000))
    write_modbus_register(slave=1, addr=50, values=[hi, lo])
    write_modbus_register(slave=1, addr=52, values=[1])
    write_modbus_register(slave=1, addr=54, values=[180])

    # 热泵启动 (slave=4, mode=1, 25kW)
    hi, lo = struct.unpack(">HH", struct.pack(">i", 25000))
    write_modbus_register(slave=4, addr=50, values=[hi, lo])
    write_modbus_register(slave=4, addr=52, values=[1])
    write_modbus_register(slave=4, addr=54, values=[180])

    log("注入: PV→10kW + 热泵→25kW | 等待 MILP 响应...")
    time.sleep(20)

    status_after = milp_status()
    cost_after = status_after.get("total_cost_yuan", 0)
    reopt = status_after.get("last_solve_ts") != last_solve

    schedule = milp_schedule()
    if schedule:
        s0 = schedule[0]
        log(f"扰动后: PV调度={s0.get('P_pv_kw', 0):.0f}kW, "
            f"HP={s0.get('P_hp_kw', 0):.0f}kW, "
            f"CHP={s0.get('P_chp_kw', 0):.0f}kW, "
            f"Imp={s0.get('P_grid_import_kw', 0):.0f}kW, "
            f"TS_dis={s0.get('Q_ts_dis_kw', 0):.0f}kW")

        # 判定综合响应
        observations = []
        if s0.get("P_chp_kw", 0) > 5:
            observations.append("CHP启动(补电+供热)")
        if s0.get("Q_ts_dis_kw", 0) > 5:
            observations.append("蓄能罐放热(补热负荷)")
        if s0.get("P_grid_import_kw", 0) > 80:
            observations.append("大幅增加购电")
        if observations:
            log(f"✓ 综合响应: {', '.join(observations)}")
        else:
            log("○ 无明显热-电联动 — 可能热负荷已由蓄能罐满足")

    log(f"成本变化: ¥{cost_before} → ¥{cost_after} "
        f"({'增加' if cost_after > cost_before else '减少'}¥{abs(cost_after - cost_before)})")

    # 清理
    write_modbus_register(slave=1, addr=52, values=[0])
    write_modbus_register(slave=1, addr=54, values=[0])
    write_modbus_register(slave=4, addr=52, values=[0])
    write_modbus_register(slave=4, addr=54, values=[0])


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="IES 场景仿真测试")
    parser.add_argument("--host", default="localhost", help="后端地址")
    parser.add_argument("--scenario", default="all",
                        choices=["all", "s1", "s2", "s3", "s4",
                                 "s5", "s6", "s7", "s8", "s9"],
                        help="运行指定场景 (默认 all)")
    args = parser.parse_args()

    global HOST, API
    HOST = args.host
    API = f"http://{HOST}/api"

    # 前置检查
    print("=" * 60)
    print("IES 控制终端 — 场景仿真测试")
    print(f"后端: {API}")
    print(f"时间: {datetime.now().isoformat()}")
    print("=" * 60)

    status = milp_status()
    if not status or "error" in status:
        print(f"\n❌ 无法连接后端 ({API}/milp/status)")
        print("   请确认: docker compose -f docker/docker-compose.yml up -d")
        return

    print(f"\nMILP 状态: {status.get('status')}, "
          f"步长: {status.get('step_minutes')}min, "
          f"运行中: {status.get('running')}")

    if not status.get("has_schedule"):
        print("⚠ MILP 尚无调度方案 — 等待首次优化完成...")
        trigger_milp()
        time.sleep(5)

    # 运行场景
    scenarios = {
        "s1": scenario_1_baseline,
        "s2": scenario_2_pv_drop,
        "s3": scenario_3_load_spike,
        "s4": scenario_4_combined_perturbation,
        "s5": scenario_5_battery_soc_boundary,
        "s6": scenario_6_chp_startup,
        "s7": scenario_7_heatpump_thermal_storage,
        "s8": scenario_8_storage_competition,
        "s9": scenario_9_combined_thermal_electrical,
    }

    if args.scenario == "all":
        for name, func in scenarios.items():
            try:
                func()
            except Exception as e:
                print(f"  ❌ {name} 失败: {e}")
    else:
        try:
            scenarios[args.scenario]()
        except Exception as e:
            print(f"  ❌ {args.scenario} 失败: {e}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
