"""
Modbus 数据采集服务。
周期性轮询模拟设备，解析遥测值，写入 TDengine。

寄存器地址为 0-based（pymodbus 3.7.x 约定）。
"""

import asyncio
import struct
import time
from datetime import datetime

from pymodbus.client import AsyncModbusTcpClient

from ..core.config import settings
from ..core.tdengine import td_manager

# ── 设备点表定义 (0-based) ─────────────────────────────────

PV_POINTS = [
    ("dc_voltage",     0, 10.0),
    ("dc_current",     1, 10.0),
    ("ac_voltage_a",   2, 10.0),
    ("ac_current_a",   3, 10.0),
    ("active_power",   4, 1.0),     # 32bit 地址 4-5
    ("reactive_power", 6, 1.0),     # 32bit 地址 6-7
    ("pf",             8, 100.0),
    ("frequency",      9, 10.0),
    ("daily_energy",   10, 10.0),
    ("total_energy",   11, 1.0),    # 32bit 地址 11-12
    ("temp_module",    13, 10.0),
    ("temp_cabinet",   14, 10.0),
    ("status",         15, 1.0),
]

BATTERY_POINTS = [
    ("dc_voltage",     0, 10.0),
    ("dc_current",     1, 10.0),
    ("ac_voltage_a",   2, 10.0),
    ("ac_current_a",   3, 10.0),
    ("active_power",   4, 1.0),     # 32bit 地址 4-5
    ("reactive_power", 6, 1.0),     # 32bit 地址 6-7
    ("soc",            8, 10.0),
    ("soh",            9, 10.0),
    ("temp_battery",   10, 10.0),
    ("temp_cabinet",   11, 10.0),
    ("status",         12, 1.0),
]

PV_32BIT_ADDRS = {4, 6, 11}
BATTERY_32BIT_ADDRS = {4, 6}

# CHP (slave=3): 10 regs
CHP_POINTS = [
    ("active_power",   0, 1.0, True),   # 32bit
    ("heat_power",     2, 1.0, True),   # 32bit
    ("gas_flow",       4, 10.0),
    ("elec_efficiency", 5, 100.0),
    ("total_efficiency", 6, 100.0),
    ("temp_out",       7, 10.0),
    ("temp_in",        8, 10.0),
    ("status",         9, 1.0),
]
CHP_32BIT = {0, 2}

# Heat Pump (slave=4): 8 regs
HP_POINTS = [
    ("elec_power",     0, 1.0, True),   # 32bit
    ("thermal_power",  2, 1.0, True),   # 32bit
    ("cop",            4, 10.0),
    ("temp_out",       5, 10.0),
    ("temp_in",        6, 10.0),
    ("mode",           7, 1.0),
]
HP_32BIT = {0, 2}

# Thermal Storage (slave=5): 8 regs
TS_POINTS = [
    ("heat_stored",    0, 1000.0, True),  # 32bit, reg=Wh, val=kWh
    ("power",          2, 1.0, True),    # 32bit
    ("heat_soc",       4, 10.0),
    ("cool_soc",       5, 10.0),
    ("tank_temp",      6, 10.0),
    ("mode",           7, 1.0),
]
TS_32BIT = {0, 2}


def read_reg32(regs: list[int], addr: int) -> int:
    """读取 32bit 有符号值（两个连续寄存器，大端）"""
    hi = regs[addr]
    lo = regs[addr + 1]
    raw = struct.pack(">HH", hi, lo)
    return struct.unpack(">i", raw)[0]


def read_reg16(regs: list[int], addr: int, signed: bool = False) -> int:
    """读取 16bit 寄存器值"""
    val = regs[addr]
    if signed and val > 32767:
        val -= 65536
    return val


def i32_to_regs(value: int) -> tuple[int, int]:
    """32bit 有符号整数 → 2 个寄存器 (大端)"""
    raw = struct.pack(">i", int(value))
    return struct.unpack(">HH", raw)


