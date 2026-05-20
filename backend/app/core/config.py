"""应用配置"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "IES Control Terminal"
    version: str = "0.1.0"
    debug: bool = True

    # TDengine
    td_host: str = "localhost"
    td_port: int = 6030
    td_user: str = "root"
    td_password: str = "taosdata"
    td_database: str = "ies"

    # Modbus 模拟设备
    modbus_host: str = "localhost"
    modbus_port: int = 5020
    modbus_pv_slave: int = 1
    modbus_battery_slave: int = 2
    modbus_chp_slave: int = 3
    modbus_hp_slave: int = 4
    modbus_ts_slave: int = 5

    # 采集间隔 (秒)
    collect_interval: int = 5

    # 算法引擎
    rule_engine_interval: int = 30      # 规则引擎评估间隔 (秒)
    forecast_interval: int = 900        # 预测更新间隔 (秒), 默认 15 分钟
    pv_latitude: float = 39.9           # 光伏站纬度
    pv_longitude: float = 116.4         # 光伏站经度

    model_config = {"env_prefix": "IES_", "env_file": ".env"}


settings = Settings()
