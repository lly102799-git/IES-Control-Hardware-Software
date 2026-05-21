"""REST API 路由"""

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..core.tdengine import td_manager
from ..services.collector import collector

router = APIRouter(prefix="/api")


# ── 请求模型 ──────────────────────────────────────────────


class DeviceCommand(BaseModel):
    addr: int = 50              # 控制寄存器起始地址 (Modbus)
    values: list[float] = []    # 寄存器值（1个=16bit, 2个=32bit）
    mode: int = 0               # 控制模式: 0=自动, 1=强制充电, 2=强制放电, 3=待机
    duration: int = 0           # 生效时长（秒），0=永久有效


# ── WebSocket 连接管理 ────────────────────────────────────

active_ws: set[WebSocket] = set()


async def broadcast(data: dict):
    """向所有连接的 WebSocket 客户端推送数据"""
    dead: set[WebSocket] = set()
    payload = json.dumps(data, default=str)

    async def send_one(ws: WebSocket):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)

    await asyncio.gather(*(send_one(ws) for ws in active_ws))
    active_ws.difference_update(dead)


# ── API 端点 ──────────────────────────────────────────────


@router.get("/devices")
async def get_devices():
    """返回所有设备列表（Modbus + MQTT）"""
    from ..services.mqtt_collector import mqtt_collector as mc  # noqa: E402
    modbus = [
        {"device_id": "pv_inverter_01", "name": "光伏逆变器 #1",
         "dev_type": "pv_inverter", "rated_power_w": 100000, "status": "online"},
        {"device_id": "battery_pcs_01", "name": "储能变流器 #1",
         "dev_type": "battery_pcs", "rated_power_w": 50000, "capacity_kwh": 200, "status": "online"},
        {"device_id": "chp_01", "name": "CHP 三联供 #1",
         "dev_type": "chp", "rated_power_w": 50000, "status": "online"},
        {"device_id": "heatpump_01", "name": "热泵 #1",
         "dev_type": "heatpump", "rated_power_w": 30000, "status": "online"},
        {"device_id": "thermal_storage_01", "name": "蓄能罐 #1",
         "dev_type": "thermal_storage", "rated_power_w": 40000, "capacity_kwh": 500, "status": "online"},
    ]
    mqtt = mc.get_devices()
    return modbus + mqtt


@router.get("/devices/{device_id}/realtime")
async def get_device_realtime(device_id: str):
    """获取设备最新实时数据（内存缓存）"""
    data = collector.get_latest(device_id)
    return {"device_id": device_id, "data": data, "ts": datetime.now().isoformat()}


@router.get("/devices/{device_id}/history")
async def get_device_history(
    device_id: str, point_name: str = "active_power", start: str = "", end: str = ""
):
    """查询历史遥测数据"""
    if not start:
        start_ts = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    else:
        start_ts = start

    if not end:
        end_ts = datetime.now().isoformat()
    else:
        end_ts = end

    sql = f"""
        SELECT ts, val, quality
        FROM {device_id}_{point_name}
        WHERE ts >= '{start_ts}' AND ts <= '{end_ts}'
        ORDER BY ts
        LIMIT 2000
    """
    try:
        rows = td_manager.query(sql)
        return {
            "device_id": device_id,
            "point_name": point_name,
            "data": rows,
        }
    except Exception as e:
        # 表可能还没有数据
        return {"device_id": device_id, "point_name": point_name, "data": [], "error": str(e)}


@router.get("/system/status")
async def get_system_status():
    """系统整体运行状态"""
    td_ok = await td_manager.health_check()
    return {
        "tdengine": "ok" if td_ok else "error",
        "collector_running": collector._running,
        "devices_online": len(collector._latest),
    }


# ── 控制指令 ──────────────────────────────────────────────


@router.post("/devices/{device_id}/command")
async def send_command(device_id: str, cmd: DeviceCommand):
    """向设备下发控制指令。
    手动指令 (mode != 0) 会注册为 MILP 手动覆盖，优先级高于自动优化。
    自动模式 (mode == 0) 会清除覆盖，恢复 MILP 控制。
    """
    result = await collector.write_device_command(
        device_id,
        {"register": cmd.addr, "values": cmd.values, "mode": cmd.mode, "duration": cmd.duration},
    )
    # 注册/清除 MILP 手动覆盖
    from ..services.milp_engine import milp_engine
    if cmd.mode == 0:
        milp_engine.clear_manual_override(device_id)
    elif result.get("success"):
        milp_engine.set_manual_override(device_id, cmd.duration)
    return result


