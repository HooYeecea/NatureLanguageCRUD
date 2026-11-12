"""Short-lived undo snapshots for DELETE (re-insert rows by primary key)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.meta_db import meta_conn
from app import secure_payload

DEFAULT_TTL_SECONDS = 10 * 60


def ensure_undo_table(conn) -> None:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS undo_mutations (
            id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL,
            table_name TEXT NOT NULL,
            schema_name TEXT,
            dialect TEXT NOT NULL,
            rows_json TEXT NOT NULL,
            primary_key_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            restored_at TEXT,
            FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
        )
        """
    )

    if "connection_version" not in {r[1] for r in conn.execute("PRAGMA table_info(undo_mutations)")}:
        conn.execute("ALTER TABLE undo_mutations ADD COLUMN connection_version TEXT")
    secure_payload.maintain(conn, "undo_mutations", "rows_json", [])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_undo(
    connection_id: str,
    *,
    table: str,
    schema_name: Optional[str],
    dialect: str,
    rows: list[dict[str, Any]],
    primary_key: list[str],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    status: str = "available",
    connection_version: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if not rows or not primary_key:
        return None
    undo_id = str(uuid.uuid4())
    now = _utcnow()
    expires = now + timedelta(seconds=ttl_seconds)
    with meta_conn() as conn:
        ensure_undo_table(conn)
        conn.execute(
            """
            INSERT INTO undo_mutations (
                id, connection_id, table_name, schema_name, dialect,
                rows_json, primary_key_json, status, created_at, expires_at, connection_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                undo_id,
                connection_id,
                table,
                schema_name,
                dialect,
                secure_payload.dumps(rows),
                json.dumps(list(primary_key), ensure_ascii=False),
                status,
                now.isoformat(),
                expires.isoformat(),
                connection_version,
            ),
        )
    return get_undo(undo_id)


def get_undo(undo_id: str) -> Optional[dict[str, Any]]:
    with meta_conn() as conn:
        ensure_undo_table(conn)
        row = conn.execute(
            "SELECT * FROM undo_mutations WHERE id = ?",
            (undo_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "connection_version": row["connection_version"],
        "table": row["table_name"],
        "schema_name": row["schema_name"],
        "dialect": row["dialect"],
        "rows": secure_payload.loads(row["rows_json"]),
        "primary_key": json.loads(row["primary_key_json"]),
        "status": row["status"],
        "created_at": datetime.fromisoformat(row["created_at"]),
        "expires_at": datetime.fromisoformat(row["expires_at"]),
        "restored_at": datetime.fromisoformat(row["restored_at"]) if row["restored_at"] else None,
    }


def is_undo_expired(undo: dict[str, Any]) -> bool:
    return _utcnow() >= undo["expires_at"]


def claim_undo(undo_id: str, *, connection_id: str, connection_version: Optional[str] = None) -> tuple[Optional[dict[str, Any]], str]:
    """Atomically available → restoring."""
    now = _utcnow()
    with meta_conn() as conn:
        ensure_undo_table(conn)
        row = conn.execute(
            "SELECT * FROM undo_mutations WHERE id = ?",
            (undo_id,),
        ).fetchone()
        if not row:
            return None, "not_found"
        if row["connection_id"] != connection_id:
            return None, "wrong_connection"
        if connection_version is not None:
            current = conn.execute("SELECT updated_at FROM connections WHERE id=?", (connection_id,)).fetchone()
            if not current or row["connection_version"] != connection_version or current[0] != connection_version:
                return None, "stale_connection"
        if row["status"] != "available":
            return None, "not_available"
        if now >= datetime.fromisoformat(row["expires_at"]):
            conn.execute(
                "UPDATE undo_mutations SET status = 'expired' WHERE id = ?",
                (undo_id,),
            )
            return None, "expired"
        cur = conn.execute(
            """
            UPDATE undo_mutations
            SET status = 'restoring'
            WHERE id = ? AND status = 'available'
            """,
            (undo_id,),
        )
        if cur.rowcount != 1:
            return None, "busy"
    return get_undo(undo_id), ""


def mark_undo_restored(undo_id: str) -> None:
    with meta_conn() as conn:
        ensure_undo_table(conn)
        conn.execute(
            """
            UPDATE undo_mutations
            SET status = 'restored', restored_at = ?
            WHERE id = ? AND status = 'restoring'
            """,
            (_utcnow().isoformat(), undo_id),
        )


def mark_undo_failed(undo_id: str) -> None:
    with meta_conn() as conn:
        ensure_undo_table(conn)
        conn.execute(
            """
            UPDATE undo_mutations
            SET status = 'available'
            WHERE id = ? AND status = 'restoring'
            """,
            (undo_id,),
        )


def activate_undo(undo_id: str) -> None:
    with meta_conn() as conn:
        conn.execute("UPDATE undo_mutations SET status='available' WHERE id=? AND status='preparing'", (undo_id,))


def discard_undo(undo_id: str) -> None:
    with meta_conn() as conn:
        conn.execute("DELETE FROM undo_mutations WHERE id=? AND status='preparing'", (undo_id,))


def mark_undo_indeterminate(item_id):
    with meta_conn() as conn:
        conn.execute("UPDATE undo_mutations SET status='indeterminate' WHERE id=? AND status='restoring'", (item_id,))
