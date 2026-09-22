"""Persist and reuse schema relationship analyses per connection + table set."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.meta_db import meta_conn


def ensure_analysis_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_analyses (
            id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL,
            tables_key TEXT NOT NULL,
            tables_json TEXT NOT NULL,
            analysis_json TEXT NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(connection_id, tables_key),
            FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
        )
        """
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def tables_key(tables: list[dict[str, Any]]) -> str:
    """Stable key for a selected table set."""
    parts = []
    for t in tables:
        name = t.get("table") or t.get("name") or ""
        schema = t.get("schema_name") or ""
        parts.append(f"{schema}.{name}".lower())
    parts = sorted(set(parts))
    raw = "|".join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{len(parts)}:{digest}:{raw[:180]}"


def get_analysis(
    connection_id: str,
    tables: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    key = tables_key(tables)
    with meta_conn() as conn:
        ensure_analysis_table(conn)
        row = conn.execute(
            """
            SELECT * FROM schema_analyses
            WHERE connection_id = ? AND tables_key = ?
            """,
            (connection_id, key),
        ).fetchone()
    if not row:
        return None
    analysis = json.loads(row["analysis_json"])
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "tables_key": row["tables_key"],
        "selected_tables": json.loads(row["tables_json"]),
        "cached": True,
        "updated_at": datetime.fromisoformat(row["updated_at"]),
        "source": row["source"] or analysis.get("source") or "cache",
        **{k: analysis.get(k) for k in (
            "overview",
            "tables",
            "relationships",
            "join_hints",
            "warnings",
            "dialect",
        )},
    }


def get_analysis_for_connection(connection_id: str) -> Optional[dict[str, Any]]:
    """Latest analysis for a connection (any table set)."""
    with meta_conn() as conn:
        ensure_analysis_table(conn)
        row = conn.execute(
            """
            SELECT * FROM schema_analyses
            WHERE connection_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (connection_id,),
        ).fetchone()
    if not row:
        return None
    analysis = json.loads(row["analysis_json"])
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "tables_key": row["tables_key"],
        "selected_tables": json.loads(row["tables_json"]),
        "cached": True,
        "updated_at": datetime.fromisoformat(row["updated_at"]),
        "source": row["source"] or analysis.get("source") or "cache",
        **{k: analysis.get(k) for k in (
            "overview",
            "tables",
            "relationships",
            "join_hints",
            "warnings",
            "dialect",
        )},
    }


def save_analysis(
    connection_id: str,
    tables: list[dict[str, Any]],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    key = tables_key(tables)
    now = _utcnow()
    payload = {
        "overview": analysis.get("overview") or "",
        "tables": analysis.get("tables") or [],
        "relationships": analysis.get("relationships") or [],
        "join_hints": analysis.get("join_hints") or [],
        "warnings": analysis.get("warnings") or [],
        "source": analysis.get("source") or "metadata",
        "dialect": analysis.get("dialect"),
    }
    table_names = [
        t.get("table") or t.get("name")
        for t in tables
        if (t.get("table") or t.get("name"))
    ]
    with meta_conn() as conn:
        ensure_analysis_table(conn)
        existing = conn.execute(
            """
            SELECT id FROM schema_analyses
            WHERE connection_id = ? AND tables_key = ?
            """,
            (connection_id, key),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE schema_analyses
                SET tables_json = ?, analysis_json = ?, source = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(table_names, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    payload["source"],
                    now,
                    existing["id"],
                ),
            )
            analysis_id = existing["id"]
        else:
            analysis_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO schema_analyses (
                    id, connection_id, tables_key, tables_json,
                    analysis_json, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    connection_id,
                    key,
                    json.dumps(table_names, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    payload["source"],
                    now,
                    now,
                ),
            )
    return get_analysis(connection_id, tables)  # type: ignore[return-value]


def analysis_context_text(analysis: Optional[dict[str, Any]]) -> str:
    if not analysis:
        return ""
    lines = ["## Cached schema analysis (use this for joins and business meaning)"]
    if analysis.get("overview"):
        lines.append(f"Overview: {analysis['overview']}")
    for t in analysis.get("tables") or []:
        name = t.get("name")
        purpose = t.get("purpose")
        keys = ", ".join(t.get("key_fields") or [])
        lines.append(f"- Table {name}: {purpose}; key_fields={keys}")
    for r in analysis.get("relationships") or []:
        lines.append(
            f"- Rel: {r.get('from_table')} -> {r.get('to_table')} via {r.get('via')} ({r.get('note')})"
        )
    for h in analysis.get("join_hints") or []:
        lines.append(f"- Join hint: {h}")
    return "\n".join(lines)
