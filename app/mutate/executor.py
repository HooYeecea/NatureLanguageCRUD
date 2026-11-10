"""Execute mutation plans and gather preview / read-back stats."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text, Table, MetaData, select, and_
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
    for i, row in enumerate(result.fetchmany(limit)):
        if i >= limit:
            break
        rows.append({k: _jsonable(v) for k, v in zip(keys, row)})
    return rows


def preview_plan(engine: Engine, plan: MutatePlan, *, max_rows: int) -> dict[str, Any]:
    """
    Preview mutation impact.

    Only count and sample. DML runs exclusively during confirmation because
    triggers and non-transactional side effects cannot reliably be rolled back.
    """
    if plan.operation == "insert":
        return {
            "affected_count": 1,
            "sample_rows": [plan.values or {}],
            "blocked": False,
            "block_reason": None,
        }

    with engine.connect() as conn:
        trans = conn.begin()
        try:
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

            # Never execute DML in previews: triggers may have external side effects.

            return {
                "affected_count": affected,
                "sample_rows": sample_rows,
                "blocked": False,
                "block_reason": None,
            }
        finally:
            if trans.is_active:
                trans.rollback()


def execute_plan(engine: Engine, plan: MutatePlan, *, max_rows: int = 100,
                 ack_threshold: int = 10, ack_large_impact: bool = False,
                 snapshot_writer=None) -> dict[str, Any]:
    """Lock and bound the complete target set; commit only after all checks pass."""
    from app.query.sql_guard import SqlGuardError
    import sqlglot
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            conn = conn.execution_options(isolation_level="SERIALIZABLE")
            conn.begin()
        try:
            tbl = Table(plan.table, MetaData(), schema=plan.schema_name, autoload_with=conn)
            if plan.operation == "insert":
                result = conn.execute(tbl.insert().values(**(plan.values or {})))
                output = {"rowcount": 1, "lastrowid": getattr(result, "lastrowid", None), "before_rows": []}
            else:
                pk = [c.name for c in tbl.primary_key]
                if not pk:
                    raise SqlGuardError("UPDATE/DELETE requires a table primary key")
                dialect = {"postgresql": "postgres", "mssql": "tsql"}.get(engine.dialect.name, engine.dialect.name)
                where = sqlglot.parse_one(plan.count_sql, read=dialect).args.get("where")
                stmt = select(tbl).limit(max_rows + 1)
                if where is not None:
                    stmt = stmt.where(text(where.this.sql(dialect=dialect)))
                if engine.dialect.name == "mssql":
                    stmt = stmt.with_hint(tbl, "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql")
                elif engine.dialect.name != "sqlite":
                    stmt = stmt.with_for_update()
                before_rows = [dict(row) for row in conn.execute(stmt).mappings()]
                count = len(before_rows)
                if count > max_rows:
                    raise SqlGuardError("Affected rows exceed max_rows_per_mutation")
                if count >= ack_threshold and not ack_large_impact:
                    raise SqlGuardError("Live affected rows require large-impact acknowledgement")
                for row in before_rows:
                    predicate = and_(*(tbl.c[k] == row[k] for k in pk))
                    write = tbl.delete().where(predicate) if plan.operation == "delete" else tbl.update().where(predicate).values(**(plan.set_values or {}))
                    result = conn.execute(write)
                    if result.rowcount not in (-1, 0, 1):
                        raise SqlGuardError("Unexpected affected row count; transaction rolled back")
                output = {"rowcount": count, "lastrowid": None, "before_rows": before_rows}
                if plan.operation == "delete" and count and snapshot_writer:
                    output["undo_id"] = snapshot_writer(before_rows, pk)
            conn.commit()
            return output
        except BaseException:
            conn.rollback()
            raise


def restore_rows(engine: Engine, table_name: str, schema_name, rows: list[dict]) -> None:
    """All rows are restored, or none are; no partially committed undo."""
    with engine.begin() as conn:
        tbl = Table(table_name, MetaData(), schema=schema_name, autoload_with=conn)
        for row in rows:
            conn.execute(tbl.insert().values(**row))


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
