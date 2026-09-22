"""Persisted workbench settings (LLM config) in meta DB."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from app.crypto import decrypt_secret, encrypt_secret
from app.meta_db import meta_conn


def ensure_settings_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_raw() -> dict[str, Any]:
    with meta_conn() as conn:
        ensure_settings_table(conn)
        row = conn.execute(
            "SELECT value_json FROM app_settings WHERE key = 'llm'"
        ).fetchone()
    if not row:
        return {}
    return json.loads(row["value_json"] or "{}")


def get_llm_settings() -> dict[str, Any]:
    """DB overrides env. API key never returned in full via public helpers."""
    stored = _get_raw()
    api_key_enc = stored.get("api_key_enc") or ""
    api_key = ""
    if api_key_enc:
        try:
            api_key = decrypt_secret(api_key_enc)
        except ValueError:
            api_key = ""
    if not api_key:
        api_key = LLM_API_KEY or ""

    base_url = stored.get("base_url") or LLM_BASE_URL or "https://api.deepseek.com"
    model = stored.get("model") or LLM_MODEL or "deepseek-chat"

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "configured": bool(api_key),
        "api_key_masked": _mask(api_key) if api_key else None,
        "source": "ui" if api_key_enc else ("env" if LLM_API_KEY else "none"),
    }


def update_llm_settings(
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    clear_api_key: bool = False,
) -> dict[str, Any]:
    stored = _get_raw()

    if clear_api_key:
        stored.pop("api_key_enc", None)
    elif api_key is not None and api_key.strip():
        # Empty string means "keep existing" when client sends blank intentionally
        stored["api_key_enc"] = encrypt_secret(api_key.strip())

    if base_url is not None and base_url.strip():
        stored["base_url"] = base_url.strip()
    if model is not None and model.strip():
        stored["model"] = model.strip()

    with meta_conn() as conn:
        ensure_settings_table(conn)
        conn.execute(
            """
            INSERT INTO app_settings (key, value_json, updated_at)
            VALUES ('llm', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (json.dumps(stored, ensure_ascii=False), _utcnow()),
        )
    return get_llm_settings()


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}...{key[-4:]}"
