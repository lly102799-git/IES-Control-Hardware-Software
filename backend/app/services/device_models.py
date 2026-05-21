"""
关键设备稳态数学模型 — 为 MILP 优化引擎提供约束参数。

所有功率单位：kW，能量单位：kWh，温度单位：°C。
爬坡速率单位：kW/step（每 5-min 时间步）。
参数与 simulator/device_simulator.py 保持一致。
"""

from dataclasses import dataclass


@dataclass
class PVModel:
    """光伏逆变器模型。不可控出力，无爬坡约束（由天气决定）。"""
    rated_power: float = 100.0       # kW
    panel_efficiency: float = 0.20
    inverter_efficiency: float = 0.96
    temp_coeff: float = -0.0035     # /°C

    def constraints(self) -> dict:
        return {
            "p_min": 0.0,
            "p_max": self.rated_power,
            "ramp_up": self.rated_power,    # PV ramp 不约束 (由天气驱动)
            "ramp_down": self.rated_power,
        }


@dataclass
class BatteryModel:
    """储能变流器 + 电池模型。包含爬坡约束。"""
    rated_power: float = 50.0        # kW
    energy_capacity: float = 200.0   # kWh
    soc_min: float = 0.10
    soc_max: float = 0.95
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    ramp_rate: float = 20.0          # kW/step (5min), 即 240kW/min → 实际保守取 20kW
    standby_loss_rate: float = 0.0

    def constraints(self) -> dict:
        return {
            "p_ch_max": self.rated_power,
            "p_dis_max": self.rated_power,
            "soc_min": self.soc_min,
            "soc_max": self.soc_max,
            "eta_ch": self.charge_efficiency,
            "eta_dis": self.discharge_efficiency,
            "e_rated": self.energy_capacity,
            "ramp_up": self.ramp_rate,
            "ramp_down": self.ramp_rate,
        }


@dataclass
class CHPModel:
    """燃气三联供 CHP。包含冷启动和爬坡约束。"""
    rated_elec_power: float = 50.0   # kW
    min_elec_power: float = 15.0     # 最小出力 (30%)
    elec_efficiency: float = 0.35
    heat_efficiency: float = 0.50
    total_efficiency: float = 0.85
    heat_to_power_ratio: float = 1.43
    ramp_rate: float = 10.0          # kW/step, CHP 响应较慢

    def constraints(self) -> dict:
        return {
            "p_min": self.min_elec_power,
            "p_max": self.rated_elec_power,
            "hpr": self.heat_to_power_ratio,
            "eta_elec": self.elec_efficiency,
            "ramp_up": self.ramp_rate,
            "ramp_down": self.ramp_rate,
        }


@dataclass
class HeatPumpModel:
    """空气源热泵。变频调节，响应快。"""
    rated_elec_power: float = 30.0   # kW
    cop_rated: float = 3.5
    ramp_rate: float = 15.0          # kW/step, 变频热泵调节较快

    def cop_at(self, ambient_temp: float) -> float:
        return max(1.5, min(5.0, self.cop_rated * (1.0 + 0.03 * (ambient_temp - 7.0))))

    def constraints(self) -> dict:
        return {
            "p_min": 0.0,
            "p_max": self.rated_elec_power,
            "cop_default": self.cop_rated,
            "ramp_up": self.ramp_rate,
            "ramp_down": self.ramp_rate,
        }


@dataclass
class ThermalStorageModel:
    """蓄能罐模型。"""
    rated_power: float = 40.0        # kW
    heat_capacity: float = 500.0     # kWh
    soc_min: float = 0.05
    soc_max: float = 0.95
    charge_efficiency: float = 0.92
    discharge_efficiency: float = 0.92
    ramp_rate: float = 25.0          # kW/step, 蓄能罐功率调节较快
    heat_loss_rate: float = 0.005    # /h

    def constraints(self) -> dict:
        return {
            "p_ch_max": self.rated_power,
            "p_dis_max": self.rated_power,
            "soc_min": self.soc_min,
            "soc_max": self.soc_max,
            "eta_ch": self.charge_efficiency,
            "eta_dis": self.discharge_efficiency,
            "e_rated": self.heat_capacity,
            "loss_rate": self.heat_loss_rate,
            "ramp_up": self.ramp_rate,
            "ramp_down": self.ramp_rate,
        }


# ── 聚合接口 ──────────────────────────────────────────────

_device_params_cache: dict | None = None


def get_device_params() -> dict[str, dict]:
    global _device_params_cache
    if _device_params_cache is not None:
        return _device_params_cache

    pv = PVModel()
    bat = BatteryModel()
    chp = CHPModel()
    hp = HeatPumpModel()
    ts = ThermalStorageModel()

    _device_params_cache = {
        "pv_inverter_01": pv.constraints(),
        "battery_pcs_01": bat.constraints(),
        "chp_01": chp.constraints(),
        "heatpump_01": hp.constraints(),
        "thermal_storage_01": ts.constraints(),
        "_meta": {
            "grid_import_max": 200.0,
            "grid_export_max": 100.0,
            "dt_hours": 1.0 / 12.0,        # 5 min = 1/12 h
            "step_minutes": 5,
            "horizon_hours": 24,
            "num_steps": 288,              # 24h × 12 steps/h
            "dispatch_interval": 300,      # 指令下发间隔 5min
        },
    }
    return _device_params_cache
