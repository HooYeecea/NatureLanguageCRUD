"""Schema introspection via SQLAlchemy Inspector."""

from __future__ import annotations

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
