"""Local metadata store for workbench connections (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app.config import META_DB_PATH
from app.crypto import decrypt_secret, encrypt_secret


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def meta_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_meta_db() -> None:
    with meta_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                dialect TEXT NOT NULL,
                host TEXT,
                port INTEGER,
                database_name TEXT,
                username TEXT,
                password_enc TEXT,
                options_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
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


def _row_to_dict(row: sqlite3.Row, include_password: bool = False) -> dict[str, Any]:
    options = json.loads(row["options_json"] or "{}")
    data = {
        "id": row["id"],
        "name": row["name"],
        "dialect": row["dialect"],
        "host": row["host"],
        "port": row["port"],
        "database": row["database_name"],
        "username": row["username"],
        "has_password": bool(row["password_enc"]),
        "options": options,
        "created_at": datetime.fromisoformat(row["created_at"]),
        "updated_at": datetime.fromisoformat(row["updated_at"]),
    }
    if include_password:
        data["password"] = decrypt_secret(row["password_enc"] or "")
    return data


def create_connection(payload: dict[str, Any]) -> dict[str, Any]:
    conn_id = str(uuid.uuid4())
    now = _utcnow()
    password_enc = encrypt_secret(payload.get("password") or "")
    options = payload.get("options") or {}

    # Normalize sqlite path into options.path
    if payload["dialect"] == "sqlite":
        path = options.get("path") or payload.get("database")
        options = {**options, "path": path}

    with meta_conn() as conn:
        conn.execute(
            """
            INSERT INTO connections (
                id, name, dialect, host, port, database_name, username,
                password_enc, options_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conn_id,
                payload["name"],
                payload["dialect"],
                payload.get("host"),
                payload.get("port"),
                payload.get("database"),
                payload.get("username"),
                password_enc,
                json.dumps(options, ensure_ascii=False),
                now,
                now,
            ),
        )
    return get_connection(conn_id)


def list_connections() -> list[dict[str, Any]]:
    with meta_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM connections ORDER BY created_at ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_connection(conn_id: str, include_password: bool = False) -> Optional[dict[str, Any]]:
    with meta_conn() as conn:
        row = conn.execute(
            "SELECT * FROM connections WHERE id = ?", (conn_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_dict(row, include_password=include_password)


def update_connection(conn_id: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    existing = get_connection(conn_id, include_password=True)
    if not existing:
        return None

    name = payload.get("name", existing["name"])
    dialect = payload.get("dialect", existing["dialect"])
    host = payload["host"] if "host" in payload else existing["host"]
    port = payload["port"] if "port" in payload else existing["port"]
    database = payload["database"] if "database" in payload else existing["database"]
    username = payload["username"] if "username" in payload else existing["username"]
    options = payload["options"] if payload.get("options") is not None else existing["options"]

    if "password" in payload and payload["password"] is not None:
        password_enc = encrypt_secret(payload["password"])
    else:
        password_enc = encrypt_secret(existing.get("password") or "")

    if dialect == "sqlite":
        path = (options or {}).get("path") or database
        options = {**(options or {}), "path": path}

    now = _utcnow()
    with meta_conn() as conn:
        conn.execute(
            """
            UPDATE connections SET
                name = ?, dialect = ?, host = ?, port = ?, database_name = ?,
                username = ?, password_enc = ?, options_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                dialect,
                host,
                port,
                database,
                username,
                password_enc,
                json.dumps(options or {}, ensure_ascii=False),
                now,
                conn_id,
            ),
        )
    return get_connection(conn_id)


def delete_connection(conn_id: str) -> bool:
    with meta_conn() as conn:
        conn.execute("DELETE FROM access_policies WHERE connection_id = ?", (conn_id,))
        cur = conn.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
        return cur.rowcount > 0
