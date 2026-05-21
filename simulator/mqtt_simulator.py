"""
MQTT 设备模拟器 —— 模拟物联网传感器和智能仪表。
设备通过 MQTT broker (localhost:1883) 发布 JSON 遥测数据。

设备列表：
- smart_meter_01: 智能电表 (电能、功率、电压、电流)
- env_sensor_01: 环境传感器 (温湿度、CO₂、PM2.5)
- pipe_sensor_01: 管道传感器 (供/回水温度、流量)
"""

import asyncio
import json
import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime

os.environ.setdefault("TZ_OFFSET_HOURS", "8")

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("pip install paho-mqtt")
    raise

BROKER = os.environ.get("MQTT_BROKER", "localhost")
PORT = 1883

def local_hour(t: float) -> float:
    tz = int(os.environ.get("TZ_OFFSET_HOURS", "8"))
    return ((t + tz * 3600) % 86400) / 3600


# ── 智能电表 ──────────────────────────────────────────────

@dataclass
class SmartMeter:
    total_energy: float = 0.0       # kWh 累计
    active_power: float = 0.0       # W
    voltage: float = 230.0          # V
    current: float = 0.0            # A
    power_factor: float = 0.95      # cos φ
    frequency: float = 50.0         # Hz
    base_load: float = 5000.0       # W 基础负荷

    def update(self, t: float):
        hour = local_hour(t)
        # 办公楼负荷模式
        if 8 <= hour < 18:
            load = self.base_load * (1.2 + 0.3 * math.sin((hour - 8) / 10 * math.pi))
        elif 18 <= hour < 22:
            load = self.base_load * 0.6
        else:
            load = self.base_load * 0.3
        self.active_power = round(load + random.gauss(0, 200), 1)
        self.voltage = round(230 + random.gauss(0, 1.5), 1)
        self.current = round(self.active_power / max(self.voltage, 1), 2)
        self.power_factor = round(0.92 + random.gauss(0, 0.01), 2)
        self.total_energy += self.active_power * 5 / 3600 / 1000  # kWh per 5s

    def to_payload(self):
        return {
            "total_energy_kwh": round(self.total_energy, 3),
            "active_power_w": self.active_power,
            "voltage_v": self.voltage,
            "current_a": self.current,
            "power_factor": self.power_factor,
            "frequency_hz": round(self.frequency + random.gauss(0, 0.02), 2),
        }


# ── 环境传感器 ────────────────────────────────────────────

@dataclass
class EnvSensor:
    temperature: float = 22.0       # °C
    humidity: float = 55.0          # %
    co2: float = 450.0              # ppm
    pm25: float = 20.0              # μg/m³

    def update(self, t: float):
        hour = local_hour(t)
        outdoor = 15 + 10 * math.sin((hour - 3) / 24 * 2 * math.pi)
        self.temperature = round(22 + 3 * math.sin((hour - 8) / 10 * math.pi) + random.gauss(0, 0.3), 1)
        self.humidity = round(55 - 5 * math.sin((hour - 6) / 14 * math.pi) + random.gauss(0, 1), 1)
        self.co2 = round(420 + 200 * max(0, math.sin((hour - 8) / 10 * math.pi)) + random.gauss(0, 20), 1)
        self.pm25 = round(15 + 15 * max(0, math.sin((hour - 7) / 12 * math.pi)) + random.gauss(0, 3), 1)

    def to_payload(self):
        return {
            "temperature_c": self.temperature,
            "humidity_pct": self.humidity,
            "co2_ppm": self.co2,
            "pm25_ugm3": self.pm25,
        }


# ── 管道传感器 (供冷/热) ─────────────────────────────────

@dataclass
class PipeSensor:
    temp_supply: float = 7.0        # °C 供水温度
    temp_return: float = 12.0       # °C 回水温度
    flow_rate: float = 15.0         # m³/h

    def update(self, t: float):
        hour = local_hour(t)
        winter = datetime.now().month in [11, 12, 1, 2, 3]
        if winter:
            self.temp_supply = round(45 + random.gauss(0, 0.5), 1)
            self.temp_return = round(35 + random.gauss(0, 0.4), 1)
            self.flow_rate = round(12 + 6 * max(0, math.sin((hour - 6) / 13 * math.pi)) + random.gauss(0, 0.3), 1)
        else:
            self.temp_supply = round(7 + random.gauss(0, 0.3), 1)
            self.temp_return = round(12 + random.gauss(0, 0.4), 1)
            self.flow_rate = round(10 + 8 * max(0, math.sin((hour - 9) / 11 * math.pi)) + random.gauss(0, 0.3), 1)

    def to_payload(self):
        return {
            "temp_supply_c": self.temp_supply,
            "temp_return_c": self.temp_return,
            "flow_rate_m3h": self.flow_rate,
        }


# ── 主程序 ────────────────────────────────────────────────

DEVICES = {
    "smart_meter_01": SmartMeter(),
    "env_sensor_01": EnvSensor(),
    "pipe_sensor_01": PipeSensor(),
}


async def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(BROKER, PORT, 60)
    client.loop_start()

    print("=== MQTT 设备模拟器 ===")
    print(f"Broker: {BROKER}:{PORT}")
    for dev_id in DEVICES:
        print(f"  {dev_id} → ies/device/{dev_id}/telemetry")
    print("=" * 40)

    while True:
        t = time.time()
        for dev_id, dev in DEVICES.items():
            dev.update(t)  # type: ignore[arg-type]
            topic = f"ies/device/{dev_id}/telemetry"
            payload = json.dumps(dev.to_payload())
            client.publish(topic, payload, qos=1)
        now = datetime.now().strftime("%H:%M:%S")
        meter = DEVICES["smart_meter_01"]
        print(f"[{now}] MQTT publish: {len(DEVICES)} devices | 电表:{meter.active_power:.0f}W")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
