"""Execute guarded SELECT queries."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def execute_select(engine: Engine, sql: str, *, max_rows: int = 100_000, timeout_seconds: int = 5) -> dict[str, Any]:
    with engine.connect() as conn:
        dialect = engine.dialect.name
        raw = conn.connection.driver_connection
        old_timeout = None
        old_readonly = None
        try:
            if dialect == "sqlite":
                old_readonly = conn.exec_driver_sql("PRAGMA query_only").scalar()
                conn.exec_driver_sql("PRAGMA query_only=ON")
                deadline = time.monotonic() + timeout_seconds
                raw.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            elif dialect == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_seconds * 1000)}")
            elif dialect == "mysql":
                old_timeout = conn.exec_driver_sql("SELECT @@SESSION.MAX_EXECUTION_TIME").scalar()
                conn.rollback()
                conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_seconds * 1000)}")
                conn.exec_driver_sql("START TRANSACTION READ ONLY")
            elif dialect == "mssql":
                old_timeout = raw.timeout
                raw.timeout = timeout_seconds
            result = conn.execute(text(sql))
            keys = list(result.keys())
            fetched = result.fetchmany(max_rows + 1)
            if len(fetched) > max_rows:
                raise ValueError("Result exceeds the enforced row limit")
            rows = [{k: _jsonable(v) for k, v in zip(keys, row)} for row in fetched]
        finally:
            conn.rollback()
            if dialect == "sqlite" and old_readonly is not None:
                raw.set_progress_handler(None, 0)
                conn.exec_driver_sql(f"PRAGMA query_only={int(old_readonly)}")
            elif dialect == "mysql" and old_timeout is not None:
                conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {int(old_timeout)}")
            elif dialect == "mssql" and old_timeout is not None:
                raw.timeout = old_timeout
    return {"columns": keys, "rows": rows, "row_count": len(rows)}
