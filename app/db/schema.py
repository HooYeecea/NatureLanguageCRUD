"""Schema introspection via SQLAlchemy Inspector."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from sqlalchemy.engine import Engine
from sqlalchemy import inspect

from app.models import ColumnInfo, ForeignKeyInfo, SchemaOverview, TableInfo


def _schema_names(inspector, dialect: str) -> list[Optional[str]]:
    """Return schema names to scan. None means default/unqualified."""
    if dialect == "sqlite":
        return [None]
    if dialect == "mysql":
        # MySQL: database == schema; inspector uses default
        return [None]
    if dialect in ("postgresql", "postgres"):
        names = inspector.get_schema_names()
        return [s for s in names if s not in ("pg_catalog", "information_schema")]
    if dialect in ("mssql", "microsoft", "sqlserver"):
        names = inspector.get_schema_names()
        return [s for s in names if s not in ("sys", "INFORMATION_SCHEMA", "guest")]
    return [None]


def introspect_schema(engine: Engine, connection_id: str, dialect: str) -> SchemaOverview:
    inspector = inspect(engine)
    sa_dialect = engine.dialect.name
    tables: list[TableInfo] = []

    for schema_name in _schema_names(inspector, sa_dialect):
        try:
            table_names = inspector.get_table_names(schema=schema_name)
        except Exception:  # noqa: BLE001
            table_names = inspector.get_table_names()
            schema_name = None

        for table_name in sorted(table_names):
            tables.append(
                _load_table(inspector, table_name, schema_name)
            )

    return SchemaOverview(
        connection_id=connection_id,
        dialect=dialect,  # type: ignore[arg-type]
        tables=tables,
    )


def introspect_table(
    engine: Engine,
    table_name: str,
    schema_name: Optional[str] = None,
) -> Optional[TableInfo]:
    inspector = inspect(engine)
    names = inspector.get_table_names(schema=schema_name)
    if table_name not in names:
        # Try without schema / search all schemas
        if schema_name is None:
            for sch in _schema_names(inspector, engine.dialect.name):
                if table_name in inspector.get_table_names(schema=sch):
                    return _load_table(inspector, table_name, sch)
        return None
    return _load_table(inspector, table_name, schema_name)


def _load_table(inspector, table_name: str, schema_name: Optional[str]) -> TableInfo:
    columns_raw = inspector.get_columns(table_name, schema=schema_name)
    pk = inspector.get_pk_constraint(table_name, schema=schema_name) or {}
    pk_cols = list(pk.get("constrained_columns") or [])

    columns = [
        ColumnInfo(
            name=col["name"],
            type=str(col.get("type") or ""),
            nullable=bool(col.get("nullable", True)),
            default=str(col["default"]) if col.get("default") is not None else None,
            primary_key=col["name"] in pk_cols,
        )
        for col in columns_raw
    ]

    fks_raw = inspector.get_foreign_keys(table_name, schema=schema_name) or []
    foreign_keys = [
        ForeignKeyInfo(
            constrained_columns=list(fk.get("constrained_columns") or []),
            referred_table=fk.get("referred_table") or "",
            referred_columns=list(fk.get("referred_columns") or []),
        )
        for fk in fks_raw
    ]

    return TableInfo(
        name=table_name,
        schema_name=schema_name,
        columns=columns,
        primary_key=pk_cols,
        foreign_keys=foreign_keys,
    )


def get_engine_cfg(connection: dict[str, Any]) -> dict[str, Any]:
    """Normalize stored connection dict for create_db_engine."""
    return {
        "dialect": connection["dialect"],
        "host": connection.get("host"),
        "port": connection.get("port"),
        "database": connection.get("database"),
        "username": connection.get("username"),
        "password": connection.get("password") or "",
        "options": connection.get("options") or {},
    }


def schema_fingerprint(tables: list[dict[str, Any]]) -> str:
    """
    Stable fingerprint of selected table structures (columns/PK/FK).
    Used to detect drift vs a cached analysis.
    """
    parts: list[dict[str, Any]] = []
    for t in tables:
        name = t.get("name") or t.get("table") or ""
        schema = t.get("schema_name")
        cols = []
        for c in t.get("columns") or []:
            if isinstance(c, dict):
                cols.append(
                    {
                        "name": c.get("name"),
                        "type": str(c.get("type") or ""),
                        "nullable": bool(c.get("nullable", True)),
                        "primary_key": bool(c.get("primary_key", False)),
                    }
                )
            else:
                cols.append(
                    {
                        "name": getattr(c, "name", None),
                        "type": str(getattr(c, "type", "") or ""),
                        "nullable": bool(getattr(c, "nullable", True)),
                        "primary_key": bool(getattr(c, "primary_key", False)),
                    }
                )
        cols.sort(key=lambda x: str(x.get("name") or ""))
        fks = []
        for fk in t.get("foreign_keys") or []:
            if isinstance(fk, dict):
                fks.append(
                    {
                        "constrained_columns": list(fk.get("constrained_columns") or []),
                        "referred_table": fk.get("referred_table"),
                        "referred_columns": list(fk.get("referred_columns") or []),
                    }
                )
            else:
                fks.append(
                    {
                        "constrained_columns": list(getattr(fk, "constrained_columns", []) or []),
                        "referred_table": getattr(fk, "referred_table", None),
                        "referred_columns": list(getattr(fk, "referred_columns", []) or []),
                    }
                )
        fks.sort(key=lambda x: (str(x.get("referred_table") or ""), str(x.get("constrained_columns"))))
        parts.append(
            {
                "schema_name": schema,
                "name": name,
                "columns": cols,
                "primary_key": list(t.get("primary_key") or []),
                "foreign_keys": fks,
            }
        )
    parts.sort(key=lambda x: f"{x.get('schema_name') or ''}.{x.get('name') or ''}".lower())
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
