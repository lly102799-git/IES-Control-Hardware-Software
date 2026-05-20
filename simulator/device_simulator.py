"""
Modbus TCP 设备模拟器 —— 仿真光伏逆变器和储能 PCS。
支持控制指令响应（可写寄存器）。

Modbus 寄存器点表（0-based，pymodbus 3.7.x）：

 遥测区 (地址 0-15, 只读):
   PV (slave_id=1):
     地址 0: DC电压 (×10),     地址 1: DC电流 (×10)
     地址 2: AC电压 (×10),     地址 3: AC电流 (×10)
     地址 4-5: 有功功率 (32bit W)
     地址 6-7: 无功功率 (32bit Var)
     地址 8: 功率因数 (×100),   地址 9: 频率 (×10)
     地址 10: 日发电量 (×10),   地址 11-12: 总发电量 (32bit kWh)
     地址 13: 组件温度 (×10),   地址 14: 机柜温度 (×10)
     地址 15: 运行状态

   Battery (slave_id=2):
     地址 0: DC电压 (×10),     地址 1: DC电流 (×10, 有符号)
     地址 2: AC电压 (×10),     地址 3: AC电流 (×10)
     地址 4-5: 有功功率 (32bit W 有符号)
     地址 6-7: 无功功率 (32bit Var)
     地址 8: SOC (×10),         地址 9: SOH (×10)
     地址 10: 电池温度 (×10),   地址 11: 机柜温度 (×10)
     地址 12: 运行状态

 控制区 (地址 50-55, 可读写):
   PV:
     地址 50-51: 功率限值 (32bit W, 0=MPPT自动)
     地址 52: 控制模式 (0=自动, 1=限功率)
   Battery:
     地址 50-51: 功率设定值 (32bit W 有符号, 正=放电, 负=充电)
     地址 52: 控制模式 (0=自动, 1=强制充电, 2=强制放电, 3=待机)
     地址 53: 指令执行状态 (0=待执行, 1=已执行, 2=执行失败)
"""

import asyncio
import math
import os
import random
import struct
import time
from dataclasses import dataclass
from datetime import datetime

from pymodbus.datastore import (
    ModbusServerContext,
    ModbusSlaveContext,
    ModbusSequentialDataBlock,
)
from pymodbus.device import ModbusDeviceIdentification
from pymodbus.server import StartAsyncTcpServer

_TZ_OFFSET = int(os.environ.get("TZ_OFFSET_HOURS", "8"))


def local_hour(t: float) -> float:
    return ((t + _TZ_OFFSET * 3600) % 86400) / 3600


# ── 数据模型 ──────────────────────────────────────────────


@dataclass
class PvInverterState:
    dc_voltage: float = 650.0
    dc_current: float = 0.0
    ac_voltage_a: float = 230.0
    ac_current_a: float = 0.0
    active_power: float = 0.0
    reactive_power: float = 0.0
    pf: float = 1.0
    frequency: float = 50.0
    daily_energy: float = 0.0
    total_energy: float = 0.0
    temp_module: float = 25.0
    temp_cabinet: float = 30.0
    status: int = 1
    rated_power: int = 100000
    # 控制字段
    power_limit: int = 0         # W, 0=MPPT 自动
    control_mode: int = 0        # 0=自动, 1=限功率
    cmd_status: int = 0          # 0=待执行, 1=已执行, 2=失败
    effective_until: float = 0   # Unix timestamp, 0=永久有效

    def update(self, t: float, hr=None):
        hour = local_hour(t)

        # 时效检查：到期自动恢复
        if self.effective_until > 0 and t >= self.effective_until:
            self.power_limit = 0
            self.control_mode = 0
            self.effective_until = 0
            self.cmd_status = 0
            if hr is not None:
                hr.setValues(52, [0])  # 清 mode
                hr.setValues(54, [0])  # 清 duration

        if 6 <= hour <= 19:
            irradiance_ratio = max(0, math.sin((hour - 6) / 13 * math.pi))
        else:
            irradiance_ratio = 0

        cloud_factor = 1.0 - abs(math.sin(t / 1800 + random.random()) * 0.3)
        meas_noise = random.gauss(0, 0.005)
        pv_output = self.rated_power * irradiance_ratio * cloud_factor + meas_noise * self.rated_power
        pv_output = max(0, min(pv_output, self.rated_power))

        # 功率限值控制
        if self.power_limit > 0:
            pv_output = min(pv_output, self.power_limit)
            self.control_mode = 1
        else:
            self.control_mode = 0

        self.active_power = round(pv_output, 1)
        self.dc_voltage = round(650 + (pv_output / self.rated_power) * 150, 1)
        self.dc_current = round(pv_output / max(self.dc_voltage, 1), 2)
        self.ac_voltage_a = round(230 + random.gauss(0, 1), 1)
        self.ac_current_a = round(pv_output / 230, 2)
        self.daily_energy = round(pv_output / 1000 * max(0, hour - 6) / 13 * 8, 1)
        self.temp_module = round(25 + irradiance_ratio * 30 + random.gauss(0, 0.5), 1)
        self.status = 1 if irradiance_ratio > 0.02 else 0