@router.get("/devices/{device_id}/command/status")
async def get_command_status(device_id: str):
    """查询设备最近指令的执行状态"""
    return await collector.read_cmd_status(device_id)


@router.get("/devices/{device_id}/commands")
async def get_command_history(device_id: str, limit: int = 20):
    """查询设备指令历史记录"""
    try:
        rows = td_manager.query(f"""
            SELECT ts, cmd_register, cmd_values, cmd_mode, cmd_duration, success, message
            FROM {td_manager.db}.command_log
            WHERE device_id = '{device_id}'
            ORDER BY ts DESC
            LIMIT {limit}
        """)
        return {"device_id": device_id, "commands": rows}
    except Exception as e:
        return {"device_id": device_id, "commands": [], "error": str(e)}


# ── 规则引擎 ──────────────────────────────────────────────

from ..services.rule_engine import rule_engine  # noqa: E402


@router.get("/rules")
async def get_rules():
    """获取当前规则列表"""
    return {"rules": rule_engine.get_rules()}


@router.post("/rules/reload")
async def reload_rules():
    """重新加载规则文件（热更新）"""
    rule_engine.load_rules()
    return {"ok": True, "count": len(rule_engine.rules)}


@router.get("/rules/executions")
async def get_rule_executions(limit: int = 50):
    """查询规则执行历史"""
    try:
        rows = td_manager.query(f"""
            SELECT ts, rule_name, device_id, action_mode, action_power,
                   condition_snapshot, executed, reason
            FROM {td_manager.db}.rule_exec_log
            ORDER BY ts DESC
            LIMIT {limit}
        """)
        return {"executions": rows}
    except Exception as e:
        return {"executions": [], "error": str(e)}


# ── 预测数据 ──────────────────────────────────────────────

from ..services.forecast_pv import forecast_service  # noqa: E402


@router.get("/forecast/pv")
async def get_pv_forecast(hours: int = 24):
    """获取光伏预测数据"""
    try:
        rows = td_manager.query(f"""
            SELECT ts, power_kw, ghi
            FROM {td_manager.db}.forecast_pv
            WHERE ts >= NOW
            ORDER BY ts
            LIMIT {hours * 4}
        """)
        return {"forecast": rows}
    except Exception as e:
        return {"forecast": [], "error": str(e)}


@router.get("/forecast/load")
async def get_load_forecast(hours: int = 24):
    """获取负荷预测数据"""
    try:
        rows = td_manager.query(f"""
            SELECT ts, power_kw
            FROM {td_manager.db}.forecast_load
            WHERE ts >= NOW
            ORDER BY ts
            LIMIT {hours * 4}
        """)
        return {"forecast": rows}
    except Exception as e:
        return {"forecast": [], "error": str(e)}


@router.post("/forecast/trigger")
async def trigger_forecast():
    """手动触发预测计算"""
    result = forecast_service.trigger_now()
    return result


# ── 配置管理 ────────────────────────────────────────────

from pydantic import BaseModel as PbModel  # noqa: E402


class ConfigPayload(PbModel):
    items: list[dict]


@router.get("/config/rules")
async def get_config_rules():
    """获取规则引擎当前配置"""
    return {"items": rule_engine.get_rules()}


