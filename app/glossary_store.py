"""Persist per-connection business glossary."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.meta_db import meta_conn


def ensure_glossary_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS glossaries (
            id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL UNIQUE,
            glossary_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
        )
        """
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_glossary(connection_id: str) -> list[dict[str, Any]]:
    with meta_conn() as conn:
        ensure_glossary_table(conn)
        row = conn.execute(
            "SELECT glossary_json FROM glossaries WHERE connection_id = ?",
            (connection_id,),
        ).fetchone()
    if not row:
        return []
    data = json.loads(row["glossary_json"] or "[]")
    return data if isinstance(data, list) else data.get("tables") or []


def save_glossary(connection_id: str, tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _utcnow()
    payload = json.dumps(tables, ensure_ascii=False)
    with meta_conn() as conn:
        ensure_glossary_table(conn)
        existing = conn.execute(
            "SELECT id FROM glossaries WHERE connection_id = ?",
            (connection_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE glossaries SET glossary_json = ?, updated_at = ? WHERE id = ?",
                (payload, now, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO glossaries (id, connection_id, glossary_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), connection_id, payload, now),
            )
    return get_glossary(connection_id)


def delete_glossary(connection_id: str) -> None:
    with meta_conn() as conn:
        ensure_glossary_table(conn)
        conn.execute("DELETE FROM glossaries WHERE connection_id = ?", (connection_id,))
