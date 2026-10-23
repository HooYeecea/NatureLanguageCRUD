"""Pending mutation preview store (confirm-before-execute)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.meta_db import meta_conn

DEFAULT_TTL_SECONDS = 10 * 60


def ensure_pending_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_mutations (
            id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            executed_at TEXT,
            FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
        )
        """
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_pending(
    connection_id: str,
    plan: dict[str, Any],
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    pending_id = str(uuid.uuid4())
    now = _utcnow()
    expires = now + timedelta(seconds=ttl_seconds)
    with meta_conn() as conn:
        ensure_pending_table(conn)
        conn.execute(
            """
            INSERT INTO pending_mutations (
                id, connection_id, plan_json, status, created_at, expires_at
            ) VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (
                pending_id,
                connection_id,
                json.dumps(plan, ensure_ascii=False),
                now.isoformat(),
                expires.isoformat(),
            ),
        )
    return get_pending(pending_id)  # type: ignore[return-value]


def get_pending(pending_id: str) -> Optional[dict[str, Any]]:
    with meta_conn() as conn:
        ensure_pending_table(conn)
        row = conn.execute(
            "SELECT * FROM pending_mutations WHERE id = ?",
            (pending_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "plan": json.loads(row["plan_json"]),
        "status": row["status"],
        "created_at": datetime.fromisoformat(row["created_at"]),
        "expires_at": datetime.fromisoformat(row["expires_at"]),
        "executed_at": datetime.fromisoformat(row["executed_at"]) if row["executed_at"] else None,
    }


def mark_executed(pending_id: str) -> None:
    with meta_conn() as conn:
        ensure_pending_table(conn)
        conn.execute(
            """
            UPDATE pending_mutations
            SET status = 'executed', executed_at = ?
            WHERE id = ?
            """,
            (_utcnow().isoformat(), pending_id),
        )


def mark_cancelled(pending_id: str) -> None:
    with meta_conn() as conn:
        ensure_pending_table(conn)
        conn.execute(
            """
            UPDATE pending_mutations
            SET status = 'cancelled'
            WHERE id = ?
            """,
            (pending_id,),
        )


def mark_failed(pending_id: str) -> None:
    with meta_conn() as conn:
        ensure_pending_table(conn)
        conn.execute(
            """
            UPDATE pending_mutations
            SET status = 'failed'
            WHERE id = ? AND status = 'executing'
            """,
            (pending_id,),
        )


def release_claim(pending_id: str) -> None:
    """Return executing → pending (e.g. soft validation failure before write)."""
    with meta_conn() as conn:
        ensure_pending_table(conn)
        conn.execute(
            """
            UPDATE pending_mutations
            SET status = 'pending'
            WHERE id = ? AND status = 'executing'
            """,
            (pending_id,),
        )


def claim_pending(
    pending_id: str,
    *,
    connection_id: Optional[str] = None,
) -> tuple[Optional[dict[str, Any]], str]:
    """
    Atomically move pending → executing.

    Returns (pending_dict, error) where error is '' on success, else one of:
    not_found | wrong_connection | not_pending | expired | busy
    """
    now = _utcnow()
    with meta_conn() as conn:
        ensure_pending_table(conn)
        row = conn.execute(
            "SELECT * FROM pending_mutations WHERE id = ?",
            (pending_id,),
        ).fetchone()
        if not row:
            return None, "not_found"
        if connection_id is not None and row["connection_id"] != connection_id:
            return None, "wrong_connection"
        if row["status"] == "executing":
            return None, "busy"
        if row["status"] != "pending":
            return None, "not_pending"
        expires_at = datetime.fromisoformat(row["expires_at"])
        if now >= expires_at:
            conn.execute(
                """
                UPDATE pending_mutations
                SET status = 'cancelled'
                WHERE id = ? AND status = 'pending'
                """,
                (pending_id,),
            )
            return None, "expired"
        cur = conn.execute(
            """
            UPDATE pending_mutations
            SET status = 'executing'
            WHERE id = ? AND status = 'pending'
            """,
            (pending_id,),
        )
        if cur.rowcount != 1:
            return None, "busy"
    return get_pending(pending_id), ""


def is_expired(pending: dict[str, Any]) -> bool:
    return _utcnow() >= pending["expires_at"]