@dataclass
class BatteryPcsState:
    dc_voltage: float = 400.0
    dc_current: float = 0.0
    ac_voltage_a: float = 230.0
    ac_current_a: float = 0.0
    active_power: float = 0.0
    reactive_power: float = 0.0
    soc: float = 50.0
    soh: float = 98.0
    temp_battery: float = 28.0
    temp_cabinet: float = 32.0
    status: int = 1
    rated_power: int = 50000
    capacity: float = 200.0
    # 控制字段
    power_setpoint: int = 0       # W, 正=放电, 负=充电, 0=自动
    control_mode: int = 0         # 0=自动, 1=强制充电, 2=强制放电, 3=待机
    cmd_status: int = 0           # 0=待执行, 1=已执行, 2=失败
    effective_until: float = 0    # Unix timestamp, 0=永久有效

    def update(self, t: float, hr=None):
        hour = local_hour(t)

        # 时效检查：到期自动恢复 + 清零控制寄存器
        if self.effective_until > 0 and t >= self.effective_until:
            self.power_setpoint = 0
            self.control_mode = 0
            self.effective_until = 0
            self.cmd_status = 0
            if hr is not None:
                hr.setValues(52, [0])
                hr.setValues(54, [0])

        # power_setpoint 优先（含符号：正=放电, 负=充电），mode 提供缺省倍率
        if self.control_mode == 3:  # 强制待机
            charge_rate, self.status = 0.0, 0
        elif self.power_setpoint != 0:
            charge_rate = -self.power_setpoint / self.rated_power
            self.status = 2 if self.power_setpoint > 0 else 1
        elif self.control_mode == 1:  # 强制充电（默认 0.5C）
            charge_rate, self.status = 0.5, 1
        elif self.control_mode == 2:  # 强制放电（默认 0.5C）
            charge_rate, self.status = -0.5, 2
        else:
            # 分时电价自动策略
            if 0 <= hour < 7:
                charge_rate, self.status = 0.5, 1
            elif 9 <= hour < 12:
                charge_rate, self.status = -0.4, 2
            elif 17 <= hour < 21:
                charge_rate, self.status = -0.6, 2
            else:
                charge_rate, self.status = 0.0, 0

        # 指令执行后标记已执行
        if self.cmd_status == 0 and self.control_mode != 0:
            self.cmd_status = 1

        power_noise = random.gauss(0, 50)
        self.active_power = round(charge_rate * self.rated_power + power_noise, 1)
        self.active_power = max(-self.rated_power, min(self.active_power, self.rated_power))

        power_kw = self.active_power / 1000
        if power_kw > 0:
            delta_soc = -power_kw * 5 / 3600 / self.capacity * 100 / 0.95
        else:
            delta_soc = -power_kw * 5 / 3600 / self.capacity * 100 * 0.95

        self.soc = round(max(10, min(95, self.soc + delta_soc)), 1)
        self.dc_voltage = round(350 + self.soc * 1.5 + random.gauss(0, 0.5), 1)
        self.dc_current = round(self.active_power / max(self.dc_voltage, 1), 2)
        self.ac_voltage_a = round(230 + random.gauss(0, 1), 1)
        self.ac_current_a = round(abs(self.active_power) / 230, 2)
        self.temp_battery = round(25 + abs(self.active_power / self.rated_power) * 12 + random.gauss(0, 0.3), 1)


