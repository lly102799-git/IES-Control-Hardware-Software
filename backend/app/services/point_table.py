"""设备点表配置管理。从 JSON 加载 Modbus 寄存器映射。"""

import json
import os

POINT_TABLES_FILE = os.path.join(os.path.dirname(__file__), "point_tables.json")

_point_tables: dict = {}


def load() -> dict:
    global _point_tables
    with open(POINT_TABLES_FILE) as f:
        _point_tables = json.load(f)
    return _point_tables


def save(tables: dict):
    global _point_tables
    with open(POINT_TABLES_FILE, "w") as f:
        json.dump(tables, f, indent=2, ensure_ascii=False)
    _point_tables = tables


def get_all() -> dict:
    return _point_tables


def get_device(device_id: str) -> dict | None:
    return _point_tables.get(device_id)
