"""Controlled SELECT validation and LIMIT enforcement."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.policy import check_access

DIALECT_MAP = {
    "sqlite": "sqlite",
    "mysql": "mysql",
    "postgresql": "postgres",
    "sqlserver": "tsql",
}


class SqlGuardError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass
class GuardedQuery:
    sql: str
    tables: list[str] = field(default_factory=list)
    limit: Optional[int] = None


def _sqlglot_dialect(dialect: str) -> str:
    try:
        return DIALECT_MAP[dialect]
    except KeyError as exc:
        raise SqlGuardError(f"Unsupported dialect: {dialect}") from exc


def _assert_select_only(expression: exp.Expression) -> exp.Expression:
    # Unwrap WITH
    root = expression
    if isinstance(root, exp.With):
        root = root.this
    if not isinstance(root, exp.Select):
        # UNION etc.
        if isinstance(root, exp.Union):
            for part in root.flatten():
                if isinstance(part, exp.Select):
                    continue
                if isinstance(part, exp.Union):
                    continue
            # Ensure all leaves are SELECT
            for node in root.walk():
                if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command)):
                    raise SqlGuardError("Only SELECT queries are allowed")
            return expression
        raise SqlGuardError(f"Only SELECT queries are allowed, got: {type(root).__name__}")

    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Command,
        exp.Grant,
        exp.Merge,
    )
    for node in expression.walk():
        if isinstance(node, forbidden):
            raise SqlGuardError(f"Forbidden SQL clause: {type(node).__name__}")
        # SELECT ... INTO
        if isinstance(node, exp.Table) and node.args.get("into"):
            raise SqlGuardError("SELECT INTO is not allowed")
    return expression


def _collect_tables(expression: exp.Expression) -> list[tuple[Optional[str], str]]:
    tables: list[tuple[Optional[str], str]] = []
    for table in expression.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        schema = table.db or None  # sqlglot uses db for schema in many dialects
        tables.append((schema, name))
    # unique preserve order
    seen = set()
    out = []
    for item in tables:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _collect_columns(expression: exp.Expression) -> list[str]:
    cols: list[str] = []
    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if not select:
        return cols
    for projection in select.expressions:
        if isinstance(projection, exp.Star):
            cols.append("*")
            continue
        # alias: x AS y -> use underlying column if simple
        col = projection.find(exp.Column)
        if col and col.name:
            cols.append(col.name)
    return cols


def _current_limit(expression: exp.Expression) -> Optional[int]:
    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if not select:
        return None
    limit = select.args.get("limit")
    if not limit:
        return None
    try:
        return int(limit.expression.this)
    except Exception:  # noqa: BLE001
        return None


def _apply_limit(expression: exp.Expression, max_rows: int, dialect: str) -> tuple[exp.Expression, int]:
    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if not select:
        raise SqlGuardError("Cannot apply LIMIT to non-SELECT query")

    current = _current_limit(expression)
    effective = max_rows if current is None else min(current, max_rows)

    # Rebuild limit
    select = select.copy()
    select.set("limit", exp.Limit(expression=exp.Literal.number(effective)))

    if isinstance(expression, exp.With):
        expression = expression.copy()
        expression.set("this", select)
        return expression, effective
    if isinstance(expression, exp.Union):
        # Apply to outer wrap: SELECT * FROM (union) LIMIT n
        wrapped = (
            exp.select("*")
            .from_(exp.Subquery(this=expression.copy(), alias="q"))
            .limit(effective)
        )
        return wrapped, effective
    return select, effective


def guard_select_sql(
    sql: str,
    *,
    dialect: str,
    policy: dict[str, Any],
) -> GuardedQuery:
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        raise SqlGuardError("SQL is empty")
    if ";" in sql:
        raise SqlGuardError("Multiple SQL statements are not allowed")

    read_dialect = _sqlglot_dialect(dialect)
    try:
        expressions = sqlglot.parse(sql, read=read_dialect)
    except ParseError as exc:
        raise SqlGuardError(f"SQL parse error: {exc}") from exc

    if not expressions or expressions[0] is None:
        raise SqlGuardError("SQL parse returned empty expression")
    if len(expressions) > 1:
        raise SqlGuardError("Multiple SQL statements are not allowed")

    expression = expressions[0]
    expression = _assert_select_only(expression)

    tables = _collect_tables(expression)
    if not tables:
        raise SqlGuardError("Could not determine target tables from SQL")

    columns = _collect_columns(expression)
    # Per-table access check
    for schema_name, table in tables:
        # When SELECT *, don't pass columns so we only check table+op
        cols_for_check = None if "*" in columns else (columns or None)
        ok, reason, _ = check_access(
            policy,
            table=table,
            operation="select",
            schema_name=schema_name,
            columns=cols_for_check,
        )
        if not ok:
            raise SqlGuardError(reason or "Access denied")

    # Block SELECT * when any involved table has column allow/deny restrictions
    if "*" in columns:
        for schema_name, table in tables:
            tp = None
            for item in policy.get("tables") or []:
                if item.get("table") != table:
                    continue
                if (
                    schema_name is None
                    or item.get("schema_name") is None
                    or item.get("schema_name") == schema_name
                ):
                    tp = item
                    break
            if not tp:
                continue
            if tp.get("allowed_columns") is not None or tp.get("denied_columns"):
                raise SqlGuardError(
                    "SELECT * is not allowed when column whitelist/blacklist is configured; "
                    "list explicit columns instead"
                )

    max_rows = int(policy.get("max_rows_per_query") or 500)
    expression, effective_limit = _apply_limit(expression, max_rows, read_dialect)
    final_sql = expression.sql(dialect=read_dialect)

    return GuardedQuery(
        sql=final_sql,
        tables=[t for _, t in tables],
        limit=effective_limit,
    )


def build_structured_select(
    *,
    dialect: str,
    policy: dict[str, Any],
    table: str,
    schema_name: Optional[str] = None,
    columns: Optional[list[str]] = None,
    filters: Optional[list[dict[str, Any]]] = None,
    order_by: Optional[list[dict[str, str]]] = None,
    limit: Optional[int] = None,
) -> GuardedQuery:
    """Build a parameterized-looking SELECT; values are literal-escaped via sqlglot."""
    ok, reason, effective_cols = check_access(
        policy,
        table=table,
        operation="select",
        schema_name=schema_name,
        columns=columns,
    )
    if not ok:
        raise SqlGuardError(reason or "Access denied")

    table_policy = None
    for item in policy.get("tables") or []:
        if item.get("table") == table and (
            schema_name is None
            or item.get("schema_name") is None
            or item.get("schema_name") == schema_name
        ):
            table_policy = item
            break

    select_cols = columns or effective_cols
    if not select_cols:
        # all columns allowed (None whitelist) -> SELECT *
        if table_policy and table_policy.get("allowed_columns") is None:
            projections: list[exp.Expression] = [exp.Star()]
        else:
            raise SqlGuardError("No selectable columns resolved")
    else:
        projections = [exp.column(c) for c in select_cols]

    table_exp = exp.Table(
        this=exp.to_identifier(table),
        db=exp.to_identifier(schema_name) if schema_name else None,
    )
    query = exp.Select(expressions=projections).from_(table_exp)

    allowed_ops = {
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
    for f in filters or []:
        col = f.get("column")
        op = (f.get("op") or "=").lower()
        value = f.get("value")
        if not col:
            raise SqlGuardError("Filter requires column")
        # column must be allowed
        ok, reason, _ = check_access(
            policy, table=table, operation="select", schema_name=schema_name, columns=[col]
        )
        if not ok:
            raise SqlGuardError(reason or f"Filter column not allowed: {col}")
        op_cls = allowed_ops.get(op)
        if not op_cls:
            raise SqlGuardError(f"Unsupported filter op: {op}")
        lit = exp.Literal.number(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else exp.Literal.string(str(value))
        query = query.where(op_cls(this=exp.column(col), expression=lit), append=True)

    for item in order_by or []:
        col = item.get("column")
        direction = (item.get("direction") or "asc").lower()
        if not col:
            continue
        ok, reason, _ = check_access(
            policy, table=table, operation="select", schema_name=schema_name, columns=[col]
        )
        if not ok:
            raise SqlGuardError(reason or f"Order column not allowed: {col}")
        order_exp = exp.column(col)
        query = query.order_by(order_exp.desc() if direction == "desc" else order_exp.asc(), append=True)

    max_rows = int(policy.get("max_rows_per_query") or 500)
    effective_limit = max_rows if limit is None else min(int(limit), max_rows)
    query = query.limit(effective_limit)

    read_dialect = _sqlglot_dialect(dialect)
    return GuardedQuery(sql=query.sql(dialect=read_dialect), tables=[table], limit=effective_limit)