# ── 辅助函数 ──────────────────────────────────────────────


def f2r(value: float, scale: int = 1) -> int:
    return int(value * scale)


def i32_to_regs(value: int) -> tuple[int, int]:
    raw = struct.pack(">i", int(value))
    return struct.unpack(">HH", raw)


def read_i32_from_regs(regs: list[int], offset: int) -> int:
    """从寄存器列表中读取 32bit 有符号值"""
    raw = struct.pack(">HH", regs[offset], regs[offset + 1])
    return struct.unpack(">i", raw)[0]


# ── 寄存器更新器 ──────────────────────────────────────────


def update_pv_regs(ctx: ModbusSlaveContext, state: PvInverterState):
    hr = ctx.store["h"]
    hr.setValues(0, [f2r(state.dc_voltage, 10)])
    hr.setValues(1, [f2r(state.dc_current, 10)])
    hr.setValues(2, [f2r(state.ac_voltage_a, 10)])
    hr.setValues(3, [f2r(state.ac_current_a, 10)])
    hi, lo = i32_to_regs(int(state.active_power))
    hr.setValues(4, [hi, lo])
    hi, lo = i32_to_regs(int(state.reactive_power))
    hr.setValues(6, [hi, lo])
    hr.setValues(8, [f2r(state.pf, 100)])
    hr.setValues(9, [f2r(state.frequency, 10)])
    hr.setValues(10, [f2r(state.daily_energy, 10)])
    hi, lo = i32_to_regs(int(state.total_energy))
    hr.setValues(11, [hi, lo])
    hr.setValues(13, [f2r(state.temp_module, 10)])
    hr.setValues(14, [f2r(state.temp_cabinet, 10)])
    hr.setValues(15, [state.status])
    # 回写控制状态反馈 + 剩余时效
    hr.setValues(53, [state.cmd_status])
    remaining = max(0, int(state.effective_until - time.time())) if state.effective_until > 0 else 0
    hr.setValues(54, [remaining])


def update_battery_regs(ctx: ModbusSlaveContext, state: BatteryPcsState):
    hr = ctx.store["h"]
    hr.setValues(0, [f2r(state.dc_voltage, 10)])
    dc_i_raw = int(state.dc_current * 10)
    hr.setValues(1, [dc_i_raw & 0xFFFF])
    hr.setValues(2, [f2r(state.ac_voltage_a, 10)])
    hr.setValues(3, [f2r(state.ac_current_a, 10)])
    hi, lo = i32_to_regs(int(state.active_power))
    hr.setValues(4, [hi, lo])
    hi, lo = i32_to_regs(int(state.reactive_power))
    hr.setValues(6, [hi, lo])
    hr.setValues(8, [f2r(state.soc, 10)])
    hr.setValues(9, [f2r(state.soh, 10)])
    hr.setValues(10, [f2r(state.temp_battery, 10)])
    hr.setValues(11, [f2r(state.temp_cabinet, 10)])
    hr.setValues(12, [state.status])
    # 回写控制状态反馈 + 剩余时效
    hr.setValues(53, [state.cmd_status])
    remaining = max(0, int(state.effective_until - time.time())) if state.effective_until > 0 else 0
    hr.setValues(54, [remaining])


