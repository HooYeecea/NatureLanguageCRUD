"""Check whether a SELECT result actually answers the user's question."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.query.heuristics import heuristic_mismatch
from app.settings_store import get_llm_settings


def critique_query_result(
    prompt: str,
    sql: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    row_count: int,
    *,
    use_llm: bool = False,
) -> dict[str, Any]:
    """
    Returns {ok, reason, rewrite_hint, source}.

    Cheap path (default): heuristics only — no LLM call when results look fine,
    and rewrite hints come from the heuristic when suspicious.
    Set use_llm=True to ask the model for a richer critique (extra latency/cost).
    """
    heuristic = heuristic_mismatch(prompt, sql, rows, row_count)
    if not heuristic:
        return {"ok": True, "reason": "", "rewrite_hint": "", "source": "heuristic"}

    if not use_llm:
        return {
            "ok": False,
            "reason": heuristic,
            "rewrite_hint": heuristic,
            "source": "heuristic",
        }

    try:
        cfg = get_llm_settings()
        if not cfg["api_key"]:
            raise RuntimeError("no key")
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)
        preview_rows = rows[:8]
        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You judge whether a SQL result answers a user's database question. "
                        "Return JSON only: "
                        '{"ok": boolean, "reason": string, "rewrite_hint": string}. '
                        "ok=true if the rows reasonably answer the question (empty can be ok "
                        "if the filter is correct). "
                        "ok=false if the SQL dumped an unfiltered table, joined wrong, "
                        "ignored a named entity, or selected the wrong columns. "
                        "If the question names a specific entity (e.g. 超级管理员) and SQL has no WHERE, "
                        "ok MUST be false even when that entity appears among other rows. "
                        "If the question asks for 权限, listing role names without permission columns is not ok. "
                        "rewrite_hint is a short instruction for a better SELECT. "
                        "Write reason/rewrite_hint in Chinese."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": prompt,
                            "sql": sql,
                            "columns": columns,
                            "row_count": row_count,
                            "preview_rows": preview_rows,
                            "heuristic": heuristic,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        ok = bool(parsed.get("ok"))
        reason = str(parsed.get("reason") or heuristic or "")
        hint = str(parsed.get("rewrite_hint") or "")
        if heuristic and ok is True and ("没有 WHERE" in heuristic or "整表" in heuristic):
            ok = False
            reason = heuristic
            hint = hint or heuristic
        return {
            "ok": ok,
            "reason": reason,
            "rewrite_hint": hint or reason,
            "source": "llm",
        }
    except Exception:  # noqa: BLE001
        return {
            "ok": False,
            "reason": heuristic,
            "rewrite_hint": heuristic,
            "source": "heuristic",
        }
