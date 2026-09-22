"""Execute mutation plans and gather preview stats."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.mutate.builder import MutatePlan
from app.query.executor import _jsonable
from app.query.sql_guard import SqlGuardError


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
            result = conn.execute(text(plan.preview_sql))
            keys = list(result.keys())
            sample_rows = [
                {k: _jsonable(v) for k, v in zip(keys, row)}
                for row in result.fetchall()
            ]

    return {
        "affected_count": affected,
        "sample_rows": sample_rows,
        "blocked": False,
        "block_reason": None,
    }


def execute_plan(engine: Engine, plan: MutatePlan) -> dict[str, Any]:
    with engine.begin() as conn:
        if plan.operation == "insert":
            result = conn.execute(text(plan.sql))
            return {
                "rowcount": result.rowcount if result.rowcount is not None else 1,
                "lastrowid": getattr(result, "lastrowid", None),
            }

        # Re-check count inside transaction for update/delete
        count_row = conn.execute(text(plan.count_sql)).fetchone()
        affected = int(count_row[0]) if count_row is not None else 0
        result = conn.execute(text(plan.sql))
        return {
            "rowcount": result.rowcount if result.rowcount is not None else affected,
            "lastrowid": None,
        }