def read_pv_control(ctx: ModbusSlaveContext, state: PvInverterState):
    """从寄存器读取外部写入的控制指令（地址 50-54）"""
    hr = ctx.store["h"]
    t = time.time()
    try:
        state.power_limit = read_i32_from_regs(hr.getValues(50, 2), 0)
    except Exception:
        pass
    try:
        mode = hr.getValues(52, 1)[0]
        if mode in (0, 1):
            if mode == 1 and state.power_limit > 0:
                state.cmd_status = 1
                # 读取时效 (reg 54)
                duration = hr.getValues(54, 1)[0]
                if duration > 0 and state.effective_until == 0:
                    state.effective_until = t + duration
            elif mode == 0:
                state.power_limit = 0
                state.cmd_status = 0
                state.effective_until = 0
    except Exception:
        pass


def read_battery_control(ctx: ModbusSlaveContext, state: BatteryPcsState):
    """从寄存器读取外部写入的控制指令（地址 50-54）"""
    hr = ctx.store["h"]
    t = time.time()
    try:
        state.power_setpoint = read_i32_from_regs(hr.getValues(50, 2), 0)
    except Exception:
        pass
    try:
        mode = hr.getValues(52, 1)[0]
        if mode != state.control_mode:
            state.control_mode = mode
            state.cmd_status = 0  # 新指令待执行
            if mode == 0:
                # 恢复自动：同时清零功率设定
                state.power_setpoint = 0
                state.effective_until = 0
            else:
                duration = hr.getValues(54, 1)[0]
                state.effective_until = (t + duration) if duration > 0 else 0
        elif mode == 0:
            state.effective_until = 0
    except Exception:
        pass


# ── CHP 三联供 (slave=3) ──────────────────────────────────


@dataclass
class ChpState:
    """燃气 CHP: 天然气 → 电 + 热"""
    active_power: float = 0.0        # W, 发电功率
    heat_power: float = 0.0          # W, 余热功率
    gas_flow: float = 0.0            # Nm³/h, 燃气流量
    elec_efficiency: float = 0.35    # 发电效率
    total_efficiency: float = 0.85   # 综合效率 (电+热)
    temp_out: float = 80.0           # °C, 出水温度
    temp_in: float = 60.0            # °C, 回水温度
    status: int = 0                  # 0=停机, 1=运行, 2=故障
    rated_power: int = 50000         # 额定发电功率 W
    # 控制
    power_setpoint: int = 0          # W, 0=自动
    control_mode: int = 0            # 0=自动(以热定电), 1=以电定热, 2=停机
    cmd_status: int = 0
    effective_until: float = 0

    def update(self, t: float, hr=None):
        if self.effective_until > 0 and t >= self.effective_until:
            self.power_setpoint = 0; self.control_mode = 0
            self.effective_until = 0; self.cmd_status = 0
            if hr: hr.setValues(52, [0]); hr.setValues(54, [0])

        hour = local_hour(t)

        if self.control_mode == 2:  # 强制停机
            load_ratio, self.status = 0.0, 0
        elif self.control_mode == 1:  # 以电定热
            load_ratio = min(1.0, self.power_setpoint / self.rated_power) if self.power_setpoint > 0 else 0
            self.status = 1 if load_ratio > 0.05 else 0
        elif self.control_mode == 0:  # 自动
            # 冬季(11-3月)或夜间热需求高
            winter = (datetime.now().month in [11, 12, 1, 2, 3])
            if winter and (6 <= hour <= 9 or 17 <= hour <= 21):
                load_ratio = 0.7 + random.gauss(0, 0.03)
            elif winter:
                load_ratio = 0.4 + random.gauss(0, 0.02)
            elif 6 <= hour <= 19:
                load_ratio = 0.2 + random.gauss(0, 0.01)
            else:
                load_ratio = 0.0
            self.status = 1 if load_ratio > 0.05 else 0
        else:
            load_ratio, self.status = 0.0, 0

        self.active_power = round(load_ratio * self.rated_power + random.gauss(0, 200), 1)
        self.active_power = max(0, min(self.active_power, self.rated_power))
        heat_ratio = (self.total_efficiency - self.elec_efficiency) / self.elec_efficiency
        self.heat_power = round(self.active_power * heat_ratio, 1)
        self.gas_flow = round(self.active_power / (self.elec_efficiency * 10.8 * 1000), 3)  # Nm³/h
        self.temp_out = round(80 + load_ratio * 10 + random.gauss(0, 0.3), 1)
        self.temp_in = round(60 + load_ratio * 5 + random.gauss(0, 0.2), 1)


