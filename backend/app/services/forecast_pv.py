"""
光伏功率预测 —— 晴空模型 + 天气类型修正。

方法：
  GHI_clear = cg1 * I0 * exp(-cg2 / sin(α)) * sin(α)   (简化 Ineichen-Perez)
  P_pv = GHI * η * A * (1 - γ_temp * (T_cell - 25))

输入：经纬度 + 日期时间 → 太阳高度角 α
输出：日前 24h + 日内 4h 预测 (15min 分辨率)
"""

import asyncio
import math
import time
from datetime import datetime, timedelta, timezone

from ..core.config import settings
from ..core.tdengine import td_manager
from .collector import collector

# 物理常数
SOLAR_CONSTANT = 1367.0         # W/m²
CG1, CG2 = 0.73, 0.12           # 晴空模型参数 (中等浑浊度)

# 天气衰减系数 (简化：可由外部 API 输入)
WEATHER_FACTORS = {
    "clear": 1.0, "partly_cloudy": 0.75, "cloudy": 0.45,
    "overcast": 0.25, "rain": 0.15,
}


class PvForecaster:
    def __init__(self, lat: float = 39.9, lon: float = 116.4,
                 rated_power: float = 100000.0, panel_efficiency: float = 0.20):
        self.lat = math.radians(lat)
        self.lon = math.radians(lon)
        self.rated_power = rated_power
        self.panel_area = rated_power / (1000.0 * panel_efficiency)  # m²
        self.panel_efficiency = panel_efficiency
        self.temp_coeff = -0.0035  # /°C

    # ── 太阳位置计算 ──────────────────────────────────────

    def solar_declination(self, day_of_year: int) -> float:
        """太阳赤纬角 (弧度)"""
        return math.radians(23.45 * math.sin(math.radians(360.0 / 365 * (day_of_year - 81))))

    def equation_of_time(self, day_of_year: int) -> float:
        """时差方程 (分钟)"""
        b = math.radians(360.0 / 365 * (day_of_year - 81))
        return 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)

    def solar_position(self, dt: datetime) -> tuple[float, float]:
        """返回 (太阳高度角 α, 太阳方位角) 单位：度"""
        day_of_year = dt.timetuple().tm_yday
        hour = dt.hour + dt.minute / 60 + dt.second / 3600
        # 当地太阳时
        lstm = 15 * round(self.lon / (15 * math.pi / 180) / 15) * 15  # 当地标准子午线
        solar_time = hour + (4 * (math.degrees(self.lon) - lstm) + self.equation_of_time(day_of_year)) / 60

        hour_angle = math.radians(15 * (solar_time - 12))
        dec = self.solar_declination(day_of_year)

        sin_alt = (math.sin(self.lat) * math.sin(dec) +
                   math.cos(self.lat) * math.cos(dec) * math.cos(hour_angle))
        altitude = math.asin(max(-1, min(1, sin_alt)))  # 弧度

        # 方位角
        cos_az = ((math.sin(dec) - math.sin(self.lat) * math.sin(altitude)) /
                  (math.cos(self.lat) * math.cos(altitude) + 1e-10))
        azimuth = math.acos(max(-1, min(1, cos_az)))
        if solar_time > 12:
            azimuth = 2 * math.pi - azimuth

        return math.degrees(altitude), math.degrees(azimuth)

    # ── 晴空辐照度 ────────────────────────────────────────

    def clear_sky_ghi(self, dt: datetime) -> float:
        """简化 Ineichen-Perez 晴空 GHI (W/m²)"""
        alt_deg, _ = self.solar_position(dt)
        if alt_deg <= 0:
            return 0.0
        alt_rad = math.radians(alt_deg)
        sin_alt = math.sin(alt_rad)
        air_mass = 1.0 / (sin_alt + 0.50572 * (alt_deg + 6.07995) ** -1.6364)
        ghi = CG1 * SOLAR_CONSTANT * math.exp(-CG2 * air_mass) * sin_alt
        return max(0, ghi)

    # ── 光伏功率转换 ──────────────────────────────────────

    def pv_power(self, ghi: float, temp_ambient: float = 25.0,
                 weather_type: str = "clear") -> float:
        """GHI + 温度 + 天气 → 光伏交流输出功率 (W)"""
        factor = WEATHER_FACTORS.get(weather_type, 0.8)
        ghi_eff = ghi * factor
        # 电池温度估算
        t_cell = temp_ambient + ghi_eff * 0.03
        # 温度修正
        temp_correction = 1.0 + self.temp_coeff * (t_cell - 25)
        # 逆变器效率 0.96
        dc_power = ghi_eff * self.panel_area * self.panel_efficiency * temp_correction
        ac_power = dc_power * 0.96
        return max(0, min(ac_power, self.rated_power))

    # ── 预测接口 ──────────────────────────────────────────

    def forecast(self, start: datetime, hours: int = 24,
                 step_minutes: int = 15,
                 temp_profile: list[float] | None = None,
                 weather_types: list[str] | None = None) -> list[dict]:
        """生成预测序列。
        temp_profile: 逐小时温度 (°C)，长度 = hours
        weather_types: 逐小时天气类型，长度 = hours
        """
        points = []
        total_steps = hours * 60 // step_minutes
        for i in range(total_steps):
            dt = start + timedelta(minutes=i * step_minutes)
            ghi = self.clear_sky_ghi(dt)
            hour_idx = i * step_minutes // 60
            temp = (temp_profile[hour_idx] if temp_profile and hour_idx < len(temp_profile)
                    else 25.0)
            weather = (weather_types[hour_idx] if weather_types and hour_idx < len(weather_types)
                       else "clear")
            power = self.pv_power(ghi, temp, weather)
            points.append({
                "ts": int(dt.timestamp() * 1000),
                "power_kw": round(power / 1000, 2),
                "ghi": round(ghi, 1),
            })
        return points

    # ── 存储 ──────────────────────────────────────────────

    def save_forecast(self, points: list[dict], forecast_type: str = "pv"):
        table = f"{td_manager.db}.forecast_{forecast_type}"
        for p in points:
            cols = "ts, power_kw, ghi" if forecast_type == "pv" else "ts, power_kw"
            vals = f"{p['ts']}, {p['power_kw']}, {p['ghi']}" if forecast_type == "pv" else f"{p['ts']}, {p['power_kw']}"
            td_manager._exec(f"INSERT INTO {table} ({cols}) VALUES ({vals})")


