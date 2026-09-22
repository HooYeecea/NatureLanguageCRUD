"""Natural language -> guarded SELECT via LLM tool calling."""

from __future__ import annotations

import json
from typing import Any, Optional

from openai import OpenAI

from app.query.sql_guard import SqlGuardError, guard_select_sql
from app.settings_store import get_llm_settings


class LlmNotConfigured(Exception):
    pass


def _client() -> OpenAI:
    cfg = get_llm_settings()
    if not cfg["api_key"]:
        raise LlmNotConfigured(
            "LLM API Key 未配置。请在界面右上角「API 设置」中填写，或设置环境变量 LLM_API_KEY。"
        )
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)


def _schema_prompt(policy: dict[str, Any], schema_tables: list[dict[str, Any]]) -> str:
    allowed = {t["table"]: t for t in (policy.get("tables") or []) if t.get("allow_select")}
    lines = []
    for table in schema_tables:
        name = table["name"]
        if name not in allowed:
            continue
        tp = allowed[name]
        cols = table.get("columns") or []
        denied = set(tp.get("denied_columns") or [])
        allow = tp.get("allowed_columns")
        col_parts = []
        for c in cols:
            cname = c["name"] if isinstance(c, dict) else c.name
            ctype = c.get("type", "") if isinstance(c, dict) else getattr(c, "type", "")
            if cname in denied:
                continue
            if allow is not None and cname not in allow:
                continue
            col_parts.append(f"{cname}:{ctype}")
        schema_name = table.get("schema_name")
        qname = f"{schema_name}.{name}" if schema_name else name
        lines.append(f"- {qname}({', '.join(col_parts)})")
    return "\n".join(lines) if lines else "(no selectable tables)"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_select_sql",
            "description": (
                "Run a single read-only SELECT query. "
                "Use only whitelisted tables/columns. Always include LIMIT."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A single SELECT statement (no DDL/DML).",
                    },
                    "explanation": {
                        "type": "string",
                        "description": "Short explanation of what the query does.",
                    },
                },
                "required": ["sql"],
            },
        },
    }
]


def nl_to_guarded_sql(
    prompt: str,
    *,
    dialect: str,
    policy: dict[str, Any],
    schema_tables: list[dict[str, Any]],
) -> tuple[Any, Optional[str], Optional[str]]:
    """
    Returns (GuardedQuery|None, explanation, assistant_text_if_no_tool).
    """
    client = _client()
    schema_text = _schema_prompt(policy, schema_tables)
    max_rows = policy.get("max_rows_per_query") or 500

    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful SQL assistant for an internal CRUD workbench. "
                f"Dialect: {dialect}. "
                "You must only call run_select_sql with a single SELECT. "
                "Never invent tables/columns outside the schema. "
                f"Respect max rows <= {max_rows}. "
                "If the request is not a read query, explain that writes are not available on this endpoint.\n"
                f"Allowed schema:\n{schema_text}"
            ),
        },
        {"role": "user", "content": prompt},
    ]

    response = client.chat.completions.create(
        model=get_llm_settings()["model"],
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
    )
    message = response.choices[0].message

    if not message.tool_calls:
        return None, None, message.content

    tool_call = message.tool_calls[0]
    if tool_call.function.name != "run_select_sql":
        raise SqlGuardError(f"Unexpected tool: {tool_call.function.name}")

    args = json.loads(tool_call.function.arguments or "{}")
    sql = args.get("sql") or ""
    explanation = args.get("explanation")
    guarded = guard_select_sql(sql, dialect=dialect, policy=policy)
    return guarded, explanation, None
