"""
规则引擎服务。
周期性地根据系统状态评估规则条件，执行匹配的规则动作。
规则通过 Modbus 控制寄存器（50-54）下发给模拟设备。
"""

import asyncio
import json
import os
import time
from datetime import datetime

from ..core.config import settings
from ..core.tdengine import td_manager
from .collector import collector

RULES_FILE = os.path.join(os.path.dirname(__file__), "rules.json")


class RuleEngine:
    def __init__(self):
        self.rules: list[dict] = []
        self._running = False
        self._last_triggered: dict[str, float] = {}
        self.safety_only = False  # MILP 激活时仅评估安全规则 (priority >= 90)

    # ── 规则管理 ──────────────────────────────────────────

    def load_rules(self, path: str = RULES_FILE):
        with open(path) as f:
            self.rules = json.load(f)
        # 按优先级降序排列
        self.rules.sort(key=lambda r: r.get("priority", 0), reverse=True)
        print(f"[RuleEngine] 加载 {len(self.rules)} 条规则")

    def get_rules(self) -> list[dict]:
        return self.rules

    def save_rules(self, rules: list[dict], path: str = RULES_FILE):
        """保存规则到文件并重新加载"""
        rules.sort(key=lambda r: r.get("priority", 0), reverse=True)
        with open(path, "w") as f:
            json.dump(rules, f, indent=2, ensure_ascii=False)
        self.load_rules(path)

    # ── 状态采集 ──────────────────────────────────────────

    def collect_state(self) -> dict:
        latest = collector.get_latest()
        now = datetime.now()
        hour = now.hour + now.minute / 60

        pv = latest.get("pv_inverter_01", {})
        bat = latest.get("battery_pcs_01", {})

        return {
            "hour": hour,
            "pv_power": pv.get("active_power", 0),
            "pv_status": int(pv.get("status", 0)),
            "battery_power": bat.get("active_power", 0),
            "soc": bat.get("soc", 50),
            "status": int(bat.get("status", 0)),
            "temp_battery": bat.get("temp_battery", 25),
        }

    # ── 条件评估 ──────────────────────────────────────────

    def _eval_condition(self, cond: dict, state: dict) -> bool:
        field = cond["field"]
        op = cond["op"]
        val = state.get(field, 0)

        if op == "lt":
            return val < cond["value"]
        elif op == "gt":
            return val > cond["value"]
        elif op == "lte":
            return val <= cond["value"]
        elif op == "gte":
            return val >= cond["value"]
        elif op == "eq":
            return val == cond["value"]
        elif op == "neq":
            return val != cond["value"]
        elif op == "between":
            return cond["min"] <= val <= cond["max"]
        elif op == "not_between":
            return val < cond["min"] or val > cond["max"]
        return False

    def evaluate(self, state: dict) -> list[dict]:
        matched = []
        now = time.time()
        for rule in self.rules:
            if not rule.get("enabled", True):
                continue
            # MILP 激活时仅评估安全规则 (priority >= 90)
            if self.safety_only and rule.get("priority", 0) < 90:
                continue
            # 冷却时间检查
            cooldown = rule.get("cooldown_seconds", 0)
            name = rule["name"]
            if name in self._last_triggered:
                if now - self._last_triggered[name] < cooldown:
                    continue

            # 评估所有条件 (AND 逻辑)
            conditions = rule.get("conditions", [])
            if not conditions:
                continue
            if all(self._eval_condition(c, state) for c in conditions):
                matched.append(rule)
        return matched

    # ── 动作执行 ──────────────────────────────────────────

    async def execute_actions(self, matched_rules: list[dict], state: dict):
        now = time.time()
        for rule in matched_rules:
            name = rule["name"]
            for action in rule.get("actions", []):
                device_id = action["device"]
                cmd = {
                    "register": 50,
                    "values": [action.get("power_setpoint", 0)],
                    "mode": action.get("mode", 0),
                    "duration": action.get("duration", 0),
                }
                result = await collector.write_device_command(device_id, cmd)
                self._log_execution(
                    name, device_id, action, result["success"], result["message"], state
                )
            self._last_triggered[name] = now

    def _log_execution(self, rule_name: str, device_id: str, action: dict,
                       success: bool, message: str, state: dict):
        """写入规则执行日志到 TDengine"""
        try:
            snapshot = f"soc={state['soc']:.1f},pw={state['battery_power']:.0f}"
            td_manager._exec(
                f"INSERT INTO {td_manager.db}.rule_exec_log VALUES "
                f"({int(time.time()*1000)}, '{rule_name}', '{device_id}', "
                f"{action.get('mode', 0)}, {action.get('power_setpoint', 0)}, "
                f"'{snapshot}', {str(success).lower()}, '{message[:127]}')"
            )
        except Exception as e:
            print(f"[RuleEngine] 日志写入失败: {e}")

    # ── 调度循环 ──────────────────────────────────────────

    async def run_loop(self):
        self.load_rules()
        self._running = True
        interval = getattr(settings, "rule_engine_interval", 30)
        print(f"[RuleEngine] 启动, 间隔 {interval}s")
        while self._running:
            started = time.time()
            try:
                state = self.collect_state()
                matched = self.evaluate(state)
                if matched:
                    names = [r["name"] for r in matched]
                    print(f"[RuleEngine] 匹配规则: {names}")
                    await self.execute_actions(matched, state)
            except Exception as e:
                print(f"[RuleEngine] 异常: {e}")
            elapsed = time.time() - started
            await asyncio.sleep(max(1, interval - elapsed))

    def stop(self):
        self._running = False


rule_engine = RuleEngine()
