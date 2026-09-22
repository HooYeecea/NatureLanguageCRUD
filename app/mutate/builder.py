"""Build restricted INSERT/UPDATE/DELETE plans (no free-form DML)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from sqlglot import exp

from app.policy import check_access
from app.query.sql_guard import DIALECT_MAP, SqlGuardError

MutationOp = Literal["insert", "update", "delete"]

ALLOWED_FILTER_OPS = {
    "=": exp.EQ,
    "!=": exp.NEQ,
    "<>": exp.NEQ,
    ">": exp.GT,
    ">=": exp.GTE,
    "<": exp.LT,
    "<=": exp.LTE,
    "like": exp.Like,
    "ilike": exp.ILike,
}


@dataclass
class MutatePlan:
    operation: MutationOp
    table: str
    schema_name: Optional[str]
    sql: str
    count_sql: str
    preview_sql: str
    filters: list[dict[str, Any]] = field(default_factory=list)
    values: Optional[dict[str, Any]] = None
    set_values: Optional[dict[str, Any]] = None


def _dialect(dialect: str) -> str:
    try:
        return DIALECT_MAP[dialect]
    except KeyError as exc:
        raise SqlGuardError(f"Unsupported dialect: {dialect}") from exc


def _table_exp(table: str, schema_name: Optional[str]) -> exp.Table:
    return exp.Table(
        this=exp.to_identifier(table),
        db=exp.to_identifier(schema_name) if schema_name else None,
    )


def _literal(value: Any) -> exp.Expression:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int) and not isinstance(value, bool):
        return exp.Literal.number(value)
    if isinstance(value, float):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _apply_filters(query: exp.Select, filters: list[dict[str, Any]]) -> exp.Select:
    for f in filters:
        col = f.get("column")
        op = (f.get("op") or "=").lower()
        value = f.get("value")
        if not col:
            raise SqlGuardError("Filter requires column")
        op_cls = ALLOWED_FILTER_OPS.get(op)
        if not op_cls:
            raise SqlGuardError(f"Unsupported filter op: {op}")
        query = query.where(
            op_cls(this=exp.column(col), expression=_literal(value)),
            append=True,
        )
    return query


def _where_from_filters(filters: list[dict[str, Any]]) -> Optional[exp.Expression]:
    if not filters:
        return None
    parts: list[exp.Expression] = []
    for f in filters:
        col = f.get("column")
        op = (f.get("op") or "=").lower()
        value = f.get("value")
        if not col:
            raise SqlGuardError("Filter requires column")
        op_cls = ALLOWED_FILTER_OPS.get(op)
        if not op_cls:
            raise SqlGuardError(f"Unsupported filter op: {op}")
        parts.append(op_cls(this=exp.column(col), expression=_literal(value)))
    where_expr = parts[0]
    for part in parts[1:]:
        where_expr = exp.And(this=where_expr, expression=part)
    return where_expr


def _assert_filter_columns(
    policy: dict[str, Any],
    table: str,
    schema_name: Optional[str],
    filters: list[dict[str, Any]],
) -> None:
    for f in filters:
        col = f.get("column")
        if not col:
            continue
        ok, reason, _ = check_access(
            policy,
            table=table,
            operation="select",
            schema_name=schema_name,
            columns=[col],
        )
        # Filters may reference columns even if select is off; fall back to write op columns later.
        # Prefer: column must not be denied / must be in allow list if present.
        from app.policy_store import find_table_policy

        tp = find_table_policy(policy, table, schema_name)
        if not tp:
            raise SqlGuardError(reason or "Table not in whitelist")
        denied = set(tp.get("denied_columns") or [])
        allowed = tp.get("allowed_columns")
        if col in denied:
            raise SqlGuardError(f"Filter column denied: {col}")
        if allowed is not None and col not in allowed:
            raise SqlGuardError(f"Filter column not in whitelist: {col}")


def build_mutate_plan(
    *,
    dialect: str,
    policy: dict[str, Any],
    operation: MutationOp,
    table: str,
    schema_name: Optional[str] = None,
    values: Optional[dict[str, Any]] = None,
    set_values: Optional[dict[str, Any]] = None,
    filters: Optional[list[dict[str, Any]]] = None,
    preview_limit: int = 20,
) -> MutatePlan:
    filters = filters or []
    read_dialect = _dialect(dialect)

    if operation == "insert":
        if not values:
            raise SqlGuardError("insert requires values")
        cols = list(values.keys())
        ok, reason, _ = check_access(
            policy, table=table, operation="insert", schema_name=schema_name, columns=cols
        )
        if not ok:
            raise SqlGuardError(reason or "Access denied")

        insert = exp.Insert(
            this=_table_exp(table, schema_name),
            expression=exp.Values(
                expressions=[
                    exp.Tuple(expressions=[_literal(values[c]) for c in cols])
                ]
            ),
        )
        # column list
        insert.set(
            "this",
            exp.Schema(
                this=_table_exp(table, schema_name),
                expressions=[exp.to_identifier(c) for c in cols],
            ),
        )
        sql = insert.sql(dialect=read_dialect)
        # Preview is the payload itself; count is always 1
        return MutatePlan(
            operation="insert",
            table=table,
            schema_name=schema_name,
            sql=sql,
            count_sql="SELECT 1 AS cnt",
            preview_sql="",
            filters=[],
            values=values,
        )

    # update / delete need where rules
    if operation == "update":
        if policy.get("require_where_for_update", True) and not filters:
            raise SqlGuardError("UPDATE requires a WHERE filter under current policy")
        if not set_values:
            raise SqlGuardError("update requires set_values")
        cols = list(set_values.keys())
        ok, reason, _ = check_access(
            policy, table=table, operation="update", schema_name=schema_name, columns=cols
        )
        if not ok:
            raise SqlGuardError(reason or "Access denied")
        _assert_filter_columns(policy, table, schema_name, filters)

        where_expr = _where_from_filters(filters)
        update = exp.Update(
            this=_table_exp(table, schema_name),
            expressions=[
                exp.EQ(this=exp.column(k), expression=_literal(v))
                for k, v in set_values.items()
            ],
        )
        if where_expr is not None:
            update.set("where", exp.Where(this=where_expr))
        sql = update.sql(dialect=read_dialect)

    elif operation == "delete":
        if policy.get("require_where_for_delete", True) and not filters:
            raise SqlGuardError("DELETE requires a WHERE filter under current policy")
        ok, reason, _ = check_access(
            policy, table=table, operation="delete", schema_name=schema_name
        )
        if not ok:
            raise SqlGuardError(reason or "Access denied")
        _assert_filter_columns(policy, table, schema_name, filters)

        where_expr = _where_from_filters(filters)
        delete = exp.Delete(this=_table_exp(table, schema_name))
        if where_expr is not None:
            delete.set("where", exp.Where(this=where_expr))
        sql = delete.sql(dialect=read_dialect)
        set_values = None
    else:
        raise SqlGuardError(f"Unsupported mutation operation: {operation}")

    # count + preview SELECT using same filters
    count_q = exp.select(exp.alias_(exp.Count(this=exp.Star()), "cnt")).from_(
        _table_exp(table, schema_name)
    )
    count_q = _apply_filters(count_q, filters)

    preview_q = exp.select(exp.Star()).from_(_table_exp(table, schema_name))
    preview_q = _apply_filters(preview_q, filters).limit(preview_limit)

    # If column restrictions exist, avoid SELECT * in preview
    from app.policy_store import find_table_policy

    tp = find_table_policy(policy, table, schema_name) or {}
    if tp.get("allowed_columns") is not None or tp.get("denied_columns"):
        from app.policy import effective_columns

        cols = effective_columns(tp, all_columns=tp.get("allowed_columns") or [])
        if tp.get("allowed_columns") is not None:
            cols = [c for c in (tp.get("allowed_columns") or []) if c not in set(tp.get("denied_columns") or [])]
        if cols:
            preview_q = exp.select(*[exp.column(c) for c in cols]).from_(
                _table_exp(table, schema_name)
            )
            preview_q = _apply_filters(preview_q, filters).limit(preview_limit)

    return MutatePlan(
        operation=operation,
        table=table,
        schema_name=schema_name,
        sql=sql,
        count_sql=count_q.sql(dialect=read_dialect),
        preview_sql=preview_q.sql(dialect=read_dialect),
        filters=filters,
        values=None,
        set_values=set_values,
    )
