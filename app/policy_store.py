"""Access policy persistence and helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.meta_db import meta_conn


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_policy_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS access_policies (
            connection_id TEXT PRIMARY KEY,
            policy_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
        )
        """
    )


def default_policy_dict(connection_id: str) -> dict[str, Any]:
    return {
        "connection_id": connection_id,
        "require_where_for_update": True,
        "require_where_for_delete": True,
        "max_rows_per_mutation": 100,
        "max_rows_per_query": 500,
        "tables": [],
        "updated_at": datetime.now(timezone.utc),
    }


def get_policy(connection_id: str) -> dict[str, Any]:
    with meta_conn() as conn:
        ensure_policy_table(conn)
        row = conn.execute(
            "SELECT policy_json, updated_at FROM access_policies WHERE connection_id = ?",
            (connection_id,),
        ).fetchone()
    if not row:
        return default_policy_dict(connection_id)
    data = json.loads(row["policy_json"])
    data["connection_id"] = connection_id
    data["updated_at"] = datetime.fromisoformat(row["updated_at"])
    return data


def upsert_policy(connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    now = _utcnow()
    stored = {
        "require_where_for_update": payload.get("require_where_for_update", True),
        "require_where_for_delete": payload.get("require_where_for_delete", True),
        "max_rows_per_mutation": payload.get("max_rows_per_mutation", 100),
        "max_rows_per_query": payload.get("max_rows_per_query", 500),
        "tables": payload.get("tables") or [],
    }
    with meta_conn() as conn:
        ensure_policy_table(conn)
        conn.execute(
            """
            INSERT INTO access_policies (connection_id, policy_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                policy_json = excluded.policy_json,
                updated_at = excluded.updated_at
            """,
            (connection_id, json.dumps(stored, ensure_ascii=False), now),
        )
    return get_policy(connection_id)


def delete_policy(connection_id: str) -> None:
    with meta_conn() as conn:
        ensure_policy_table(conn)
        conn.execute(
            "DELETE FROM access_policies WHERE connection_id = ?",
            (connection_id,),
        )


def find_table_policy(
    policy: dict[str, Any],
    table: str,
    schema_name: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    for item in policy.get("tables") or []:
        if item.get("table") != table:
            continue
        item_schema = item.get("schema_name")
        if schema_name is None or item_schema is None or item_schema == schema_name:
            return item
    return None
