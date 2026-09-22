"""Audit log persistence for workbench actions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.meta_db import meta_conn


def ensure_audit_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY,
            connection_id TEXT,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            actor TEXT,
            summary TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_logs_created
        ON audit_logs(created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_logs_connection
        ON audit_logs(connection_id, created_at DESC)
        """
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_audit(
    *,
    action: str,
    status: str = "success",
    connection_id: Optional[str] = None,
    actor: str = "local",
    summary: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    log_id = str(uuid.uuid4())
    now = _utcnow()
    safe_detail = detail or {}
    # Never persist secrets if caller accidentally passes them
    safe_detail.pop("password", None)
    with meta_conn() as conn:
        ensure_audit_table(conn)
        conn.execute(
            """
            INSERT INTO audit_logs (
                id, connection_id, action, status, actor, summary, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                connection_id,
                action,
                status,
                actor,
                summary,
                json.dumps(safe_detail, ensure_ascii=False, default=str),
                now,
            ),
        )
    return get_audit(log_id)  # type: ignore[return-value]


def get_audit(log_id: str) -> Optional[dict[str, Any]]:
    with meta_conn() as conn:
        ensure_audit_table(conn)
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE id = ?", (log_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def list_audits(
    *,
    connection_id: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    clauses = []
    params: list[Any] = []
    if connection_id:
        clauses.append("connection_id = ?")
        params.append(connection_id)
    if action:
        clauses.append("action = ?")
        params.append(action)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with meta_conn() as conn:
        ensure_audit_table(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM audit_logs
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "action": row["action"],
        "status": row["status"],
        "actor": row["actor"],
        "summary": row["summary"],
        "detail": json.loads(row["detail_json"] or "{}"),
        "created_at": datetime.fromisoformat(row["created_at"]),
    }
