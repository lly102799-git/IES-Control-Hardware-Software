"""
MILP 经济调度优化引擎 v2。

改进：
  - 5 分钟时间分辨率 (288 步/24h)，指令下发间隔 5min
  - 设备爬坡速率约束 (ramp rate)，防止功率突变
  - 事件驱动重优化：检测设备级和系统级偏差，触发即时求解
  - MPC 滚动优化：仅执行第一步指令，每 5min 重新评估

输入：设备约束 (device_models.py) + 分时电价 (price_config.json)
       + 预测 (forecast_service / TDengine) + 实时状态 (collector)

输出：288 步调度方案 → 提取第一步 → 转为控制指令 → 下发设备
"""

import asyncio
import json
import math
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from ..core.config import settings
from ..core.tdengine import td_manager
from .collector import collector

try:
    from pulp import (LpProblem, LpMinimize, LpVariable, LpBinary,
                      LpStatus, lpSum, value, PULP_CBC_CMD)
    HAS_PULP = True
except ImportError:
    HAS_PULP = False

PRICE_FILE = os.path.join(os.path.dirname(__file__), "price_config.json")


# ═══════════════════════════════════════════════════════════════
# 事件检测器
# ═══════════════════════════════════════════════════════════════

class EventDetector:
    """检测设备偏差和系统级条件，触发即时重优化。

    两类事件：
      设备级 — 单设备状态显著偏离预期 (PV 骤降、SOC 接近限值等)
      系统级 — 多设备间的耦合条件 (功率失衡、储能竞争、价格切换等)
    """

    def __init__(self):
        self._last_pv_power: float = 0.0
        self._last_load: float = 0.0
        self._last_price_period: str = ""
        self._event_cooldown: dict[str, float] = {}  # event_name → last fire time
        self._min_cooldown: float = 120  # 同类型事件最小间隔 (秒)

    # ── 设备级事件 ──────────────────────────────────────────

    def _check_pv(self, current: dict, schedule: list[dict] | None) -> list[str]:
        """光伏逆变器事件"""
        events = []
        actual_power = current.get("active_power", 0) / 1000  # W → kW
        pv_rated = 100.0

        # 事件: PV 骤降 — 实际功率与预测偏差 >30%
        if schedule and len(schedule) > 0:
            expected = schedule[0].get("P_pv_kw", 0)
            if expected > 10:  # 仅当预测值有意义时检测
                deviation = abs(actual_power - expected) / expected
                if deviation > 0.30:
                    events.append(f"pv_deviation:{deviation:.0%}")

        # 事件: PV 快速变化 — 两次检测间变化 >20% 额定功率
        if self._last_pv_power > 0:
            delta = abs(actual_power - self._last_pv_power)
            if delta > pv_rated * 0.20:
                events.append(f"pv_ramp:{delta:.0f}kW")

        self._last_pv_power = actual_power
        return events

    def _check_battery(self, current: dict) -> list[str]:
        """储能变流器事件"""
        events = []
        soc = current.get("soc", 50.0) / 100.0
        power = current.get("active_power", 0) / 1000

        # SOC 预警 (接近限值)
        if soc < 0.15:
            events.append(f"battery_soc_low:{soc:.2f}")
        elif soc > 0.90:
            events.append(f"battery_soc_high:{soc:.2f}")

        # SOC 临界 (非常接近硬限值)
        if soc < 0.12:
            events.append(f"battery_soc_critical_low:{soc:.2f}")
        elif soc > 0.93:
            events.append(f"battery_soc_critical_high:{soc:.2f}")

        # 功率方向反转 (充放切换)
        status = int(current.get("status", 0))
        if status == 0 and abs(power) < 0.5:
            pass  # idle, no event
        elif status in (1, 2) and abs(power) < 0.5:
            events.append("battery_idle_unexpected")

        return events

    def _check_chp(self, current: dict) -> list[str]:
        """CHP 事件"""
        events = []
        power = current.get("active_power", 0) / 1000
        status = int(current.get("status", 0))
        heat_power = current.get("heat_power", 0) / 1000

        # 电效率异常下降 (>10% 偏离额定)
        eff = current.get("elec_efficiency", 35.0) / 100.0
        if eff < 0.30 and power > 10:
            events.append(f"chp_efficiency_low:{eff:.2f}")

        # 热电比异常 (余热回收失效)
        if power > 10 and heat_power / max(power, 0.01) < 0.8:
            events.append("chp_heat_loss")

        # 启停状态变化
        if status == 1 and power < 5:
            events.append("chp_startup")

        return events

    def _check_heatpump(self, current: dict) -> list[str]:
        """热泵事件"""
        events = []
        cop = current.get("cop", 3.5) / 10.0  # point table scale ×10
        power = current.get("elec_power", 0) / 1000

        # COP 显著下降 (>20% 偏离额定)
        if cop < 2.8 and power > 5:
            events.append(f"hp_cop_low:{cop:.1f}")

        # 出力温度偏离设定
        temp_out = current.get("temp_out", 45.0) / 10.0
        if temp_out < 30 and power > 5:
            events.append(f"hp_temp_low:{temp_out:.0f}")

        return events

    def _check_thermal_storage(self, current: dict) -> list[str]:
        """蓄能罐事件"""
        events = []
        heat_soc = current.get("heat_soc", 50.0) / 100.0
        cool_soc = current.get("cool_soc", 50.0) / 100.0

        if heat_soc < 0.10:
            events.append(f"ts_soc_low:{heat_soc:.2f}")
        elif heat_soc > 0.90:
            events.append(f"ts_soc_high:{heat_soc:.2f}")
        # 蓄能罐 SOC 变化率过大 (可能泄漏)
        # (需要历史数据对比，暂简化为阈值)

        return events

    # ── 系统级事件 ──────────────────────────────────────────

    def _check_system(self, current_all: dict[str, dict],
                      schedule: list[dict] | None) -> list[str]:
        """系统级跨设备事件"""
        events = []

        # 收集各设备功率
        pv_pwr = current_all.get("pv_inverter_01", {}).get("active_power", 0) / 1000
        bat_pwr = current_all.get("battery_pcs_01", {}).get("active_power", 0) / 1000
        chp_pwr = current_all.get("chp_01", {}).get("active_power", 0) / 1000
        hp_pwr = current_all.get("heatpump_01", {}).get("elec_power", 0) / 1000
        ts_pwr = current_all.get("thermal_storage_01", {}).get("power", 0) / 1000
        meter_pwr = current_all.get("smart_meter_01", {}).get("active_power", 0) / 1000
        bat_soc = current_all.get("battery_pcs_01", {}).get("soc", 50.0) / 100.0
        ts_soc = current_all.get("thermal_storage_01", {}).get("heat_soc", 50.0) / 100.0

        # 1. 功率失衡: |发电 - 负荷| > 20kW
        generation = pv_pwr + max(0, bat_pwr) + chp_pwr
        consumption = abs(min(0, bat_pwr)) + hp_pwr + abs(ts_pwr)
        imbalance = abs(generation - consumption - meter_pwr)
        if imbalance > 20:
            events.append(f"power_imbalance:{imbalance:.0f}kW")

        # 2. 储能竞争: 电池和蓄能罐同时在光伏不足时充电
        bat_charging = bat_pwr < -5     # 负值 = 充电
        ts_charging = ts_pwr > 5        # 正值 = 蓄热
        if bat_charging and ts_charging and pv_pwr < 30:
            events.append("storage_competition")

        # 3. 净负荷反转: PV 下降速率 > 负荷下降速率
        # (简化为检测 PV 骤降而负荷未变)
        if self._last_pv_power > 0:
            pv_ramp = (pv_pwr - self._last_pv_power) / 5  # kW/min, ~5s check
            if pv_ramp < -100 and abs(meter_pwr) < 30:
                events.append(f"net_load_reversal:{pv_ramp:.0f}kW/min")

        # 4. 分时电价切换: 进入新的电价时段
        now = datetime.now()
        hour = now.hour
        if hour in [0, 7, 9, 17, 22]:  # 电价切换边界小时
            period = self._price_period(hour)
            if period != self._last_price_period:
                events.append(f"price_change:{self._last_price_period}→{period}")
                self._last_price_period = period

        # 5. 热电解耦失衡: CHP 余热不足而热泵未启动补偿
        chp_heat = chp_pwr * 1.43
        hp_heat = hp_pwr * 3.5
        total_heat = chp_heat + hp_heat
        # 简单判断：CHP 在运行但总热输出 < 热负荷估计
        if chp_pwr > 10 and total_heat < 20:
            events.append("thermal_gap")

        self._last_load = meter_pwr
        return events

    # ── 统一检测接口 ────────────────────────────────────────

    def detect(self, schedule: list[dict] | None = None) -> list[str]:
        """执行全部事件检测，返回触发的事件列表 (含冷却期过滤)。"""
        current = collector.get_latest()
        if not current:
            return []

        events: list[str] = []

        # 设备级检测
        for dev_id, checker in [
            ("pv_inverter_01", self._check_pv),
            ("battery_pcs_01", self._check_battery),
            ("chp_01", self._check_chp),
            ("heatpump_01", self._check_heatpump),
            ("thermal_storage_01", self._check_thermal_storage),
        ]:
            if dev_id in current:
                dev_events = checker(current[dev_id])
                events.extend(dev_events)

        # 系统级检测
        sys_events = self._check_system(current, schedule)
        events.extend(sys_events)

        # 冷却期过滤
        now = time.time()
        filtered: list[str] = []
        for evt in events:
            category = evt.split(":")[0]  # 按事件类别冷却
            last = self._event_cooldown.get(category, 0)
            if now - last >= self._min_cooldown:
                filtered.append(evt)
                self._event_cooldown[category] = now

        return filtered

    @staticmethod
    def _price_period(hour: int) -> str:
        if hour in (9, 10, 11, 12, 17, 18, 19, 20, 21):
            return "peak"
        elif hour in (7, 8, 13, 14, 15, 16, 22, 23):
            return "flat"
        return "valley"


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _load_price_config() -> dict:
    with open(PRICE_FILE) as f:
        return json.load(f)


