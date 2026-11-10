"""Controlled SELECT validation and LIMIT enforcement."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.policy import check_access
from app.policy_store import find_table_policy
from app.db.redact import should_redact_column, REDACTED
from sqlglot.optimizer.scope import Scope, traverse_scope

DIALECT_MAP = {
    "sqlite": "sqlite",
    "mysql": "mysql",
    "postgresql": "postgres",
    "sqlserver": "tsql",
}


class SqlGuardError(Exception):
    def __init__(self, message: str, sql: str | None = None):
        self.message = message
        self.sql = sql
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


_ALLOWED_AGG = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)


def _assert_select_only(expression: exp.Expression) -> exp.Expression:
    if not isinstance(expression, (exp.Select, exp.Union)):
        raise SqlGuardError(f"Only SELECT queries are allowed, got: {type(expression).__name__}")

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
    _assert_select_features(expression)
    return expression


def _assert_select_features(expression: exp.Expression) -> None:
    """Allow INNER/LEFT/RIGHT JOIN + COUNT/SUM/AVG/MIN/MAX + GROUP BY; reject the rest."""
    for node in expression.walk():
        if isinstance(node, (exp.Into, exp.Lock, exp.Dot, exp.Placeholder, exp.Parameter)):
            raise SqlGuardError(f"SQL feature not allowed: {type(node).__name__}")
        if isinstance(node, exp.Table) and (node.catalog or not isinstance(node.this, exp.Identifier)):
            raise SqlGuardError("Catalog-qualified tables and table functions are not allowed")
        if isinstance(node, exp.TableAlias) and node.args.get("columns"):
            raise SqlGuardError("Renaming derived columns through a table alias is not allowed")
        if isinstance(node, exp.Star) and any(node.args.values()):
            raise SqlGuardError("Star modifiers are not allowed")
        if isinstance(node, exp.Func) and type(node).__name__ not in {
            "Count", "Sum", "Avg", "Min", "Max", "Coalesce", "Nullif", "Abs",
            "Round", "Ceil", "Floor", "Lower", "Upper", "Length", "Substring",
            "Trim", "Concat", "ConcatWs", "Cast", "TryCast", "Case", "If",
            "Date", "CurrentDate", "CurrentTimestamp", "Extract",
        }:
            raise SqlGuardError(f"Function not allowed: {type(node).__name__}")
        if isinstance(node, exp.Join):
            side = str(node.args.get("side") or "").strip().lower()
            kind = str(node.args.get("kind") or "").strip().lower()
            label = f"{side} {kind}".strip()
            if "cross" in label or "full" in label or "semi" in label or "anti" in label:
                raise SqlGuardError(
                    f"JOIN type not allowed: {label or 'unknown'}. "
                    "Use INNER / LEFT / RIGHT JOIN only."
                )
            if node.args.get("on") is None and node.args.get("using") is None:
                raise SqlGuardError("JOIN requires ON or USING (CROSS JOIN is not allowed)")

        if isinstance(node, exp.AggFunc) and not isinstance(node, _ALLOWED_AGG):
            raise SqlGuardError(
                f"Aggregate not allowed: {type(node).__name__}. "
                "Allowed: COUNT, SUM, AVG, MIN, MAX."
            )

        if isinstance(node, exp.Window):
            raise SqlGuardError("Window functions are not allowed")


def _validate_scopes(expression: exp.Expression, policy: dict) -> list[tuple[Optional[str], str]]:
    """Check every scope/branch and replace direct secret projections before execution.

    Without a database schema, ambiguous unqualified JOIN columns are checked against
    every possible source. Unknown derived outputs and correlated references fail closed.
    """
    tables = []
    for scope in traverse_scope(expression):
        if not isinstance(scope.expression, (exp.Select, exp.Union)):
            raise SqlGuardError("Unsupported query scope")
        if isinstance(scope.expression, exp.Union):
            continue
        sources = scope.selected_sources
        for _, source in sources.values():
            if isinstance(source, exp.Table):
                ok, reason, _ = check_access(policy, table=source.name,
                    schema_name=source.db or None, operation="select")
                if not ok:
                    raise SqlGuardError(reason or "Access denied")
                tables.append((source.db or None, source.name))

        def validate_column(col: exp.Column) -> bool:
            if col.db or col.catalog:
                raise SqlGuardError("Use a table alias for qualified columns")
            if not col.table and col.name in sources:
                raise SqlGuardError("Whole-row references are not allowed")
            candidates = [sources[col.table]] if col.table in sources else list(sources.values())
            if col.table and col.table not in sources:
                raise SqlGuardError("Unresolved or correlated column reference")
            if not candidates:
                raise SqlGuardError("Cannot resolve column source")
            sensitive = False
            for _, source in candidates:
                if isinstance(source, exp.Table):
                    ok, reason, _ = check_access(policy, table=source.name,
                        schema_name=source.db or None, operation="select", columns=[col.name])
                    if not ok:
                        raise SqlGuardError(reason or "Column access denied")
                    tp = find_table_policy(policy, source.name, source.db or None) or {}
                    sensitive |= should_redact_column(col.name, set(tp.get("secret_columns") or []))
                elif isinstance(source, Scope):
                    if col.name not in source.expression.named_selects:
                        raise SqlGuardError("Cannot resolve derived column source")
                else:
                    raise SqlGuardError("Unsupported column source")
            return sensitive

        for col in scope.columns:
            if col.is_star:
                continue
            if validate_column(col):
                projection = col.parent if isinstance(col.parent, exp.Alias) else col
                if projection not in scope.expression.expressions:
                    raise SqlGuardError("Sensitive columns cannot be used in expressions or predicates")
                # Preserve the output name but never retrieve the underlying secret.
                projection.replace(exp.alias_(exp.Literal.string(REDACTED), projection.alias_or_name, quoted=True))

        for projection in scope.expression.expressions:
            if not projection.is_star:
                continue
            if scope.parent is not None:
                raise SqlGuardError("Derived queries must list explicit columns")
            candidates = list(sources.values())
            if isinstance(projection, exp.Column) and projection.table:
                if projection.table not in sources:
                    raise SqlGuardError("Unknown star qualifier")
                candidates = [sources[projection.table]]
            for _, source in candidates:
                if not isinstance(source, exp.Table):
                    raise SqlGuardError("Derived queries must list explicit columns")
                tp = find_table_policy(policy, source.name, source.db or None) or {}
                if tp.get("allowed_columns") is not None or tp.get("denied_columns") or tp.get("secret_columns"):
                    raise SqlGuardError("SELECT * is not allowed with column restrictions; list explicit columns")
        # USING columns are identifiers rather than exp.Column nodes.
        for join in scope.expression.args.get("joins") or []:
            for ident in join.args.get("using") or []:
                if validate_column(exp.column(ident.name)):
                    raise SqlGuardError("Sensitive JOIN columns are not allowed")
    return list(dict.fromkeys(tables))


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
    if current is not None and current < 1:
        raise SqlGuardError("LIMIT must be positive")
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

    tables = _validate_scopes(expression, policy)
    if not tables:
        raise SqlGuardError("Could not determine target tables from SQL")

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
    all_columns: Optional[list[str]] = None,
) -> GuardedQuery:
    """Build a parameterized-looking SELECT; values are literal-escaped via sqlglot."""
    ok, reason, effective_cols = check_access(
        policy,
        table=table,
        operation="select",
        schema_name=schema_name,
        columns=columns,
        all_columns=all_columns,
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
        if table_policy and table_policy.get("allowed_columns") is None and not table_policy.get("denied_columns") and not table_policy.get("secret_columns"):
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
    return guard_select_sql(query.sql(dialect=read_dialect), dialect=dialect, policy=policy)