def update_chp_regs(ctx: ModbusSlaveContext, s: ChpState):
    hr = ctx.store["h"]
    hi, lo = i32_to_regs(int(s.active_power)); hr.setValues(0, [hi, lo])
    hi, lo = i32_to_regs(int(s.heat_power)); hr.setValues(2, [hi, lo])
    hr.setValues(4, [f2r(s.gas_flow, 10)])
    hr.setValues(5, [f2r(s.elec_efficiency, 100)])
    hr.setValues(6, [f2r(s.total_efficiency, 100)])
    hr.setValues(7, [f2r(s.temp_out, 10)])
    hr.setValues(8, [f2r(s.temp_in, 10)])
    hr.setValues(9, [s.status])
    hr.setValues(53, [s.cmd_status])
    remaining = max(0, int(s.effective_until - time.time())) if s.effective_until > 0 else 0
    hr.setValues(54, [remaining])


def read_chp_control(ctx: ModbusSlaveContext, s: ChpState):
    hr = ctx.store["h"]; t = time.time()
    try: s.power_setpoint = read_i32_from_regs(hr.getValues(50, 2), 0)
    except: pass
    try:
        mode = hr.getValues(52, 1)[0]
        if mode != s.control_mode:
            s.control_mode = mode; s.cmd_status = 0
            if mode == 0: s.power_setpoint = 0; s.effective_until = 0
            else:
                d = hr.getValues(54, 1)[0]; s.effective_until = (t + d) if d > 0 else 0
        elif mode == 0: s.effective_until = 0
    except: pass


# ── 热泵 (slave=4) ────────────────────────────────────────


@dataclass
class HeatPumpState:
    """电驱动热泵: 电 → 热/冷 (COP > 1)"""
    elec_power: float = 0.0          # W, 耗电功率
    thermal_power: float = 0.0       # W, 制热/冷功率
    cop: float = 3.5                 # 性能系数
    temp_out: float = 45.0           # °C, 出水温度
    temp_in: float = 35.0            # °C, 回水温度
    mode: int = 0                    # 0=停机, 1=制热, 2=制冷, 3=故障
    status: int = 0
    rated_power: int = 30000         # 额定电功率 W
    # 控制
    power_setpoint: int = 0          # W, 正=制热, 负=制冷
    control_mode: int = 0            # 0=自动, 1=制热, 2=制冷, 3=停机
    cmd_status: int = 0
    effective_until: float = 0

    def update(self, t: float, hr=None):
        if self.effective_until > 0 and t >= self.effective_until:
            self.power_setpoint = 0; self.control_mode = 0
            self.effective_until = 0; self.cmd_status = 0
            if hr: hr.setValues(52, [0]); hr.setValues(54, [0])

        hour = local_hour(t)
        winter = datetime.now().month in [11, 12, 1, 2, 3]

        if self.control_mode == 3:
            self.mode, self.status, load_ratio = 0, 0, 0.0
        elif self.control_mode == 1:  # 强制制热
            self.mode, self.status = 1, 1
            sp = abs(self.power_setpoint)
            load_ratio = min(1.0, sp / self.rated_power) if sp > 0 else 0.5
        elif self.control_mode == 2:  # 强制制冷
            self.mode, self.status = 2, 1
            sp = abs(self.power_setpoint)
            load_ratio = min(1.0, sp / self.rated_power) if sp > 0 else 0.5
        elif self.control_mode == 0:  # 自动
            if winter and (6 <= hour <= 9 or 17 <= hour <= 22):
                self.mode, self.status = 1, 1; load_ratio = 0.6 + random.gauss(0, 0.03)
            elif not winter and (10 <= hour <= 16):
                self.mode, self.status = 2, 1; load_ratio = 0.5 + random.gauss(0, 0.02)
            else:
                self.mode, self.status = 0, 0; load_ratio = 0.0
        else:
            self.mode, self.status, load_ratio = 0, 0, 0.0

        self.elec_power = round(load_ratio * self.rated_power + random.gauss(0, 100), 1)
        self.elec_power = max(0, min(self.elec_power, self.rated_power))
        # COP 随工况变化: 制热 COP 3-4.5, 制冷 COP 2.5-3.5
        if self.mode == 1:
            self.cop = round(3.5 + (55 - self.temp_out) * 0.05 + random.gauss(0, 0.05), 2)
        elif self.mode == 2:
            self.cop = round(3.0 + (35 - self.temp_out) * 0.05 + random.gauss(0, 0.05), 2)
        else:
            self.cop = 0
        self.thermal_power = round(self.elec_power * self.cop, 1) if self.mode > 0 else 0
        self.temp_out = round(45 + load_ratio * 10 + random.gauss(0, 0.3), 1)
        self.temp_in = round(35 + load_ratio * 5 + random.gauss(0, 0.2), 1)


