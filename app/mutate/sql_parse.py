"""Parse simple INSERT/UPDATE/DELETE SQL back into structured mutate fields."""

from __future__ import annotations

from typing import Any, Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.query.sql_guard import DIALECT_MAP, SqlGuardError


def _read_dialect(dialect: str) -> str:
    try:
        return DIALECT_MAP[dialect]
    except KeyError as exc:
        raise SqlGuardError(f"Unsupported dialect: {dialect}") from exc


def _literal_value(node: exp.Expression) -> Any:
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        text = str(node.this)
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
        try:
            return float(text)
        except ValueError:
            return text
    raise SqlGuardError(f"Unsupported literal in mutation SQL: {node.sql()}")


def _parse_where_filters(where: Optional[exp.Expression]) -> list[dict[str, Any]]:
    if where is None:
        return []
    root = where.this if isinstance(where, exp.Where) else where
    filters: list[dict[str, Any]] = []

    def walk(node: exp.Expression) -> None:
        if isinstance(node, exp.And):
            walk(node.this)
            walk(node.expression)
            return
        op_map = {
            exp.EQ: "=",
            exp.NEQ: "!=",
            exp.GT: ">",
            exp.GTE: ">=",
            exp.LT: "<",
            exp.LTE: "<=",
            exp.Like: "like",
            exp.ILike: "ilike",
        }
        for cls, op in op_map.items():
            if isinstance(node, cls):
                col = node.this
                if not isinstance(col, exp.Column) or not col.name:
                    raise SqlGuardError("WHERE must compare a simple column")
                filters.append(
                    {
                        "column": col.name,
                        "op": op,
                        "value": _literal_value(node.expression),
                    }
                )
                return
        raise SqlGuardError(
            "Only simple AND-combined column comparisons are supported when editing mutate SQL"
        )

    walk(root)
    return filters


def parse_mutate_sql(sql: str, *, dialect: str) -> dict[str, Any]:
    """
    Returns {operation, table, schema_name, values, set_values, filters}.
    Only single-statement INSERT/UPDATE/DELETE with simple literals.
    """
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        raise SqlGuardError("SQL is empty")
    if ";" in sql:
        raise SqlGuardError("Multiple SQL statements are not allowed")

    read = _read_dialect(dialect)
    try:
        expressions = sqlglot.parse(sql, read=read)
    except ParseError as exc:
        raise SqlGuardError(f"SQL parse error: {exc}") from exc
    if not expressions or expressions[0] is None:
        raise SqlGuardError("SQL parse returned empty expression")
    if len(expressions) > 1:
        raise SqlGuardError("Multiple SQL statements are not allowed")

    expression = expressions[0]

    if isinstance(expression, exp.Insert):
        schema = expression.this
        if not isinstance(schema, exp.Schema):
            raise SqlGuardError("INSERT must list columns explicitly")
        table_exp = schema.this
        if not isinstance(table_exp, exp.Table) or not table_exp.name:
            raise SqlGuardError("Could not determine INSERT table")
        col_names = [ident.name for ident in schema.expressions if isinstance(ident, exp.Identifier)]
        values_expr = expression.expression
        if not isinstance(values_expr, exp.Values) or not values_expr.expressions:
            raise SqlGuardError("INSERT must use VALUES (...)")
        first_row = values_expr.expressions[0]
        if not isinstance(first_row, exp.Tuple):
            raise SqlGuardError("INSERT VALUES row must be a tuple")
        literals = [_literal_value(v) for v in first_row.expressions]
        if len(col_names) != len(literals):
            raise SqlGuardError("INSERT column count does not match VALUES")
        return {
            "operation": "insert",
            "table": table_exp.name,
            "schema_name": table_exp.db or None,
            "values": dict(zip(col_names, literals)),
            "set_values": None,
            "filters": [],
        }

    if isinstance(expression, exp.Update):
        table_exp = expression.this
        if not isinstance(table_exp, exp.Table) or not table_exp.name:
            raise SqlGuardError("Could not determine UPDATE table")
        set_values: dict[str, Any] = {}
        for item in expression.expressions or []:
            if not isinstance(item, exp.EQ) or not isinstance(item.this, exp.Column):
                raise SqlGuardError("UPDATE SET must be column = literal")
            set_values[item.this.name] = _literal_value(item.expression)
        if not set_values:
            raise SqlGuardError("update requires set_values")
        return {
            "operation": "update",
            "table": table_exp.name,
            "schema_name": table_exp.db or None,
            "values": None,
            "set_values": set_values,
            "filters": _parse_where_filters(expression.args.get("where")),
        }

    if isinstance(expression, exp.Delete):
        table_exp = expression.this
        if not isinstance(table_exp, exp.Table) or not table_exp.name:
            raise SqlGuardError("Could not determine DELETE table")
        return {
            "operation": "delete",
            "table": table_exp.name,
            "schema_name": table_exp.db or None,
            "values": None,
            "set_values": None,
            "filters": _parse_where_filters(expression.args.get("where")),
        }

    raise SqlGuardError(
        f"Only INSERT/UPDATE/DELETE can be used when editing mutate SQL, got: {type(expression).__name__}"
    )