class ModbusCollector:
    def __init__(self):
        self.client: AsyncModbusTcpClient | None = None
        self._running = False
        self._latest: dict[str, dict] = {}
        self._cmd_status: dict[str, dict] = {}  # 设备指令执行状态

    async def connect(self):
        self.client = AsyncModbusTcpClient(
            host=settings.modbus_host, port=settings.modbus_port
        )
        await self.client.connect()

    async def close(self):
        if self.client:
            await self.client.close()
        self._running = False

    # ── 写入方法 ──────────────────────────────────────────

    async def write_device_command(self, device_id: str, command: dict) -> dict:
        """向设备写入控制指令。
        command 格式: {"register": addr, "values": [v1, v2, ...], "mode": 0-3}
        返回: {"success": bool, "message": str}
        """
        if not self.client or not self.client.connected:
            await self.connect()

        _slave_map = {"pv": settings.modbus_pv_slave, "battery": settings.modbus_battery_slave,
                       "chp": settings.modbus_chp_slave, "heatpump": settings.modbus_hp_slave,
                       "thermal": settings.modbus_ts_slave}
        slave = 2
        for key, sl in _slave_map.items():
            if key in device_id: slave = sl; break
        addr = command.get("register", 50)
        values = command.get("values", [])
        mode = command.get("mode", 0)
        duration = command.get("duration", 0)
        assert self.client is not None

        val_str = ",".join(str(int(v)) for v in values)
        try:
            # 写入控制值：寄存器 50 始终为 32bit
            if len(values) >= 1:
                hi, lo = i32_to_regs(int(values[0]))
                await self.client.write_registers(address=addr, values=[hi, lo], slave=slave)
            elif mode == 0:
                # 恢复自动时清零功率设定
                hi, lo = i32_to_regs(0)
                await self.client.write_registers(address=addr, values=[hi, lo], slave=slave)

            # 写入模式寄存器（地址 52）
            await self.client.write_register(address=52, value=mode, slave=slave)
            # 写入时效寄存器（地址 54）
            await self.client.write_register(address=54, value=duration, slave=slave)

            msg = f"指令已下发, 持续{duration}秒" if duration > 0 else "指令已下发, 永久有效"
            self._cmd_status[device_id] = {"register": addr, "mode": mode, "duration": duration, "status": "sent"}
            td_manager.record_command(device_id, addr, val_str, mode, duration, True, msg)
            return {"success": True, "message": msg}
        except Exception as e:
            td_manager.record_command(device_id, addr, val_str, mode, duration, False, str(e))
            return {"success": False, "message": str(e)}

    async def read_cmd_status(self, device_id: str) -> dict:
        """读取控制指令执行状态（寄存器 53）"""
        if not self.client or not self.client.connected:
            await self.connect()

        _slave_map = {"pv": settings.modbus_pv_slave, "battery": settings.modbus_battery_slave,
                       "chp": settings.modbus_chp_slave, "heatpump": settings.modbus_hp_slave,
                       "thermal": settings.modbus_ts_slave}
        slave = 2
        for key, sl in _slave_map.items():
            if key in device_id: slave = sl; break
        assert self.client is not None

        try:
            resp = await self.client.read_holding_registers(
                address=53, count=1, slave=slave
            )
            if resp and not resp.isError():
                status_code = resp.registers[0]
                status_map = {0: "待执行", 1: "已执行", 2: "执行失败"}
                return {"device_id": device_id, "cmd_status": status_code,
                        "message": status_map.get(status_code, "未知")}
            return {"device_id": device_id, "cmd_status": -1, "message": "读取失败"}
        except Exception as e:
            return {"device_id": device_id, "cmd_status": -1, "message": str(e)}

    async def collect_once(self):
        if not self.client or not self.client.connected:
            await self.connect()

        ts = datetime.now()
        ts_ms = int(ts.timestamp() * 1000)
        records = []

        # ── 光伏逆变器 (slave=1), 地址 0-15 ──
        try:
            resp = await self.client.read_holding_registers(
                address=0, count=16, slave=settings.modbus_pv_slave
            )
        except Exception as e:
            print(f"[PV read error] {e}")
            resp = None

        if resp and not resp.isError():
            regs = resp.registers
            pv_data = {}
            for name, addr, scale in PV_POINTS:
                if addr in PV_32BIT_ADDRS:
                    raw_val = read_reg32(regs, addr)
                else:
                    raw_val = read_reg16(regs, addr, signed=(addr == 1))
                val = raw_val / scale
                pv_data[name] = round(val, 3)
                records.append({
                    "device_id": "pv_inverter_01",
                    "point_name": name,
                    "val": round(val, 3),
                    "quality": 0,
                    "ts": ts_ms,
                })
            self._latest["pv_inverter_01"] = pv_data

        # ── 储能 PCS (slave=2), 地址 0-12 ──
        try:
            resp = await self.client.read_holding_registers(
                address=0, count=13, slave=settings.modbus_battery_slave
            )
        except Exception as e:
            print(f"[Battery read error] {e}")
            resp = None

        if resp and not resp.isError():
            regs = resp.registers
            bat_data = {}
            for name, addr, scale in BATTERY_POINTS:
                if addr in BATTERY_32BIT_ADDRS:
                    raw_val = read_reg32(regs, addr)
                else:
                    raw_val = read_reg16(regs, addr, signed=(addr == 1))
                val = raw_val / scale
                bat_data[name] = round(val, 3)
                records.append({
                    "device_id": "battery_pcs_01",
                    "point_name": name,
                    "val": round(val, 3),
                    "quality": 0,
                    "ts": ts_ms,
                })
            self._latest["battery_pcs_01"] = bat_data

        # ── CHP (slave=3), 地址 0-9 ──
        try:
            resp = await self.client.read_holding_registers(
                address=0, count=10, slave=settings.modbus_chp_slave
            )
        except Exception as e:
            print(f"[CHP read error] {e}"); resp = None

        if resp and not resp.isError():
            regs = resp.registers; data = {}
            for name, addr, scale, *_ in CHP_POINTS:
                raw_val = read_reg32(regs, addr) if addr in CHP_32BIT else read_reg16(regs, addr)
                val = raw_val / scale
                data[name] = round(val, 3)
                records.append({"device_id": "chp_01", "point_name": name,
                                "val": round(val, 3), "quality": 0, "ts": ts_ms})
            self._latest["chp_01"] = data

        # ── 热泵 (slave=4), 地址 0-7 ──
        try:
            resp = await self.client.read_holding_registers(
                address=0, count=8, slave=settings.modbus_hp_slave
            )
        except Exception as e:
            print(f"[HP read error] {e}"); resp = None

        if resp and not resp.isError():
            regs = resp.registers; data = {}
            for name, addr, scale, *_ in HP_POINTS:
                raw_val = read_reg32(regs, addr) if addr in HP_32BIT else read_reg16(regs, addr)
                val = raw_val / scale
                data[name] = round(val, 3)
                records.append({"device_id": "heatpump_01", "point_name": name,
                                "val": round(val, 3), "quality": 0, "ts": ts_ms})
            self._latest["heatpump_01"] = data

        # ── 蓄能罐 (slave=5), 地址 0-7 ──
        try:
            resp = await self.client.read_holding_registers(
                address=0, count=8, slave=settings.modbus_ts_slave
            )
        except Exception as e:
            print(f"[TS read error] {e}"); resp = None

        if resp and not resp.isError():
            regs = resp.registers; data = {}
            for name, addr, scale, *_ in TS_POINTS:
                raw_val = read_reg32(regs, addr) if addr in TS_32BIT else read_reg16(regs, addr)
                val = raw_val / scale
                data[name] = round(val, 3)
                records.append({"device_id": "thermal_storage_01", "point_name": name,
                                "val": round(val, 3), "quality": 0, "ts": ts_ms})
            self._latest["thermal_storage_01"] = data

        # ── 批量写入 TDengine ──
        if records:
            try:
                td_manager.write_telemetry(records)
            except Exception as e:
                print(f"[TDengine write error] {e}")

        return records

    async def run_loop(self):
        self._running = True
        interval = settings.collect_interval
        print(f"[Collector] 开始采集, 间隔 {interval}s")
        while self._running:
            started = time.time()
            try:
                await self.collect_once()
            except Exception as e:
                print(f"[Collector] 采集异常: {e}")
            elapsed = time.time() - started
            await asyncio.sleep(max(0, interval - elapsed))

    def get_latest(self, device_id: str | None = None) -> dict:
        if device_id:
            return self._latest.get(device_id, {})
        return self._latest


collector = ModbusCollector()
