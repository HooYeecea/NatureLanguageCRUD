"""Natural language -> structured mutation proposal via LLM."""

from __future__ import annotations

import json
from typing import Any, Optional

from openai import OpenAI

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from app.mutate.builder import MutatePlan, build_mutate_plan
from app.query.nl import LlmNotConfigured
from app.query.sql_guard import SqlGuardError


def _client() -> OpenAI:
    if not LLM_API_KEY:
        raise LlmNotConfigured(
            "LLM_API_KEY is not set. Add it to .env to enable natural language mutate."
        )
    return OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL or None)


def _schema_prompt(policy: dict[str, Any], schema_tables: list[dict[str, Any]]) -> str:
    writable = {
        t["table"]: t
        for t in (policy.get("tables") or [])
        if t.get("allow_insert") or t.get("allow_update") or t.get("allow_delete")
    }
    lines = []
    for table in schema_tables:
        name = table["name"]
        if name not in writable:
            continue
        tp = writable[name]
        ops = []
        if tp.get("allow_insert"):
            ops.append("insert")
        if tp.get("allow_update"):
            ops.append("update")
        if tp.get("allow_delete"):
            ops.append("delete")
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
        lines.append(f"- {qname} ops={ops} cols=({', '.join(col_parts)})")
    return "\n".join(lines) if lines else "(no writable tables)"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "propose_mutation",
            "description": (
                "Propose a single restricted mutation (insert/update/delete). "
                "UPDATE/DELETE must include filters. Never invent tables/columns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["insert", "update", "delete"],
                    },
                    "table": {"type": "string"},
                    "schema_name": {"type": "string"},
                    "values": {
                        "type": "object",
                        "description": "Column values for insert",
                    },
                    "set_values": {
                        "type": "object",
                        "description": "Column values for update SET",
                    },
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "op": {
                                    "type": "string",
                                    "enum": ["=", "!=", "<>", ">", ">=", "<", "<=", "like", "ilike"],
                                },
                                "value": {},
                            },
                            "required": ["column", "value"],
                        },
                    },
                    "explanation": {"type": "string"},
                },
                "required": ["operation", "table"],
            },
        },
    }
]


def nl_to_mutate_plan(
    prompt: str,
    *,
    dialect: str,
    policy: dict[str, Any],
    schema_tables: list[dict[str, Any]],
) -> tuple[Optional[MutatePlan], Optional[str], Optional[str]]:
    client = _client()
    schema_text = _schema_prompt(policy, schema_tables)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful database mutation assistant. "
                f"Dialect: {dialect}. "
                "Only call propose_mutation for write requests. "
                "Always include WHERE filters for update/delete. "
                "If the request is read-only, do not call a tool; explain instead.\n"
                f"Writable schema:\n{schema_text}"
            ),
        },
        {"role": "user", "content": prompt},
    ]
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
    )
    message = response.choices[0].message
    if not message.tool_calls:
        return None, None, message.content

    tool_call = message.tool_calls[0]
    if tool_call.function.name != "propose_mutation":
        raise SqlGuardError(f"Unexpected tool: {tool_call.function.name}")

    args = json.loads(tool_call.function.arguments or "{}")
    plan = build_mutate_plan(
        dialect=dialect,
        policy=policy,
        operation=args["operation"],
        table=args["table"],
        schema_name=args.get("schema_name"),
        values=args.get("values"),
        set_values=args.get("set_values"),
        filters=args.get("filters") or [],
    )
    return plan, args.get("explanation"), None