@router.put("/config/rules")
async def save_config_rules(payload: ConfigPayload):
    """保存规则配置到文件并热加载"""
    try:
        rule_engine.save_rules(payload.items)
        return {"ok": True, "count": len(payload.items)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/config/alerts")
async def get_config_alerts():
    """获取告警引擎当前配置"""
    return {"items": alert_engine.get_rules()}


@router.put("/config/alerts")
async def save_config_alerts(payload: ConfigPayload):
    """保存告警配置到文件并热加载"""
    try:
        alert_engine.save_rules(payload.items)
        return {"ok": True, "count": len(payload.items)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 设备点表配置 ────────────────────────────────────────

from ..services import point_table  # noqa: E402


@router.get("/config/point-tables")
async def get_point_tables():
    """获取所有设备的点表配置"""
    return {"devices": point_table.get_all()}


@router.put("/config/point-tables")
async def save_point_tables(payload: ConfigPayload):
    """保存点表配置到文件"""
    try:
        tables = {}
        for item in payload.items:
            tables[item["device_id"]] = {
                "slave_id": item["slave_id"],
                "read_start": item["read_start"],
                "read_count": item["read_count"],
                "points": item["points"],
            }
        point_table.save(tables)
        return {"ok": True, "count": len(tables)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 数据报表 ────────────────────────────────────────────

from fastapi.responses import Response  # noqa: E402
from io import StringIO  # noqa: E402
import csv  # noqa: E402

from ..services import point_table as pt  # noqa: E402


@router.get("/report/export")
async def export_csv(device_id: str, start: str, end: str):
    """导出设备全量遥测数据为多列 CSV（最长 31 天）"""
    # 限制时间范围
    from datetime import datetime, timedelta, timezone
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        if (e - s).days > 31:
            start = (e - timedelta(days=31)).isoformat()[:10]
    except Exception:
        pass

    # 获取设备所有测点名称
    dev_cfg = pt.get_device(device_id)
    point_names = [p["name"] for p in dev_cfg["points"]] if dev_cfg else ["active_power"]

    # 收集每个测点的时序数据，用毫秒时间戳对齐
    series: dict[str, dict[int, float]] = {}
    all_ts: set[int] = set()
    for pn in point_names:
        series[pn] = {}
        try:
            rows = td_manager.query(f"""
                SELECT ts, val FROM {device_id}_{pn}
                WHERE ts >= '{start}' AND ts <= '{end}'
                ORDER BY ts LIMIT 50000
            """)
            for r in rows:
                ts_val = r.get("ts", "")
                if not ts_val: continue
                try:
                    ts_ms = int(datetime.fromisoformat(str(ts_val).replace("Z", "+00:00")).timestamp() * 1000)
                except Exception:
                    continue
                val = round(float(r.get("val", 0)), 2)
                series[pn][ts_ms] = val
                all_ts.add(ts_ms)
        except Exception:
            pass

    # 按时间排序写入 CSV
    sorted_ts = sorted(all_ts)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp"] + point_names)
    for ts_ms in sorted_ts:
        ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
        row = [ts_str] + [series[pn].get(ts_ms, 0) for pn in point_names]
        writer.writerow(row)

    csv_content = output.getvalue()
    filename = f"{device_id}_{start[:10]}_{end[:10]}.csv"
    return Response(
        content=csv_content.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/report/daily")
async def daily_report(device_id: str, date: str, point_name: str = "active_power"):
    """设备日报：当日汇总统计"""
    try:
        rows = td_manager.query(f"""
            SELECT
                MIN(val) as min_val, MAX(val) as max_val, AVG(val) as avg_val,
                SUM(val) as total, COUNT(*) as cnt
            FROM {device_id}_{point_name}
            WHERE ts >= '{date} 00:00:00' AND ts <= '{date} 23:59:59'
        """)
        stats = rows[0] if rows else {}
        result = {
            "device_id": device_id, "date": date, "point_name": point_name,
            "min": round(stats.get("min_val", 0) or 0, 1),
            "max": round(stats.get("max_val", 0) or 0, 1),
            "avg": round(stats.get("avg_val", 0) or 0, 1),
            "data_points": stats.get("cnt", 0),
        }
        if point_name == "active_power":
            result["energy_kwh"] = round((stats.get("avg_val", 0) or 0) * 24 / 1000, 2)
        return result
    except Exception as e:
        return {"error": str(e)}


@router.get("/report/monthly")
async def monthly_report(device_id: str, year: int, month: int, point_name: str = "active_power"):
    """设备月报：逐日统计 (min/max/avg)"""
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year+1}-01-01"
    else:
        end = f"{year}-{month+1:02d}-01"

    daily = []
    try:
        # TDengine GROUP BY 兼容性问题，使用逐日查询
        from datetime import datetime as dt, timedelta
        d = dt.fromisoformat(start)
        while d.strftime("%Y-%m") == f"{year}-{month:02d}":
            day_str = d.strftime("%Y-%m-%d")
            rows = td_manager.query(f"""
                SELECT MIN(val) as min_val, MAX(val) as max_val, AVG(val) as avg_val, COUNT(*) as cnt
                FROM {device_id}_{point_name}
                WHERE ts >= '{day_str} 00:00:00' AND ts <= '{day_str} 23:59:59'
            """)
            r = rows[0] if rows else {}
            cnt = int(r.get("cnt", 0) or 0)
            if cnt > 0:
                daily.append({
                    "date": day_str,
                    "min": round(float(r.get("min_val", 0) or 0), 1),
                    "max": round(float(r.get("max_val", 0) or 0), 1),
                    "avg": round(float(r.get("avg_val", 0) or 0), 1),
                    "cnt": cnt,
                })
            d += timedelta(days=1)
    except Exception as e:
        return {"device_id": device_id, "point_name": point_name, "daily": [], "error": str(e)}

    return {"device_id": device_id, "year": year, "month": month, "point_name": point_name, "daily": daily}


# ── 驾驶舱 ──────────────────────────────────────────────


@router.get("/dashboard/overview")
async def get_dashboard_overview():
    """驾驶舱概览数据聚合——含电/热/冷多能流"""
    latest = collector.get_latest()
    pv = latest.get("pv_inverter_01", {})
    bat = latest.get("battery_pcs_01", {})
    chp = latest.get("chp_01", {})
    hp = latest.get("heatpump_01", {})
    ts = latest.get("thermal_storage_01", {})

    pv_power = pv.get("active_power", 0)
    bat_power = bat.get("active_power", 0)          # 正=放电, 负=充电
    chp_elec = chp.get("active_power", 0)
    chp_heat = chp.get("heat_power", 0)
    hp_elec = hp.get("elec_power", 0)
    hp_thermal = hp.get("thermal_power", 0)
    hp_mode = hp.get("mode", 0)
    ts_power = ts.get("power", 0)                    # 正=蓄热/放冷, 负=放热/蓄冷
    ts_mode = ts.get("mode", 0)

    soc = bat.get("soc", 50)

    # 电功率平衡
    total_gen = pv_power + chp_elec
    elec_load = total_gen + bat_power - hp_elec
    elec_load = max(0, elec_load)

    # 能量流 (桑基图数据) — 按实际能源流向动态生成
    flows = []

    # 电力流向
    if pv_power > 100:
        pv_to_load = min(pv_power, elec_load)
        flows.append({"source": "光伏", "target": "电负荷", "value": round(pv_to_load / 1000, 1)})
        if bat_power < -100:  # 充电
            flows.append({"source": "光伏", "target": "储能", "value": round(min(-bat_power, pv_power - pv_to_load) / 1000, 1)})

    if chp_elec > 100:
        flows.append({"source": "CHP电", "target": "电负荷", "value": round(chp_elec / 1000, 1)})

    if bat_power > 100:  # 放电
        flows.append({"source": "储能", "target": "电负荷", "value": round(bat_power / 1000, 1)})

    # 电网补足
    grid_power = max(0, elec_load - pv_power - chp_elec - max(bat_power, 0))
    if grid_power > 100:
        flows.append({"source": "电网", "target": "电负荷", "value": round(grid_power / 1000, 1)})

    # 电→热泵→热/冷
    if hp_elec > 100:
        flows.append({"source": "电网", "target": "热泵", "value": round(hp_elec / 1000, 1)})
        target = "热负荷" if hp_mode == 1 else "冷负荷"
        flows.append({"source": "热泵", "target": target, "value": round(hp_thermal / 1000, 1)})

    # CHP 余热
    if chp_heat > 100:
        flows.append({"source": "CHP热", "target": "热负荷", "value": round(chp_heat / 1000, 1)})

    # 蓄能罐
    if ts_mode in (1, 3) and ts_power > 100:  # 蓄能
        flows.append({"source": "CHP热", "target": "蓄能", "value": round(ts_power / 1000, 1)})
    elif ts_mode in (2, 4) and abs(ts_power) > 100:  # 放能
        target = "热负荷" if ts_mode == 2 else "冷负荷"
        flows.append({"source": "蓄能", "target": target, "value": round(abs(ts_power) / 1000, 1)})

    # 自发自用率
    self_use_rate = round(min(pv_power, elec_load) / max(pv_power, 1) * 100, 1) if pv_power > 0 else 0

    return {
        "kpi": {
            "pv_power_kw": round(pv_power / 1000, 1),
            "bat_power_kw": round(bat_power / 1000, 1),
            "soc": round(soc, 1),
            "self_use_rate": self_use_rate,
            "chp_power_kw": round(chp_elec / 1000, 1),
            "chp_heat_kw": round(chp_heat / 1000, 1),
            "hp_power_kw": round(hp_elec / 1000, 1),
            "hp_thermal_kw": round(hp_thermal / 1000, 1),
            "ts_power_kw": round(ts_power / 1000, 1),
            "ts_heat_soc": round(ts.get("heat_soc", 0), 1),
            "ts_cool_soc": round(ts.get("cool_soc", 0), 1),
        },
        "energy_flow": flows,
        "devices": [
            {"device_id": "pv_inverter_01", "name": "光伏逆变器", "power_kw": round(pv_power / 1000, 1), "status": int(pv.get("status", 0))},
            {"device_id": "battery_pcs_01", "name": "储能变流器", "power_kw": round(bat_power / 1000, 1), "status": int(bat.get("status", 0))},
            {"device_id": "chp_01", "name": "CHP 三联供", "power_kw": round(chp_elec / 1000, 1), "status": int(chp.get("status", 0))},
            {"device_id": "heatpump_01", "name": "热泵", "power_kw": round(hp_elec / 1000, 1), "status": int(hp.get("mode", 0))},
            {"device_id": "thermal_storage_01", "name": "蓄能罐", "power_kw": round(ts_power / 1000, 1), "status": int(ts.get("mode", 0))},
        ],
    }


# ── 告警 ──────────────────────────────────────────────────

from ..services.alert_engine import alert_engine  # noqa: E402


@router.get("/alerts")
async def get_alerts(severity: str = "", device_id: str = "",
                     status: str = "", limit: int = 100):
    """查询告警列表（可按级别/设备/状态筛选）"""
    return {"alerts": alert_engine.query(severity, device_id, status, limit)}


@router.post("/alerts/{alert_ts}/ack")
async def ack_alert(alert_ts: str, device_id: str = ""):
    """确认告警"""
    ok = alert_engine.acknowledge(alert_ts, device_id)
    return {"ok": ok}


@router.get("/alerts/stats")
async def get_alert_stats():
    """告警统计（各状态数量）"""
    try:
        active = len(alert_engine.query(status="active", limit=500))
        acked = len(alert_engine.query(status="acked", limit=500))
        resolved = len(alert_engine.query(status="resolved", limit=500))
        return {"active": active, "acked": acked, "resolved": resolved}
    except Exception:
        return {"active": 0, "acked": 0, "resolved": 0}


# ── MILP 优化引擎 ──────────────────────────────────────────

from ..services.milp_engine import milp_engine  # noqa: E402


@router.get("/milp/status")
async def get_milp_status():
    """MILP 引擎运行状态"""
    return milp_engine.get_status()


@router.get("/milp/schedule")
async def get_milp_schedule():
    """当前最优调度方案 (96 步)"""
    schedule = milp_engine.get_schedule()
    return {"schedule": schedule, "count": len(schedule) if schedule else 0}


@router.post("/milp/trigger")
async def trigger_milp():
    """手动触发一次优化"""
    success = await milp_engine.dispatch_once()
    return {"success": success, "status": milp_engine.get_status()}


@router.get("/milp/config")
async def get_milp_config():
    """获取价格配置"""
    return milp_engine.get_price_config()


class MilpPriceConfig(BaseModel):
    electricity: dict
    gas_price: float = 0.35


@router.post("/milp/config")
async def update_milp_config(config: MilpPriceConfig):
    """更新价格配置（热加载）"""
    milp_engine.update_price_config(config.model_dump())
    return {"ok": True}


@router.get("/milp/overrides")
async def get_milp_overrides():
    """当前活跃的手动覆盖设备及剩余时间"""
    return milp_engine.get_manual_overrides()


@router.get("/milp/execution-log")
async def get_execution_log(minutes: int = 40):
    """MILP 执行日志: 过去 N 分钟的 setpoint + 实际遥测。
    用于前端绘制「过去执行 vs 未来计划」对比图。
    """
    return milp_engine.get_execution_log(minutes)


# ── WebSocket 端点 ────────────────────────────────────────


@router.websocket("/ws/realtime")
async def ws_realtime(ws: WebSocket):
    await ws.accept()
    active_ws.add(ws)
    try:
        while True:
            # 保持连接，静默等待
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        active_ws.discard(ws)
