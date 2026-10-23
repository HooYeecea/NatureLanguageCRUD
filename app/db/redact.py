"""Redact sensitive column values in API / sample / read-back rows."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from app.policy_store import find_table_policy

SENSITIVE_COL = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|salt)",
    re.I,
)

REDACTED = "[redacted]"


def is_sensitive_column_name(name: str) -> bool:
    return bool(SENSITIVE_COL.search(str(name or "")))


def secret_columns_for_table(
    policy: Optional[dict[str, Any]],
    table: Optional[str],
    schema_name: Optional[str] = None,
) -> set[str]:
    if not policy or not table:
        return set()
    tp = find_table_policy(policy, table, schema_name)
    if not tp:
        return set()
    return {str(c) for c in (tp.get("secret_columns") or []) if c}


def secret_columns_for_tables(
    policy: Optional[dict[str, Any]],
    tables: Optional[Iterable[str]] = None,
) -> set[str]:
    """Union of configured secret columns (optionally limited to table names)."""
    if not policy:
        return set()
    wanted = {t for t in (tables or []) if t} or None
    out: set[str] = set()
    for tp in policy.get("tables") or []:
        name = tp.get("table")
        if wanted is not None and name not in wanted:
            continue
        out.update(str(c) for c in (tp.get("secret_columns") or []) if c)
    return out


def should_redact_column(name: str, secret_columns: Optional[set[str]] = None) -> bool:
    if secret_columns and name in secret_columns:
        return True
    return is_sensitive_column_name(name)


def redact_row(
    row: dict[str, Any],
    *,
    secret_columns: Optional[set[str]] = None,
) -> dict[str, Any]:
    return {
        k: (REDACTED if should_redact_column(str(k), secret_columns) else v)
        for k, v in row.items()
    }


def redact_rows(
    rows: list[dict[str, Any]],
    *,
    secret_columns: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    return [redact_row(r, secret_columns=secret_columns) for r in rows]
