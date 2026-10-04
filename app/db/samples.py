"""Fetch a few sample rows per table for LLM context (never persisted)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

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

SENSITIVE_COL = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|salt)",
    re.I,
)
MAX_CELL = 80
MAX_TABLES = 24
DEFAULT_ROWS = 3


def quote_ident(dialect: str, name: str) -> str:
    if dialect == "mysql":
        return "`" + str(name).replace("`", "``") + "`"
    if dialect == "sqlserver":
        return "[" + str(name).replace("]", "]]") + "]"
    return '"' + str(name).replace('"', '""') + '"'


def qualify_table(dialect: str, name: str, schema_name: Optional[str] = None) -> str:
    q = quote_ident(dialect, name)
    if schema_name:
        return f"{quote_ident(dialect, schema_name)}.{q}"
    return q


def _truncate(value: Any) -> Any:
    value = _jsonable(value)
    if isinstance(value, str) and len(value) > MAX_CELL:
        return value[:MAX_CELL] + "…"
    return value


def _allowed_columns(table: dict[str, Any], policy: Optional[dict[str, Any]]) -> list[str]:
    cols = []
    for c in table.get("columns") or []:
        name = c["name"] if isinstance(c, dict) else getattr(c, "name", None)
        if name:
            cols.append(name)
    if not policy:
        return cols
    allowed_tables = {t.get("table"): t for t in (policy.get("tables") or [])}
    tp = allowed_tables.get(table.get("name"))
    if not tp:
        return cols
    denied = set(tp.get("denied_columns") or [])
    allow = tp.get("allowed_columns")
    out = []
    for name in cols:
        if name in denied:
            continue
        if allow is not None and name not in allow:
            continue
        out.append(name)
    return out


def fetch_table_samples(
    engine: Engine,
    dialect: str,
    tables: list[dict[str, Any]],
    policy: Optional[dict[str, Any]] = None,
    n: int = DEFAULT_ROWS,
) -> dict[str, list[dict[str, Any]]]:
    """Return {table_name: [row, ...]} with redacted secrets."""
    samples: dict[str, list[dict[str, Any]]] = {}
    for table in tables[:MAX_TABLES]:
        name = table.get("name")
        if not name:
            continue
        columns = _allowed_columns(table, policy)
        if not columns:
            continue
        quoted_cols = ", ".join(quote_ident(dialect, c) for c in columns)
        qtable = qualify_table(dialect, name, table.get("schema_name"))
        if dialect == "sqlserver":
            sql = f"SELECT TOP {int(n)} {quoted_cols} FROM {qtable}"
        else:
            sql = f"SELECT {quoted_cols} FROM {qtable} LIMIT {int(n)}"
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                keys = list(result.keys())
                rows = []
                for raw in result.fetchall():
                    row = {}
                    for k, v in zip(keys, raw):
                        if SENSITIVE_COL.search(str(k)):
                            row[k] = "[redacted]"
                        else:
                            row[k] = _truncate(v)
                    rows.append(row)
            samples[name] = rows
        except Exception:  # noqa: BLE001
            samples[name] = []
    return samples


def format_samples_text(samples: dict[str, list[dict[str, Any]]]) -> str:
    if not samples:
        return ""
    lines = [
        "## Sample rows (use these to learn real values, codes, and labels; do not dump whole tables)"
    ]
    for table, rows in samples.items():
        if not rows:
            lines.append(f"- {table}: (no sample rows)")
            continue
        preview = []
        for row in rows:
            parts = [f"{k}={v!r}" for k, v in row.items()]
            preview.append("{" + ", ".join(parts) + "}")
        lines.append(f"- {table}: " + " | ".join(preview))
    return "\n".join(lines)
