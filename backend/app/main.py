"""IES 控制终端后端服务"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router, broadcast, active_ws
from app.core.config import settings
from app.core.tdengine import td_manager
from app.services.collector import collector
from app.services.rule_engine import rule_engine
from app.services.forecast_pv import forecast_service
from app.services.alert_engine import alert_engine
from app.services.mqtt_collector import mqtt_collector
from app.services.milp_engine import milp_engine


# ── 生命周期管理 ──────────────────────────────────────────


async def ws_broadcast_loop():
    """定期将最新采集数据通过 WebSocket 广播到前端"""
    while True:
        await asyncio.sleep(settings.collect_interval)
        if not active_ws:
            continue
        latest = collector.get_latest()
        await broadcast({"type": "realtime", "data": latest})


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    print("[App] 连接 TDengine...")
    td_manager.connect()
    print("[App] 启动采集器...")
    rules_task = asyncio.create_task(collector.run_loop())
    print("[App] 启动 WebSocket 广播...")
    broadcast_task = asyncio.create_task(ws_broadcast_loop())
    print("[App] 启动规则引擎...")
    engine_task = asyncio.create_task(rule_engine.run_loop())
    print("[App] 启动预测服务...")
    forecast_task = asyncio.create_task(forecast_service.run_loop())
    print("[App] 启动告警引擎...")
    alert_task = asyncio.create_task(alert_engine.run_loop())
    print("[App] 启动 MQTT 采集器...")
    mqtt_collector.start()
    print("[App] 启动 MILP 优化引擎...")
    milp_task = asyncio.create_task(milp_engine.run_loop())
    yield
    # shutdown
    milp_engine.stop()
    mqtt_collector.stop()
    rule_engine.stop()
    alert_engine.stop()
    rules_task.cancel()
    broadcast_task.cancel()
    engine_task.cancel()
    forecast_task.cancel()
    alert_task.cancel()
    milp_task.cancel()
    await collector.close()
    td_manager.close()
    print("[App] 已关闭")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