def _build_price_arrays(config: dict, num_steps: int,
                        step_minutes: int = 5) -> tuple[list[float], list[float]]:
    """构建每时间步的购/售电价数组 (¥/kWh)。"""
    elec = config["electricity"]
    hour_map: dict[int, tuple[float, float]] = {}
    for period in ["peak", "flat", "valley"]:
        p_cfg = elec[period]
        for h in p_cfg["hours"]:
            hour_map[h] = (p_cfg["price_import"], p_cfg["price_export"])

    import_prices, export_prices = [], []
    for t in range(num_steps):
        hour = (t * step_minutes) // 60
        ip, ep = hour_map.get(hour, (0.7, 0.5))
        import_prices.append(ip)
        export_prices.append(ep)
    return import_prices, export_prices


def _estimate_heat_load(ambient_temp: float, hour: float,
                        base_load: float = 25.0) -> float:
    """估算建筑热负荷 (kW)。"""
    if 6 <= hour < 9:
        occupancy = 1.3
    elif 9 <= hour < 17:
        occupancy = 0.6
    elif 17 <= hour < 22:
        occupancy = 1.2
    else:
        occupancy = 0.4
    delta_t = max(0, 20.0 - ambient_temp)
    return max(0, base_load * occupancy * (1.0 + delta_t * 0.1))