def update_hp_regs(ctx: ModbusSlaveContext, s: HeatPumpState):
    hr = ctx.store["h"]
    hi, lo = i32_to_regs(int(s.elec_power)); hr.setValues(0, [hi, lo])
    hi, lo = i32_to_regs(int(s.thermal_power)); hr.setValues(2, [hi, lo])
    hr.setValues(4, [f2r(s.cop, 10)])
    hr.setValues(5, [f2r(s.temp_out, 10)])
    hr.setValues(6, [f2r(s.temp_in, 10)])
    hr.setValues(7, [s.mode])
    hr.setValues(53, [s.cmd_status])
    remaining = max(0, int(s.effective_until - time.time())) if s.effective_until > 0 else 0
    hr.setValues(54, [remaining])


def read_hp_control(ctx: ModbusSlaveContext, s: HeatPumpState):
    hr = ctx.store["h"]; t = time.time()
    try: s.power_setpoint = read_i32_from_regs(hr.getValues(50, 2), 0)
    except: pass
    try:
        mode = hr.getValues(52, 1)[0]
        if mode != s.control_mode:
            s.control_mode = mode; s.cmd_status = 0
            if mode == 0: s.power_setpoint = 0; s.effective_until = 0
            else:
                d = hr.getValues(54, 1)[0]; s.effective_until = (t + d) if d > 0 else 0
        elif mode == 0: s.effective_until = 0
    except: pass


# ── 蓄热/蓄冷 (slave=5) ──────────────────────────────────


