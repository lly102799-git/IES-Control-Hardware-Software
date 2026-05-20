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
    """向设备下发控制指令"""
    result = await collector.write_device_command(
        device_id,
        {"register": cmd.addr, "values": cmd.values, "mode": cmd.mode, "duration": cmd.duration},
    )
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
