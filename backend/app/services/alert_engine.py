"""
告警引擎。
周期性评估告警规则，生成告警记录，支持确认和自动恢复。
"""

import asyncio
import json
import os
import time
from datetime import datetime

from ..core.config import settings
from ..core.tdengine import td_manager
from .collector import collector

ALERTS_FILE = os.path.join(os.path.dirname(__file__), "alerts.json")


class AlertEngine:
    def __init__(self):
        self.rules: list[dict] = []
        self._running = False
        self._last_fired: dict[str, float] = {}   # rule_name → last fire time
        self._active_alerts: dict[str, bool] = {}  # rule_name → still active
        self._last_data_ts: dict[str, float] = {}  # device_id → last data timestamp

    def load_rules(self, path: str = ALERTS_FILE):
        with open(path) as f:
            self.rules = json.load(f)
        print(f"[AlertEngine] 加载 {len(self.rules)} 条告警规则")

    def get_rules(self) -> list[dict]:
        return self.rules

    def save_rules(self, rules: list[dict], path: str = ALERTS_FILE):
        """保存告警规则到文件并重新加载"""
        with open(path, "w") as f:
            json.dump(rules, f, indent=2, ensure_ascii=False)
        self.load_rules(path)

    # ── 状态采集 ──────────────────────────────────────────

    def _collect_telemetry(self) -> dict[str, dict]:
        """从 collector 获取当前遥测，按设备组织"""
        latest = collector.get_latest()
        now = time.time()
        result: dict[str, dict] = {}
        for dev_id, data in latest.items():
            result[dev_id] = dict(data)
            result[dev_id]["_comm_loss"] = 0
            self._last_data_ts[dev_id] = now
        # 通信检测：超过采集间隔×3 无数据 → 通信中断
        timeout = settings.collect_interval * 3
        for dev_id in ["pv_inverter_01", "battery_pcs_01"]:
            if dev_id not in result:
                last = self._last_data_ts.get(dev_id, 0)
                result.setdefault(dev_id, {})["_comm_loss"] = 1 if (now - last > timeout) else 0
        return result

    # ── 规则评估 ──────────────────────────────────────────

    def evaluate(self, telemetry: dict[str, dict]) -> list[dict]:
        now = time.time()
        alerts = []
        for rule in self.rules:
            name = rule["name"]
            dev_filter = rule["device_id"]
            cooldown = rule.get("cooldown_seconds", 300)

            # 冷却: 同规则在冷却期内不重复产生
            if name in self._last_fired:
                if now - self._last_fired[name] < cooldown:
                    continue

            for dev_id, data in telemetry.items():
                if dev_filter != "*" and dev_id != dev_filter:
                    continue
                point = rule["point"]
                val = data.get(point)
                if val is None:
                    continue

                if self._eval_threshold(val, rule["op"], rule["threshold"]):
                    msg = rule["message"].replace("{value}", str(round(val, 1)))
                    alerts.append({
                        "device_id": dev_id,
                        "alert_name": name,
                        "severity": rule["severity"],
                        "value": round(val, 3),
                        "threshold": rule["threshold"],
                        "message": msg,
                        "status": "active",
                    })
                    self._last_fired[name] = now
                    self._active_alerts[name] = True
                    break  # 每条规则只匹配第一个设备

            # 自动恢复：之前活跃的告警，当前不再匹配
            if name in self._active_alerts and self._active_alerts[name]:
                still_active = False
                for dev_id, data in telemetry.items():
                    if dev_filter != "*" and dev_id != dev_filter:
                        continue
                    val = data.get(rule["point"])
                    if val is not None and self._eval_threshold(val, rule["op"], rule["threshold"]):
                        still_active = True
                        break
                if not still_active:
                    self._active_alerts[name] = False
                    alerts.append({
                        "device_id": dev_filter if dev_filter != "*" else "system",
                        "alert_name": name,
                        "severity": rule["severity"],
                        "value": 0,
                        "threshold": rule["threshold"],
                        "message": f"告警已自动恢复",
                        "status": "resolved",
                    })
                    self._last_fired.pop(name, None)

        return alerts

    @staticmethod
    def _eval_threshold(val: float, op: str, threshold: float) -> bool:
        if op == "lt": return val < threshold
        if op == "gt": return val > threshold
        if op == "lte": return val <= threshold
        if op == "gte": return val >= threshold
        if op == "eq": return val == threshold
        if op == "neq": return val != threshold
        return False

    # ── 告警存储 ──────────────────────────────────────────

    def _save_alerts(self, alerts: list[dict]):
        if not alerts:
            return
        ts = int(time.time() * 1000)
        for a in alerts:
            try:
                td_manager._exec(
                    f"INSERT INTO {td_manager.db}.alerts "
                    f"(ts, device_id, alert_name, severity, val, threshold, status, message) "
                    f"VALUES ({ts}, '{a['device_id']}', '{a['alert_name']}', "
                    f"'{a['severity']}', {a['value']}, {a['threshold']}, "
                    f"'{a['status']}', '{a['message'][:127]}')"
                )
            except Exception as e:
                print(f"[AlertEngine] 存储失败: {e}")

    # ── 确认 ──────────────────────────────────────────────

    def acknowledge(self, alert_ts: str, device_id: str) -> bool:
        """确认告警"""
        try:
            td_manager._exec(
                f"UPDATE {td_manager.db}.alerts "
                f"SET status = 'acked' "
                f"WHERE ts = {alert_ts} AND device_id = '{device_id}'"
            )
            return True
        except Exception as e:
            print(f"[AlertEngine] 确认失败: {e}")
            return False

    # ── 查询 ──────────────────────────────────────────────

    def query(self, severity: str = "", device_id: str = "",
              status: str = "", limit: int = 100) -> list[dict]:
        conditions = []
        if severity:
            conditions.append(f"severity = '{severity}'")
        if device_id:
            conditions.append(f"device_id = '{device_id}'")
        if status:
            conditions.append(f"status = '{status}'")
        where = " AND ".join(conditions) if conditions else "1=1"
        try:
            return td_manager.query(f"""
                SELECT ts, device_id, alert_name, severity, val, threshold, status, message
                FROM {td_manager.db}.alerts
                WHERE {where}
                ORDER BY ts DESC
                LIMIT {limit}
            """)
        except Exception:
            return []

    # ── 调度循环 ──────────────────────────────────────────

    async def run_loop(self):
        self.load_rules()
        self._running = True
        interval = 15
        print(f"[AlertEngine] 启动, 间隔 {interval}s")
        while self._running:
            try:
                telemetry = self._collect_telemetry()
                alerts = self.evaluate(telemetry)
                self._save_alerts(alerts)
                if alerts:
                    names = [a["alert_name"] for a in alerts]
                    print(f"[AlertEngine] 产生告警: {names}")
            except Exception as e:
                print(f"[AlertEngine] 异常: {e}")
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False


alert_engine = AlertEngine()
