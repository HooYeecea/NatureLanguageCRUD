"""Enforce access policy for later query/mutate steps."""

from __future__ import annotations

from typing import Optional

from app.models import Operation
from app.policy_store import find_table_policy


class PolicyDenied(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _op_allowed(table_policy: dict, operation: Operation) -> bool:
    mapping = {
        "select": "allow_select",
        "insert": "allow_insert",
        "update": "allow_update",
        "delete": "allow_delete",
    }
    return bool(table_policy.get(mapping[operation], False))


def effective_columns(
    table_policy: dict,
    requested: Optional[list[str]] = None,
    all_columns: Optional[list[str]] = None,
) -> list[str]:
    """
    Resolve columns permitted by policy.
    - allowed_columns None => all_columns (or requested) minus denied
    - allowed_columns set => intersection with allowed, minus denied
    """
    denied = set(table_policy.get("denied_columns") or [])
    allowed = table_policy.get("allowed_columns")

    if requested is not None:
        base = list(requested)
    elif allowed is not None:
        base = list(allowed)
    elif all_columns is not None:
        base = list(all_columns)
    else:
        base = []

    if allowed is not None:
        allowed_set = set(allowed)
        base = [c for c in base if c in allowed_set]

    return [c for c in base if c not in denied]


def check_access(
    policy: dict,
    *,
    table: str,
    operation: Operation,
    schema_name: Optional[str] = None,
    columns: Optional[list[str]] = None,
    all_columns: Optional[list[str]] = None,
) -> tuple[bool, Optional[str], Optional[list[str]]]:
    table_policy = find_table_policy(policy, table, schema_name)
    if not table_policy:
        return False, f"Table not in whitelist: {table}", None

    if not _op_allowed(table_policy, operation):
        return False, f"Operation '{operation}' not allowed on table '{table}'", None

    cols = effective_columns(table_policy, columns, all_columns)

    if columns:
        denied = set(table_policy.get("denied_columns") or [])
        allowed = table_policy.get("allowed_columns")
        for col in columns:
            if col in denied:
                return False, f"Column denied: {col}", None
            if allowed is not None and col not in allowed:
                return False, f"Column not in whitelist: {col}", None

    return True, None, cols


def assert_access(
    policy: dict,
    *,
    table: str,
    operation: Operation,
    schema_name: Optional[str] = None,
    columns: Optional[list[str]] = None,
    all_columns: Optional[list[str]] = None,
) -> list[str]:
    ok, reason, cols = check_access(
        policy,
        table=table,
        operation=operation,
        schema_name=schema_name,
        columns=columns,
        all_columns=all_columns,
    )
    if not ok:
        raise PolicyDenied(reason or "Access denied")
    return cols or []