# ── 负荷预测（简化版）─────────────────────────────────────


def _hour_label(dt: datetime) -> str:
    return f"{dt.isoweekday()}_{dt.hour}"


class LoadForecaster:
    def forecast(self, device_id: str, lookahead_hours: int = 24) -> list[dict]:
        """历史同期均值 + 工作日因子"""
        now = datetime.now()
        points = []
        for h in range(lookahead_hours):
            dt = now + timedelta(hours=h)
            label = _hour_label(dt)
            # 查询过去 7 天同时段的历史均值
            try:
                rows = td_manager.query(f"""
                    SELECT AVG(val) as avg_val
                    FROM {device_id}_active_power
                    WHERE ts >= '{ (now - timedelta(days=7)).isoformat() }'
                    AND ts <= '{now.isoformat()}'
                """)
                baseline = rows[0]["avg_val"] if rows and rows[0]["avg_val"] else 0
            except Exception:
                baseline = 0
            # 工作日因子 (简化)
            is_weekday = 1 if dt.isoweekday() <= 5 else 0.7
            forecast_val = baseline * is_weekday
            points.append({
                "ts": int(dt.timestamp() * 1000),
                "power_kw": round(forecast_val / 1000, 2),
            })
        return points


# ── 调度服务 ──────────────────────────────────────────────


class ForecastService:
    def __init__(self):
        self.pv = PvForecaster(
            lat=settings.pv_latitude,
            lon=settings.pv_longitude,
            rated_power=100000,
        )
        self.load = LoadForecaster()
        self._running = False

    async def run_loop(self):
        self._running = True
        interval = settings.forecast_interval
        print(f"[Forecast] 启动, 间隔 {interval}s")
        while self._running:
            try:
                now = datetime.now()
                # 光伏日前预测
                pv_day = self.pv.forecast(now, hours=24)
                self.pv.save_forecast(pv_day, "pv")
                # 负荷日前预测
                load_day = self.load.forecast("pv_inverter_01", 24)
                for ld in load_day:
                    td_manager._exec(
                        f"INSERT INTO {td_manager.db}.forecast_load (ts, power_kw) "
                        f"VALUES ({ld['ts']}, {ld['power_kw']})"
                    )
                print(f"[Forecast] 预测完成: {len(pv_day)} PV + {len(load_day)} load")
            except Exception as e:
                print(f"[Forecast] 异常: {e}")
            await asyncio.sleep(interval)

    def trigger_now(self) -> dict:
        now = datetime.now()
        pv = self.pv.forecast(now, hours=24)
        self.pv.save_forecast(pv, "pv")
        return {"pv_points": len(pv), "ts": now.isoformat()}


forecast_service = ForecastService()