# ═══════════════════════════════════════════════════════════════
# MILP 优化器
# ═══════════════════════════════════════════════════════════════

class MilpOptimizer:
    def __init__(self):
        from .device_models import get_device_params
        self.params = get_device_params()
        self.prices = _load_price_config()
        self._last_schedule: list[dict] | None = None
        self._last_solve_status: str = "idle"
        self._last_solve_cost: float = 0.0
        self._last_solve_ts: str = ""
        self._running = False
        self._prev_power: dict[str, float] = {}  # 上一指令的设备功率 (用于爬坡约束初始值)
        self.detector = EventDetector()

    # ── 模型构建 ──────────────────────────────────────────

    def _build_model(self, pv_forecast: list[float],
                     load_forecast: list[float],
                     heat_load_forecast: list[float],
                     soc_bat_0: float, soc_ts_0: float,
                     ambient_temp: float = 10.0) -> tuple:
        """构建 288 步 MILP 模型（5 分钟分辨率，含爬坡约束）。"""
        if not HAS_PULP:
            raise RuntimeError("PuLP not installed")

        T = self.params["_meta"]["num_steps"]          # 288
        dt = self.params["_meta"]["dt_hours"]          # 1/12
        step_min = self.params["_meta"]["step_minutes"]  # 5

        pv_p = self.params["pv_inverter_01"]
        bat = self.params["battery_pcs_01"]
        chp_p = self.params["chp_01"]
        hp_p = self.params["heatpump_01"]
        ts_p = self.params["thermal_storage_01"]
        grid_imp_max = self.params["_meta"]["grid_import_max"]
        grid_exp_max = self.params["_meta"]["grid_export_max"]

        import_prices, export_prices = _build_price_arrays(
            self.prices, T, step_min)
        gas_price = self.prices["gas_price"]

        cop = hp_p["cop_default"] * (1.0 + 0.03 * (ambient_temp - 7.0))
        cop = max(1.5, min(5.0, cop))

        # ── 问题 ──
        prob = LpProblem("IES_MILP_5min", LpMinimize)

        # ── 连续变量 ──
        P_pv = {t: LpVariable(f"Ppv_{t}", 0, pv_forecast[t] if t < len(pv_forecast) else 0)
                for t in range(T)}
        P_bat_ch = {t: LpVariable(f"Pbc_{t}", 0, bat["p_ch_max"]) for t in range(T)}
        P_bat_dis = {t: LpVariable(f"Pbd_{t}", 0, bat["p_dis_max"]) for t in range(T)}
        P_chp = {t: LpVariable(f"Pcp_{t}", 0, chp_p["p_max"]) for t in range(T)}
        P_hp = {t: LpVariable(f"Php_{t}", 0, hp_p["p_max"]) for t in range(T)}
        P_imp = {t: LpVariable(f"Pim_{t}", 0, grid_imp_max) for t in range(T)}
        P_exp = {t: LpVariable(f"Pex_{t}", 0, grid_exp_max) for t in range(T)}
        Q_ts_ch = {t: LpVariable(f"Qtc_{t}", 0, ts_p["p_ch_max"]) for t in range(T)}
        Q_ts_dis = {t: LpVariable(f"Qtd_{t}", 0, ts_p["p_dis_max"]) for t in range(T)}
        SOC_bat = {t: LpVariable(f"Sb_{t}", bat["soc_min"], bat["soc_max"])
                   for t in range(T)}
        SOC_ts = {t: LpVariable(f"St_{t}", ts_p["soc_min"], ts_p["soc_max"])
                  for t in range(T)}

        # ── 二进制变量 ──
        u_bat_ch = {t: LpVariable(f"uBC_{t}", cat=LpBinary) for t in range(T)}
        u_bat_dis = {t: LpVariable(f"uBD_{t}", cat=LpBinary) for t in range(T)}
        u_chp = {t: LpVariable(f"uCP_{t}", cat=LpBinary) for t in range(T)}
        u_ts_ch = {t: LpVariable(f"uTC_{t}", cat=LpBinary) for t in range(T)}
        u_ts_dis = {t: LpVariable(f"uTD_{t}", cat=LpBinary) for t in range(T)}

        # ── 逐步约束 ──
        for t in range(T):
            ld = load_forecast[t] if t < len(load_forecast) else 0
            hld = heat_load_forecast[t] if t < len(heat_load_forecast) else 0

            # 电功率平衡
            prob += (P_pv[t] + P_bat_dis[t] + P_chp[t] + P_imp[t]
                     == ld + P_bat_ch[t] + P_hp[t] + P_exp[t])

            # 热功率平衡
            Q_chp_t = P_chp[t] * chp_p["hpr"]
            Q_hp_t = P_hp[t] * cop
            prob += (Q_chp_t + Q_hp_t + Q_ts_dis[t]
                     == hld + Q_ts_ch[t])

            # 充放互斥
            prob += (u_bat_ch[t] + u_bat_dis[t] <= 1)
            prob += (P_bat_ch[t] <= bat["p_ch_max"] * u_bat_ch[t])
            prob += (P_bat_dis[t] <= bat["p_dis_max"] * u_bat_dis[t])

            prob += (u_ts_ch[t] + u_ts_dis[t] <= 1)
            prob += (Q_ts_ch[t] <= ts_p["p_ch_max"] * u_ts_ch[t])
            prob += (Q_ts_dis[t] <= ts_p["p_dis_max"] * u_ts_dis[t])

            # CHP 最小出力
            prob += (P_chp[t] >= chp_p["p_min"] * u_chp[t])
            prob += (P_chp[t] <= chp_p["p_max"] * u_chp[t])

        # ── 爬坡约束 (相邻时间步功率变化限制) ──
        # 从上一指令值到第一步也需要爬坡约束
        prev_bat_ch = self._prev_power.get("battery_pcs_01_ch", 0)
        prev_bat_dis = self._prev_power.get("battery_pcs_01_dis", 0)
        prev_chp = self._prev_power.get("chp_01", 0)
        prev_hp = self._prev_power.get("heatpump_01", 0)
        prev_ts_ch = self._prev_power.get("thermal_storage_01_ch", 0)
        prev_ts_dis = self._prev_power.get("thermal_storage_01_dis", 0)

        for prev_val, var_dict, ramp, label in [
            (prev_bat_ch, P_bat_ch, bat["ramp_up"], "bat_ch"),
            (prev_bat_dis, P_bat_dis, bat["ramp_up"], "bat_dis"),
            (prev_chp, P_chp, chp_p["ramp_up"], "chp"),
            (prev_hp, P_hp, hp_p["ramp_up"], "hp"),
            (prev_ts_ch, Q_ts_ch, ts_p["ramp_up"], "ts_ch"),
            (prev_ts_dis, Q_ts_dis, ts_p["ramp_up"], "ts_dis"),
        ]:
            prob += (var_dict[0] - prev_val <= ramp, f"RampInitUp_{label}")
            prob += (prev_val - var_dict[0] <= ramp, f"RampInitDn_{label}")

        for t in range(T - 1):
            for prev_getter, var_dict, ramp_up, ramp_dn, label in [
                (lambda t=t: P_bat_ch[t], P_bat_ch, bat["ramp_up"], bat["ramp_down"], "bat_ch"),
                (lambda t=t: P_bat_dis[t], P_bat_dis, bat["ramp_up"], bat["ramp_down"], "bat_dis"),
                (lambda t=t: P_chp[t], P_chp, chp_p["ramp_up"], chp_p["ramp_down"], "chp"),
                (lambda t=t: P_hp[t], P_hp, hp_p["ramp_up"], hp_p["ramp_down"], "hp"),
                (lambda t=t: Q_ts_ch[t], Q_ts_ch, ts_p["ramp_up"], ts_p["ramp_down"], "ts_ch"),
                (lambda t=t: Q_ts_dis[t], Q_ts_dis, ts_p["ramp_up"], ts_p["ramp_down"], "ts_dis"),
            ]:
                prob += (var_dict[t + 1] - var_dict[t] <= ramp_up,
                         f"RampUp_{label}_{t}")
                prob += (var_dict[t] - var_dict[t + 1] <= ramp_dn,
                         f"RampDn_{label}_{t}")

        # ── SOC 递推 + 初始/终端值 ──
        prob += (SOC_bat[0] == soc_bat_0)
        prob += (SOC_ts[0] == soc_ts_0)
        for t in range(T - 1):
            prob += (SOC_bat[t + 1] == SOC_bat[t]
                     + (bat["eta_ch"] * P_bat_ch[t]
                        - P_bat_dis[t] / max(bat["eta_dis"], 0.01)) * dt
                     / bat["e_rated"])
            loss = ts_p["loss_rate"] * SOC_ts[t] * dt
            prob += (SOC_ts[t + 1] == SOC_ts[t]
                     + (ts_p["eta_ch"] * Q_ts_ch[t]
                        - Q_ts_dis[t] / max(ts_p["eta_dis"], 0.01)) * dt
                     / ts_p["e_rated"] - loss)
        prob += (SOC_bat[T - 1] >= soc_bat_0 * 0.5)
        prob += (SOC_ts[T - 1] >= soc_ts_0 * 0.3)

        # ── 目标函数 ──
        cost_elec = lpSum(
            (P_imp[t] * import_prices[t] - P_exp[t] * export_prices[t]) * dt
            for t in range(T))
        cost_gas = lpSum(
            P_chp[t] * dt / chp_p["eta_elec"] * gas_price
            for t in range(T))
        cost_wear = lpSum(
            (P_bat_ch[t] + P_bat_dis[t]) * dt * 0.05
            for t in range(T))
        prob += cost_elec + cost_gas + cost_wear

        vars_dict = {
            "P_pv": P_pv, "P_bat_ch": P_bat_ch, "P_bat_dis": P_bat_dis,
            "P_chp": P_chp, "P_hp": P_hp,
            "P_imp": P_imp, "P_exp": P_exp,
            "Q_ts_ch": Q_ts_ch, "Q_ts_dis": Q_ts_dis,
            "SOC_bat": SOC_bat, "SOC_ts": SOC_ts,
            "u_bat_ch": u_bat_ch, "u_bat_dis": u_bat_dis,
            "u_chp": u_chp, "u_ts_ch": u_ts_ch, "u_ts_dis": u_ts_dis,
        }
        return prob, vars_dict

    # ── 求解 ──────────────────────────────────────────────

    def solve(self, pv_forecast: list[float],
              load_forecast: list[float],
              heat_load_forecast: list[float] | None = None,
              soc_bat_0: float | None = None,
              soc_ts_0: float | None = None,
              ambient_temp: float = 10.0) -> list[dict] | None:
        """求解 MILP，返回 288 步调度方案。失败返回 None。"""
        if not HAS_PULP:
            print("[MILP] PuLP 未安装")
            return None

        T = self.params["_meta"]["num_steps"]
        step_min = self.params["_meta"]["step_minutes"]

        if heat_load_forecast is None:
            now = datetime.now()
            heat_load_forecast = [
                _estimate_heat_load(ambient_temp, (now + timedelta(minutes=t * step_min)).hour
                                    + (now + timedelta(minutes=t * step_min)).minute / 60)
                for t in range(T)]

        if soc_bat_0 is None:
            latest = collector.get_latest("battery_pcs_01")
            soc_bat_0 = latest.get("soc", 50.0) / 100.0
        if soc_ts_0 is None:
            latest = collector.get_latest("thermal_storage_01")
            soc_ts_0 = latest.get("heat_soc", 50.0) / 100.0
        bat_p = self.params["battery_pcs_01"]
        ts_p = self.params["thermal_storage_01"]
        soc_bat_0 = max(bat_p["soc_min"], min(bat_p["soc_max"], soc_bat_0))
        soc_ts_0 = max(ts_p["soc_min"], min(ts_p["soc_max"], soc_ts_0))

        try:
            prob, vars_dict = self._build_model(
                pv_forecast, load_forecast, heat_load_forecast,
                soc_bat_0, soc_ts_0, ambient_temp)

            timeout = getattr(settings, "milp_solver_timeout", 60)
            prob.solve(PULP_CBC_CMD(msg=False, timeLimit=timeout))

            status = LpStatus[prob.status]
            if status == "Infeasible":
                print("[MILP] 模型不可行，检查约束")
                return None
            if status not in ("Optimal", "Feasible"):
                print(f"[MILP] 求解状态: {status}")
                return None

            schedule = []
            for t in range(T):
                step = {
                    "step": t,
                    "minute": t * step_min,
                    "P_pv_kw": round(value(vars_dict["P_pv"][t]), 2),
                    "P_bat_ch_kw": round(value(vars_dict["P_bat_ch"][t]), 2),
                    "P_bat_dis_kw": round(value(vars_dict["P_bat_dis"][t]), 2),
                    "P_chp_kw": round(value(vars_dict["P_chp"][t]), 2),
                    "P_hp_kw": round(value(vars_dict["P_hp"][t]), 2),
                    "P_grid_import_kw": round(value(vars_dict["P_imp"][t]), 2),
                    "P_grid_export_kw": round(value(vars_dict["P_exp"][t]), 2),
                    "Q_ts_ch_kw": round(value(vars_dict["Q_ts_ch"][t]), 2),
                    "Q_ts_dis_kw": round(value(vars_dict["Q_ts_dis"][t]), 2),
                    "SOC_bat": round(value(vars_dict["SOC_bat"][t]), 4),
                    "SOC_ts": round(value(vars_dict["SOC_ts"][t]), 4),
                    "u_chp": int(round(value(vars_dict["u_chp"][t]))),
                }
                schedule.append(step)

            total_cost = value(prob.objective)
            self._last_schedule = schedule
            self._last_solve_status = status
            self._last_solve_cost = round(total_cost, 2)
            self._last_solve_ts = datetime.now().isoformat()

            # 记录第一步功率用于下次爬坡约束
            s0 = schedule[0]
            self._prev_power = {
                "battery_pcs_01_ch": s0["P_bat_ch_kw"],
                "battery_pcs_01_dis": s0["P_bat_dis_kw"],
                "chp_01": s0["P_chp_kw"],
                "heatpump_01": s0["P_hp_kw"],
                "thermal_storage_01_ch": s0["Q_ts_ch_kw"],
                "thermal_storage_01_dis": s0["Q_ts_dis_kw"],
            }

            print(f"[MILP] 求解完成: {status}, 总成本=¥{total_cost:.2f}, "
                  f"SOC_bat[0]={soc_bat_0:.2f}")
            return schedule

        except Exception as e:
            print(f"[MILP] 求解异常: {e}")
            self._last_solve_status = f"error: {e}"
            return None

    # ── 指令生成 (5min 粒度) ───────────────────────────────

    def extract_commands(self, schedule: list[dict] | None,
                         duration: int = 300,
                         num_steps: int = 1) -> list[dict]:
        """从调度方案中提取前 num_steps 步的设备控制指令 (默认 1 步 = 5min)。

        duration: 指令有效期 (秒)，默认 300 (5min)。
        threshold: 功率 > 1kW 才下发指令。
        """
        if schedule is None or len(schedule) == 0:
            return []

        commands: list[dict] = []
        threshold = 1.0

        for offset in range(min(num_steps, len(schedule))):
            s = schedule[offset]
            suffix = f"_{offset}" if num_steps > 1 else ""

            # 储能变流器
            bat_ch = s.get("P_bat_ch_kw", 0)
            bat_dis = s.get("P_bat_dis_kw", 0)
            if bat_dis > threshold:
                commands.append({
                    "device_id": "battery_pcs_01",
                    "command": {"register": 50, "values": [bat_dis],
                                "mode": 2, "duration": duration},
                })
            elif bat_ch > threshold:
                commands.append({
                    "device_id": "battery_pcs_01",
                    "command": {"register": 50, "values": [bat_ch],
                                "mode": 1, "duration": duration},
                })
            else:
                commands.append({
                    "device_id": "battery_pcs_01",
                    "command": {"register": 50, "values": [0],
                                "mode": 3, "duration": 0},
                })

            # CHP
            chp_pwr = s.get("P_chp_kw", 0)
            if s.get("u_chp", 0) == 1 and chp_pwr > threshold:
                commands.append({
                    "device_id": "chp_01",
                    "command": {"register": 50, "values": [chp_pwr],
                                "mode": 1, "duration": duration},
                })
            else:
                commands.append({
                    "device_id": "chp_01",
                    "command": {"register": 50, "values": [0],
                                "mode": 0, "duration": 0},
                })

            # 热泵
            hp_pwr = s.get("P_hp_kw", 0)
            if hp_pwr > threshold:
                commands.append({
                    "device_id": "heatpump_01",
                    "command": {"register": 50, "values": [hp_pwr],
                                "mode": 1, "duration": duration},
                })
            else:
                commands.append({
                    "device_id": "heatpump_01",
                    "command": {"register": 50, "values": [0],
                                "mode": 0, "duration": 0},
                })

            # 蓄能罐
            ts_ch = s.get("Q_ts_ch_kw", 0)
            ts_dis = s.get("Q_ts_dis_kw", 0)
            if ts_dis > threshold:
                commands.append({
                    "device_id": "thermal_storage_01",
                    "command": {"register": 50, "values": [ts_dis],
                                "mode": 2, "duration": duration},
                })
            elif ts_ch > threshold:
                commands.append({
                    "device_id": "thermal_storage_01",
                    "command": {"register": 50, "values": [ts_ch],
                                "mode": 1, "duration": duration},
                })
            else:
                commands.append({
                    "device_id": "thermal_storage_01",
                    "command": {"register": 50, "values": [0],
                                "mode": 0, "duration": 0},
                })

        return commands

    # ── 预报数据获取 ──────────────────────────────────────

    def get_forecast_data(self) -> tuple[list[float], list[float]]:
        """从 TDengine 获取最新 PV/负荷预测数据 (288 点)。"""
        T = self.params["_meta"]["num_steps"]
        step_min = self.params["_meta"]["step_minutes"]
        now = datetime.now()
        start_ms = int(now.timestamp() * 1000)
        end_ms = int((now + timedelta(hours=24)).timestamp() * 1000)

        pv_forecast = [0.0] * T
        load_forecast = [0.0] * T
        step_ms = step_min * 60 * 1000

        try:
            rows = td_manager.query(f"""
                SELECT ts, power_kw FROM {td_manager.db}.forecast_pv
                WHERE ts >= {start_ms} AND ts <= {end_ms} ORDER BY ts
            """)
            for row in rows:
                t_ms = row.get("ts", 0)
                step = int((t_ms - start_ms) / step_ms)
                if 0 <= step < T:
                    pv_forecast[step] = max(0.0, float(row.get("power_kw", 0)))
        except Exception as e:
            print(f"[MILP] PV 预测读取失败: {e}")

        try:
            rows = td_manager.query(f"""
                SELECT ts, power_kw FROM {td_manager.db}.forecast_load
                WHERE ts >= {start_ms} AND ts <= {end_ms} ORDER BY ts
            """)
            for row in rows:
                t_ms = row.get("ts", 0)
                step = int((t_ms - start_ms) / step_ms)
                if 0 <= step < T:
                    load_forecast[step] = max(0.0, float(row.get("power_kw", 0)))
        except Exception as e:
            print(f"[MILP] 负荷预测读取失败: {e}")

        if all(v < 0.1 for v in pv_forecast):
            pv_forecast = self._fallback_pv_forecast(now, T)
        if all(v < 0.1 for v in load_forecast):
            load_forecast = self._fallback_load_forecast(now, T)

        return pv_forecast, load_forecast

    def _fallback_pv_forecast(self, start: datetime, T: int) -> list[float]:
        step_min = self.params["_meta"]["step_minutes"]
        pv_rated = self.params["pv_inverter_01"]["p_max"]
        forecast = []
        for t in range(T):
            dt = start + timedelta(minutes=t * step_min)
            hour = dt.hour + dt.minute / 60
            if 6 <= hour <= 18:
                power = pv_rated * max(0, math.sin((hour - 6) / 12 * math.pi))
            else:
                power = 0.0
            forecast.append(round(power, 2))
        return forecast

    def _fallback_load_forecast(self, start: datetime, T: int) -> list[float]:
        step_min = self.params["_meta"]["step_minutes"]
        pattern = [15, 14, 13, 12, 12, 13, 18, 30, 40, 45, 48, 50,
                   52, 50, 48, 46, 45, 50, 55, 58, 55, 48, 35, 22]
        return [float(pattern[(t * step_min // 60) % 24]) for t in range(T)]

    # ── 事件驱动调度 ──────────────────────────────────────

    async def dispatch_once(self, trigger_reason: str = "scheduled") -> bool:
        """单次优化 + 指令下发。返回是否成功。"""
        if not HAS_PULP:
            return False

        try:
            pv_fc, load_fc = self.get_forecast_data()

            bat_data = collector.get_latest("battery_pcs_01")
            ts_data = collector.get_latest("thermal_storage_01")
            soc_bat = bat_data.get("soc", 50.0) / 100.0
            soc_ts = ts_data.get("heat_soc", 50.0) / 100.0

            schedule = self.solve(pv_fc, load_fc,
                                  soc_bat_0=soc_bat, soc_ts_0=soc_ts)
            if schedule is None:
                print(f"[MILP] 求解失败 (触发原因: {trigger_reason})")
                return False

            duration = self.params["_meta"]["dispatch_interval"]
            commands = self.extract_commands(schedule, duration=duration)

            for cmd in commands:
                device_id = cmd["device_id"]
                try:
                    result = await collector.write_device_command(
                        device_id, cmd["command"])
                    status = "OK" if result["success"] else f"FAIL: {result['message']}"
                except Exception as e:
                    status = f"ERR: {e}"
                if trigger_reason != "scheduled":
                    print(f"[MILP] {device_id} → {status}")

            if trigger_reason != "scheduled":
                print(f"[MILP] 事件驱动重优化完成 ({trigger_reason})")
            return True

        except Exception as e:
            print(f"[MILP] dispatch_once 异常: {e}")
            return False

    async def _event_monitor_loop(self):
        """事件监控协程：检测事件 → 触发即时重优化。每 10s 检测一次。"""
        print("[MILP] 事件监控已启动 (每 10s 检测)")
        while self._running:
            await asyncio.sleep(10)
            try:
                events = self.detector.detect(self._last_schedule)
                if events:
                    summary = ", ".join(events[:5])  # 最多显示 5 个
                    if len(events) > 5:
                        summary += f" (+{len(events) - 5})"
                    print(f"[MILP] 🔔 事件触发: {summary}")
                    await self.dispatch_once(trigger_reason=events[0])
            except Exception as e:
                print(f"[MILP] 事件监控异常: {e}")

    # ── 调度循环 ──────────────────────────────────────────

    async def run_loop(self):
        """MILP 主调度循环。"""
        self._running = True
        interval = getattr(settings, "milp_interval", 300)
        safety_first = getattr(settings, "milp_safety_first", True)
        print(f"[MILP] 启动优化引擎, 间隔 {interval}s (5min 分辨率, 288 步)")

        await asyncio.sleep(10)  # 冷启动等待

        # 规则引擎 → 安全模式
        if safety_first:
            from .rule_engine import rule_engine
            rule_engine.safety_only = True
            print("[MILP] 规则引擎 → 安全模式")

        # 启动事件监控协程
        event_task = asyncio.create_task(self._event_monitor_loop())

        while self._running:
            started = time.time()
            await self.dispatch_once()
            elapsed = time.time() - started
            wait = max(1, interval - elapsed)
            print(f"[MILP] 耗时 {elapsed:.1f}s, 等待 {wait:.0f}s")
            await asyncio.sleep(wait)

        event_task.cancel()
        if safety_first:
            from .rule_engine import rule_engine
            rule_engine.safety_only = False

    def stop(self):
        self._running = False
        try:
            from .rule_engine import rule_engine
            rule_engine.safety_only = False
        except Exception:
            pass

    # ── 状态查询 ──────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "status": self._last_solve_status,
            "total_cost_yuan": self._last_solve_cost,
            "last_solve_ts": self._last_solve_ts,
            "running": self._running,
            "has_schedule": self._last_schedule is not None,
            "step_minutes": self.params["_meta"]["step_minutes"],
            "num_steps": self.params["_meta"]["num_steps"],
        }

    def get_schedule(self) -> list[dict] | None:
        return self._last_schedule

    def get_price_config(self) -> dict:
        return self.prices

    def update_price_config(self, config: dict):
        self.prices = config
        with open(PRICE_FILE, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print("[MILP] 价格配置已更新")


milp_engine = MilpOptimizer()
