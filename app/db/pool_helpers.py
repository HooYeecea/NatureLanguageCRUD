"""Helpers for pooled engines + primary-key lookup."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.engine import Engine

from app.db.schema import introspect_table


def table_primary_key(
    engine: Engine,
    table: str,
    schema_name: Optional[str] = None,
) -> list[str]:
    info = introspect_table(engine, table, schema_name)
    if not info:
        return []
    return list(info.primary_key or [])


def primary_key_map(
    engine: Engine,
    tables: list[dict[str, Any]],
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for t in tables:
        name = t.get("name") or t.get("table")
        if not name:
            continue
        out[name] = table_primary_key(engine, name, t.get("schema_name"))
    return out
