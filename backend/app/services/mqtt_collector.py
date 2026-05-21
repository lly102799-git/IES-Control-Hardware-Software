"""
MQTT 数据采集适配器。
订阅 MQTT topic，解析 JSON 遥测 → 统一格式 → 写入 TDengine。
与 ModbusCollector 并行运行，共享 _latest 缓存。
"""

import asyncio
import json
import time
from datetime import datetime
from threading import Thread

import os

import paho.mqtt.client as mqtt

from ..core.config import settings
from ..core.tdengine import td_manager
from .collector import collector

BROKER = os.environ.get("MQTT_BROKER", "localhost")
PORT = 1883
TOPIC = "ies/device/+/telemetry"

# MQTT 设备元数据
MQTT_DEVICE_META = {
    "smart_meter_01": {"name": "智能电表 #1", "dev_type": "smart_meter", "rated_power_w": 30000},
    "env_sensor_01": {"name": "环境传感器 #1", "dev_type": "env_sensor", "rated_power_w": 0},
    "pipe_sensor_01": {"name": "管道传感器 #1", "dev_type": "pipe_sensor", "rated_power_w": 0},
}

# 遥测字段映射: device_id → { payload_key → point_name }
FIELD_MAP: dict[str, dict[str, str]] = {
    "smart_meter_01": {
        "active_power_w": "active_power",
        "voltage_v": "voltage",
        "current_a": "current",
        "power_factor": "power_factor",
        "frequency_hz": "frequency",
        "total_energy_kwh": "total_energy",
    },
    "env_sensor_01": {
        "temperature_c": "temperature",
        "humidity_pct": "humidity",
        "co2_ppm": "co2",
        "pm25_ugm3": "pm25",
    },
    "pipe_sensor_01": {
        "temp_supply_c": "temp_supply",
        "temp_return_c": "temp_return",
        "flow_rate_m3h": "flow_rate",
    },
}


class MqttCollector:
    def __init__(self):
        self.client: mqtt.Client | None = None
        self._running = False
        self._devices = set()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"[MQTT] 已连接 broker, rc={reason_code}")
        client.subscribe(TOPIC, qos=1)
        print(f"[MQTT] 订阅: {TOPIC}")

    def on_message(self, client, userdata, msg):
        try:
            # 解析 topic: ies/device/{device_id}/telemetry
            parts = msg.topic.split("/")
            if len(parts) < 4:
                return
            device_id = parts[2]
            if device_id not in FIELD_MAP:
                return

            payload = json.loads(msg.payload)
            ts = int(time.time() * 1000)
            records = []
            data_cache: dict[str, float] = {}

            field_map = FIELD_MAP[device_id]
            for payload_key, point_name in field_map.items():
                raw_val = payload.get(payload_key)
                if raw_val is None:
                    continue
                try:
                    val = float(raw_val)
                except (ValueError, TypeError):
                    continue
                data_cache[point_name] = round(val, 3)
                records.append({
                    "device_id": device_id,
                    "point_name": point_name,
                    "val": round(val, 3),
                    "quality": 0,
                    "ts": ts,
                })

            # 写入 TDengine + 更新内存缓存
            if records:
                td_manager.write_telemetry(records)
                collector._latest[device_id] = data_cache
                self._devices.add(device_id)

        except Exception as e:
            print(f"[MQTT] 消息处理异常: {e}")

    def start(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect(BROKER, PORT, 60)
        self.client.loop_start()
        self._running = True
        print(f"[MQTT Collector] 已启动, broker={BROKER}:{PORT}")

    def stop(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        self._running = False

    def get_devices(self) -> list[dict]:
        return [
            {"device_id": did, "name": MQTT_DEVICE_META[did]["name"],
             "dev_type": MQTT_DEVICE_META[did]["dev_type"],
             "rated_power_w": MQTT_DEVICE_META[did]["rated_power_w"],
             "status": "online" if did in self._devices else "offline"}
            for did in MQTT_DEVICE_META
        ]


mqtt_collector = MqttCollector()
