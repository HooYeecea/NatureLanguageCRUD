"""Execute mutation plans and gather preview / read-back stats."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlglot import exp

from app.mutate.builder import MutatePlan, _apply_filters, _dialect, _literal, _table_exp
from app.query.executor import _jsonable


def _fetch_dicts(conn, sql: str, limit: int = 50) -> list[dict[str, Any]]:
    if not sql:
        return []
    result = conn.execute(text(sql))
    keys = list(result.keys())
    rows = []
    for i, row in enumerate(result.fetchall()):
        if i >= limit:
            break
        rows.append({k: _jsonable(v) for k, v in zip(keys, row)})
    return rows


def preview_plan(engine: Engine, plan: MutatePlan, *, max_rows: int) -> dict[str, Any]:
    if plan.operation == "insert":
        return {
            "affected_count": 1,
            "sample_rows": [plan.values or {}],
            "blocked": False,
            "block_reason": None,
        }

    with engine.connect() as conn:
        count_row = conn.execute(text(plan.count_sql)).fetchone()
        affected = int(count_row[0]) if count_row is not None else 0

        if affected > max_rows:
            return {
                "affected_count": affected,
                "sample_rows": [],
                "blocked": True,
                "block_reason": (
                    f"Affected rows ({affected}) exceed max_rows_per_mutation ({max_rows})"
                ),
            }

        sample_rows: list[dict[str, Any]] = []
        if plan.preview_sql and affected > 0:
            sample_rows = _fetch_dicts(conn, plan.preview_sql)

    return {
        "affected_count": affected,
        "sample_rows": sample_rows,
        "blocked": False,
        "block_reason": None,
    }


def execute_plan(engine: Engine, plan: MutatePlan) -> dict[str, Any]:
    with engine.begin() as conn:
        before_rows: list[dict[str, Any]] = []
        if plan.operation in ("update", "delete") and plan.preview_sql:
            before_rows = _fetch_dicts(conn, plan.preview_sql)

        if plan.operation == "insert":
            result = conn.execute(text(plan.sql))
            return {
                "rowcount": result.rowcount if result.rowcount is not None else 1,
                "lastrowid": getattr(result, "lastrowid", None),
                "before_rows": [],
            }

        count_row = conn.execute(text(plan.count_sql)).fetchone()
        affected = int(count_row[0]) if count_row is not None else 0
        result = conn.execute(text(plan.sql))
        return {
            "rowcount": result.rowcount if result.rowcount is not None else affected,
            "lastrowid": None,
            "before_rows": before_rows,
        }


def _insert_readback_sql(plan: MutatePlan, dialect: str, lastrowid: Any) -> Optional[str]:
    read_dialect = _dialect(dialect)
    table = _table_exp(plan.table, plan.schema_name)
    values = plan.values or {}

    if lastrowid is not None:
        pk_candidates = [k for k in values if str(k).lower() == "id" or str(k).lower().endswith("_id")]
        if not pk_candidates:
            pk_candidates = ["id"]
        pk = pk_candidates[0]
        q = (
            exp.select(exp.Star())
            .from_(table)
            .where(exp.EQ(this=exp.column(pk), expression=_literal(lastrowid)))
            .limit(5)
        )
        return q.sql(dialect=read_dialect)

    if not values:
        return None
    q = exp.select(exp.Star()).from_(table)
    filters = [{"column": k, "op": "=", "value": v} for k, v in values.items()]
    q = _apply_filters(q, filters).limit(5)
    return q.sql(dialect=read_dialect)


def readback_rows(
    engine: Engine,
    plan: MutatePlan,
    *,
    dialect: str,
    lastrowid: Any = None,
    before_rows: Optional[list[dict[str, Any]]] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return rows that show the effect of the mutation."""
    if plan.operation == "delete":
        return (before_rows or [])[:limit]

    try:
        with engine.connect() as conn:
            if plan.operation == "update" and plan.preview_sql:
                return _fetch_dicts(conn, plan.preview_sql, limit=limit)
            if plan.operation == "insert":
                sql = _insert_readback_sql(plan, dialect, lastrowid)
                if sql:
                    rows = _fetch_dicts(conn, sql, limit=limit)
                    if rows:
                        return rows
                # Fallback: show intended payload
                return [plan.values or {}] if plan.values else []
    except Exception:  # noqa: BLE001
        if plan.operation == "insert" and plan.values:
            return [plan.values]
        return (before_rows or [])[:limit]
    return []
