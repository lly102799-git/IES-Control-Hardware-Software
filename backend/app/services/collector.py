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
from . import point_table


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

        # ── 动态轮询所有配置的 Modbus 设备 ──
        tables = point_table.get_all()
        for device_id, cfg in tables.items():
            slave = cfg["slave_id"]
            start = cfg["read_start"]
            count = cfg["read_count"]
            try:
                resp = await self.client.read_holding_registers(
                    address=start, count=count, slave=slave
                )
            except Exception as e:
                print(f"[{device_id} read error] {e}")
                continue

            if not resp or resp.isError():
                continue

            regs = resp.registers
            dev_data: dict[str, float] = {}
            for pt in cfg.get("points", []):
                name = pt["name"]
                addr = pt["register"] - start  # 转换为 regs 列表索引
                raw_val = read_reg32(regs, addr) if pt.get("is_32bit") else read_reg16(regs, addr, pt.get("is_signed", False))
                val = raw_val / pt["scale"]
                dev_data[name] = round(val, 3)
                records.append({
                    "device_id": device_id,
                    "point_name": name,
                    "val": round(val, 3),
                    "quality": 0,
                    "ts": ts_ms,
                })
            self._latest[device_id] = dev_data

        # ── 批量写入 TDengine ──
        if records:
            try:
                td_manager.write_telemetry(records)
            except Exception as e:
                print(f"[TDengine write error] {e}")

        return records

    async def run_loop(self):
        point_table.load()
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