@dataclass
class ThermalStorageState:
    """蓄能罐: 蓄热 + 蓄冷"""
    heat_stored: float = 100.0       # kWh, 当前蓄热量
    cool_stored: float = 50.0        # kWh, 当前蓄冷量
    power: float = 0.0               # W, 正=蓄热/放冷, 负=放热/蓄冷
    heat_soc: float = 50.0           # %, 储热 SOC
    cool_soc: float = 50.0           # %, 储冷 SOC
    tank_temp: float = 65.0          # °C, 罐温
    mode: int = 0                    # 0=待机, 1=蓄热, 2=放热, 3=蓄冷, 4=放冷
    status: int = 0
    heat_capacity: float = 300.0     # kWh, 储热容量
    cool_capacity: float = 200.0     # kWh, 储冷容量
    rated_power: int = 40000         # W
    # 控制
    power_setpoint: int = 0
    control_mode: int = 0            # 0=自动, 1=蓄热, 2=放热, 3=蓄冷, 4=放冷, 5=停机
    cmd_status: int = 0
    effective_until: float = 0

    def update(self, t: float, hr=None):
        if self.effective_until > 0 and t >= self.effective_until:
            self.power_setpoint = 0; self.control_mode = 0
            self.effective_until = 0; self.cmd_status = 0
            if hr: hr.setValues(52, [0]); hr.setValues(54, [0])

        hour = local_hour(t)
        winter = datetime.now().month in [11, 12, 1, 2, 3]

        sp = abs(self.power_setpoint)
        def_rate = sp / self.rated_power if sp > 0 else None  # 有设定值则用设定值
        if self.control_mode == 5:
            self.mode, self.status, charge_rate = 0, 0, 0.0
        elif self.control_mode == 1:
            self.mode, self.status = 1, 1
            charge_rate = def_rate if def_rate else 0.6
        elif self.control_mode == 2:
            self.mode, self.status = 2, 1
            charge_rate = -(def_rate if def_rate else 0.5)
        elif self.control_mode == 3:
            self.mode, self.status = 3, 1
            charge_rate = def_rate if def_rate else 0.5
        elif self.control_mode == 4:
            self.mode, self.status = 4, 1
            charge_rate = -(def_rate if def_rate else 0.4)
        elif self.control_mode == 0:
            # 自动: 夜间蓄热/蓄冷, 日间放热/放冷
            if winter:
                if 0 <= hour < 6:  # 夜间蓄热
                    self.mode, self.status, charge_rate = 1, 1, 0.5
                elif 6 <= hour < 9 or 17 <= hour < 22:  # 放热
                    self.mode, self.status, charge_rate = 2, 1, -0.5
                else:
                    self.mode, self.status, charge_rate = 0, 0, 0.0
            else:
                if 0 <= hour < 5:  # 夜间蓄冷
                    self.mode, self.status, charge_rate = 3, 1, 0.4
                elif 10 <= hour < 16:  # 日间放冷
                    self.mode, self.status, charge_rate = 4, 1, -0.4
                else:
                    self.mode, self.status, charge_rate = 0, 0, 0.0
        else:
            self.mode, self.status, charge_rate = 0, 0, 0.0

        p_noise = random.gauss(0, 100)
        self.power = round(charge_rate * self.rated_power + p_noise, 1)
        self.power = max(-self.rated_power, min(self.power, self.rated_power))

        # SOC 递推
        power_kw = self.power / 1000
        if self.mode in (1, 3):  # 蓄能
            target_soc = self.heat_soc if self.mode == 1 else self.cool_soc
            cap = self.heat_capacity if self.mode == 1 else self.cool_capacity
            delta_soc = abs(power_kw) * 5 / 3600 / cap * 100 * 0.95
            new_soc = target_soc + delta_soc
        elif self.mode in (2, 4):  # 放能
            target_soc = self.heat_soc if self.mode == 2 else self.cool_soc
            cap = self.heat_capacity if self.mode == 2 else self.cool_capacity
            delta_soc = abs(power_kw) * 5 / 3600 / cap * 100 / 0.95
            new_soc = target_soc - delta_soc
        else:
            new_soc = self.heat_soc

        if self.mode in (1, 2):
            self.heat_soc = round(max(5, min(95, new_soc)), 1)
            self.heat_stored = round(self.heat_soc / 100 * self.heat_capacity, 1)
        elif self.mode in (3, 4):
            self.cool_soc = round(max(5, min(95, new_soc)), 1)
            self.cool_stored = round(self.cool_soc / 100 * self.cool_capacity, 1)

        self.tank_temp = round(65 + self.heat_soc * 0.3 + random.gauss(0, 0.2), 1)


