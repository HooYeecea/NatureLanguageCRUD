"""Execute guarded SELECT queries."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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


def execute_select(engine: Engine, sql: str) -> dict[str, Any]:
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        keys = list(result.keys())
        rows = [
            {k: _jsonable(v) for k, v in zip(keys, row)}
            for row in result.fetchall()
        ]
    return {"columns": keys, "rows": rows, "row_count": len(rows)}
