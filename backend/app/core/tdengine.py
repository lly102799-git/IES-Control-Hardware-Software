"""TDengine REST API 封装 (taosAdapter)。

端点: POST /rest/sql/{db}
要求: Content-Type: application/x-www-form-urlencoded
认证: HTTP Basic Auth (httpx 原生支持)
"""

import httpx

from .config import settings

TD_REST_PORT = 6041


class TDEngineManager:
    def __init__(self):
        self._base_url = f"http://{settings.td_host}:{TD_REST_PORT}"
        self._client: httpx.Client | None = None
        self.db = settings.td_database

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            # trust_env=False: 忽略 macOS 系统代理，防止请求被代理软件拦截返回 502
            # http1=True: Gin 框架不兼容 HTTP/2
            self._client = httpx.Client(timeout=10, http1=True, trust_env=False)
        return self._client

    def _exec(self, sql: str) -> dict:
        """执行 SQL 并返回响应 JSON。
        匹配 curl 请求头以保持与 Gin 框架的兼容性。
        """
        resp = self.client.post(
            f"{self._base_url}/rest/sql/{self.db}",
            content=sql,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
            auth=(settings.td_user, settings.td_password),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(data.get("desc", "unknown"))
        return data

    def connect(self):
        """初始化数据库和超级表"""
        self._exec(f"CREATE DATABASE IF NOT EXISTS {self.db} KEEP 90d")
        self._exec(f"""
            CREATE STABLE IF NOT EXISTS {self.db}.telemetry (
                ts TIMESTAMP,
                val DOUBLE,
                quality INT
            ) TAGS (
                device_id BINARY(32),
                point_name BINARY(64)
            )
        """)
        # 删旧表重建（增加 cmd_duration 列）
        try:
            self._exec(f"DROP TABLE IF EXISTS {self.db}.command_log")
        except Exception:
            pass
        self._exec(f"""
            CREATE TABLE IF NOT EXISTS {self.db}.command_log (
                ts TIMESTAMP,
                device_id BINARY(32),
                cmd_register INT,
                cmd_values BINARY(128),
                cmd_mode INT,
                cmd_duration INT,
                success BOOL,
                message BINARY(256)
            )
        """)
        # 规则执行日志表
        self._exec(f"""
            CREATE TABLE IF NOT EXISTS {self.db}.rule_exec_log (
                ts TIMESTAMP,
                rule_name BINARY(64),
                device_id BINARY(32),
                action_mode INT,
                action_power INT,
                condition_snapshot BINARY(256),
                executed BOOL,
                reason BINARY(128)
            )
        """)
        # 光伏预测表
        self._exec(f"""
            CREATE TABLE IF NOT EXISTS {self.db}.forecast_pv (
                ts TIMESTAMP,
                power_kw DOUBLE,
                ghi DOUBLE
            )
        """)
        # 负荷预测表
        self._exec(f"""
            CREATE TABLE IF NOT EXISTS {self.db}.forecast_load (
                ts TIMESTAMP,
                power_kw DOUBLE
            )
        """)
        # 告警表
        self._exec(f"""
            CREATE TABLE IF NOT EXISTS {self.db}.alerts (
                ts TIMESTAMP,
                device_id BINARY(32),
                alert_name BINARY(64),
                severity BINARY(16),
                val DOUBLE,
                threshold DOUBLE,
                status BINARY(16),
                message BINARY(256)
            )
        """)

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def write_telemetry(self, records: list[dict]):
        """写入遥测数据。
        每条记录自动创建子表（IF NOT EXISTS），然后 INSERT。
        """
        if not records:
            return
        # 按子表分组批量 INSERT
        by_table: dict[str, list[dict]] = {}
        for r in records:
            table = f"{r['device_id']}_{r['point_name']}"
            by_table.setdefault(table, []).append(r)

        # 逐条建子表（REST API 不支持多语句）
        seen = set()
        for r in records:
            table = f"{r['device_id']}_{r['point_name']}"
            if table not in seen:
                seen.add(table)
                self._exec(
                    f"CREATE TABLE IF NOT EXISTS {table} "
                    f"USING telemetry TAGS('{r['device_id']}', '{r['point_name']}')"
                )

        # 批量 INSERT
        for table, recs in by_table.items():
            values_parts = []
            for r in recs:
                values_parts.append(f"({r['ts']}, {r['val']}, {r['quality']})")
            sql = f"INSERT INTO {table} VALUES {' '.join(values_parts)}"
            self._exec(sql)

    def query(self, sql: str) -> list[dict]:
        data = self._exec(sql)
        column_meta = data.get("column_meta", [])
        rows = data.get("data", [])
        if not column_meta or not rows:
            return []
        col_names = [c[0] for c in column_meta]
        return [dict(zip(col_names, row)) for row in rows]

    def record_command(self, device_id: str, cmd_register: int,
                       cmd_values: str, cmd_mode: int,
                       duration: int, success: bool, message: str):
        """记录控制指令到 command_log 表"""
        ts = int(__import__("time").time() * 1000)
        val_str = cmd_values.replace("'", "''")
        msg_str = message.replace("'", "''")
        self._exec(
            f"INSERT INTO {self.db}.command_log VALUES "
            f"({ts}, '{device_id}', {cmd_register}, '{val_str}', "
            f"{cmd_mode}, {duration}, {str(success).lower()}, '{msg_str}')"
        )

    async def health_check(self) -> bool:
        try:
            self._exec("SELECT 1")
            return True
        except Exception:
            return False


td_manager = TDEngineManager()
