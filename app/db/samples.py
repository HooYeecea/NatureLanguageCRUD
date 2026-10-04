"""Fetch a few sample rows per table for LLM context (never persisted)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import column, inspect, select, table, text
from sqlalchemy.engine import Connection, Engine

SENSITIVE_COL = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|salt)",
    re.I,
)
# Introspected identifiers only: no quotes, comments, spaces, or dotted fragments.
SAFE_IDENT = re.compile(r"^[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_$\u4e00-\u9fff]{0,127}$")
MAX_CELL = 80
MAX_TABLES = 16
MAX_COLUMNS = 16
DEFAULT_ROWS = 2
SAMPLE_TIMEOUT_SEC = 3


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


def is_safe_ident(name: Optional[str]) -> bool:
    if not name or not isinstance(name, str):
        return False
    if not SAFE_IDENT.match(name):
        return False
    lowered = name.lower()
    if "--" in name or "/*" in name or "*/" in name or ";" in name:
        return False
    if lowered in {"select", "from", "where", "union", "insert", "update", "delete"}:
        return False
    return True


def require_safe_ident(name: str) -> str:
    if not is_safe_ident(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def quote_ident(dialect: str, name: str) -> str:
    require_safe_ident(name)
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    if dialect == "sqlserver":
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


def _truncate(value: Any) -> Any:
    value = _jsonable(value)
    if isinstance(value, str) and len(value) > MAX_CELL:
        return value[:MAX_CELL] + "…"
    return value


def _allowed_columns(table_info: dict[str, Any], policy: Optional[dict[str, Any]]) -> list[str]:
    cols = []
    for c in table_info.get("columns") or []:
        name = c["name"] if isinstance(c, dict) else getattr(c, "name", None)
        if name:
            cols.append(name)
    if not policy:
        return cols
    allowed_tables = {t.get("table"): t for t in (policy.get("tables") or [])}
    tp = allowed_tables.get(table_info.get("name"))
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


def _set_sample_timeout(conn: Connection, dialect: str, seconds: int = SAMPLE_TIMEOUT_SEC) -> None:
    ms = int(max(1, min(seconds, 30)) * 1000)
    try:
        if dialect == "postgresql":
            conn.execute(text(f"SET LOCAL statement_timeout = {ms}"))
        elif dialect == "mysql":
            conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {ms}"))
        elif dialect == "sqlserver":
            conn.execute(text(f"SET LOCK_TIMEOUT {ms}"))
        elif dialect == "sqlite":
            conn.execute(text(f"PRAGMA busy_timeout = {ms}"))
    except Exception:  # noqa: BLE001
        return


def _introspected_table(
    inspector,
    dialect: str,
    name: str,
    schema_name: Optional[str],
) -> tuple[Optional[str], Optional[set[str]]]:
    """Return (schema, column_names) only if inspector currently sees this table."""
    schema = schema_name or None
    if schema is not None:
        if not is_safe_ident(schema):
            return None, None
        if dialect not in ("sqlite", "mysql"):
            try:
                schemas = set(inspector.get_schema_names() or [])
            except Exception:  # noqa: BLE001
                schemas = set()
            if schema not in schemas:
                return None, None
        if dialect in ("sqlite", "mysql"):
            schema = None
    try:
        tables = inspector.get_table_names(schema=schema)
    except Exception:  # noqa: BLE001
        return None, None
    if name not in tables:
        return None, None
    try:
        col_names = {c["name"] for c in inspector.get_columns(name, schema=schema)}
    except Exception:  # noqa: BLE001
        return None, None
    return schema, col_names


def fetch_table_samples(
    engine: Engine,
    dialect: str,
    tables: list[dict[str, Any]],
    policy: Optional[dict[str, Any]] = None,
    n: int = DEFAULT_ROWS,
) -> dict[str, list[dict[str, Any]]]:
    """Return {table_name: [row, ...]} with redacted secrets."""
    n = max(1, min(int(n), DEFAULT_ROWS))
    samples: dict[str, list[dict[str, Any]]] = {}
    inspector = inspect(engine)

    for table_info in tables[:MAX_TABLES]:
        name = table_info.get("name")
        if not is_safe_ident(name):
            continue
        assert isinstance(name, str)
        schema, real_cols = _introspected_table(
            inspector, dialect, name, table_info.get("schema_name")
        )
        if real_cols is None:
            samples[name] = []
            continue
        columns = [
            c
            for c in _allowed_columns(table_info, policy)
            if c in real_cols and is_safe_ident(c)
        ][:MAX_COLUMNS]
        if not columns:
            samples[name] = []
            continue
        tbl = table(name, *[column(c) for c in columns], schema=schema)
        stmt = select(*tbl.c).limit(n)
        try:
            with engine.connect() as conn:
                conn = conn.execution_options(timeout=SAMPLE_TIMEOUT_SEC)
                _set_sample_timeout(conn, dialect)
                result = conn.execute(stmt)
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
    for table_name, rows in samples.items():
        if not rows:
            lines.append(f"- {table_name}: (no sample rows)")
            continue
        preview = []
        for row in rows:
            parts = [f"{k}={v!r}" for k, v in row.items()]
            preview.append("{" + ", ".join(parts) + "}")
        lines.append(f"- {table_name}: " + " | ".join(preview))
    return "\n".join(lines)