def update_ts_regs(ctx: ModbusSlaveContext, s: ThermalStorageState):
    hr = ctx.store["h"]
    hi, lo = i32_to_regs(int(s.heat_stored * 1000)); hr.setValues(0, [hi, lo])
    hi, lo = i32_to_regs(int(s.power)); hr.setValues(2, [hi, lo])
    hr.setValues(4, [f2r(s.heat_soc, 10)])
    hr.setValues(5, [f2r(s.cool_soc, 10)])
    hr.setValues(6, [f2r(s.tank_temp, 10)])
    hr.setValues(7, [s.mode])
    hr.setValues(53, [s.cmd_status])
    remaining = max(0, int(s.effective_until - time.time())) if s.effective_until > 0 else 0
    hr.setValues(54, [remaining])


def read_ts_control(ctx: ModbusSlaveContext, s: ThermalStorageState):
    hr = ctx.store["h"]; t = time.time()
    try: s.power_setpoint = read_i32_from_regs(hr.getValues(50, 2), 0)
    except: pass
    try:
        mode = hr.getValues(52, 1)[0]
        if mode != s.control_mode:
            s.control_mode = mode; s.cmd_status = 0
            if mode == 0: s.power_setpoint = 0; s.effective_until = 0
            else:
                d = hr.getValues(54, 1)[0]; s.effective_until = (t + d) if d > 0 else 0
        elif mode == 0: s.effective_until = 0
    except: pass


# ── 数据块工厂 ────────────────────────────────────────────


def make_slave() -> ModbusSlaveContext:
    return ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(0, [0] * 100),
        zero_mode=True,
    )


# ── 主程序 ────────────────────────────────────────────────


async def main():
    context = ModbusServerContext(
        slaves={
            0x01: make_slave(), 0x02: make_slave(),
            0x03: make_slave(), 0x04: make_slave(), 0x05: make_slave(),
        },
        single=False,
    )

    pv = PvInverterState()
    battery = BatteryPcsState()
    chp = ChpState()
    hp = HeatPumpState()
    ts = ThermalStorageState()

    print("=== Modbus 多能流设备模拟器 ===")
    print("slave 1: 光伏逆变器  |  slave 2: 储能 PCS  |  slave 3: CHP")
    print("slave 4: 热泵        |  slave 5: 蓄能罐")
    print("遥测区: 0-15  |  控制区: 50-55 (可读写)")
    print("=" * 40)

    async def update_loop():
        while True:
            t = time.time()
            # 1. 读取控制指令
            read_pv_control(context[1], pv)
            read_battery_control(context[2], battery)
            read_chp_control(context[3], chp)
            read_hp_control(context[4], hp)
            read_ts_control(context[5], ts)
            # 2. 更新状态
            pv.update(t, context[1].store["h"])
            battery.update(t, context[2].store["h"])
            chp.update(t, context[3].store["h"])
            hp.update(t, context[4].store["h"])
            ts.update(t, context[5].store["h"])
            # 3. 回写遥测
            update_pv_regs(context[1], pv)
            update_battery_regs(context[2], battery)
            update_chp_regs(context[3], chp)
            update_hp_regs(context[4], hp)
            update_ts_regs(context[5], ts)
            # 4. 日志
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"PV:{pv.active_power/1000:.1f}kW | "
                f"BAT:{battery.active_power/1000:.1f}kW SOC:{battery.soc:.0f}% | "
                f"CHP:{chp.active_power/1000:.1f}kW/{chp.heat_power/1000:.1f}kW | "
                f"HP:{hp.elec_power/1000:.1f}kW COP:{hp.cop:.1f}"
            )
            await asyncio.sleep(5)

    asyncio.create_task(update_loop())

    identity = ModbusDeviceIdentification()
    identity.VendorName = "IES Simulator"
    identity.ProductCode = "IES-SIM"
    identity.MajorMinorRevision = "1.0.0"

    print("启动 Modbus TCP 服务器 (0.0.0.0:5020)...")
    await StartAsyncTcpServer(
        context,
        identity=identity,
        address=("0.0.0.0", 5020),
    )


if __name__ == "__main__":
    asyncio.run(main())
