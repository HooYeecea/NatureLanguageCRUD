"""LLM-assisted interpretation of selected tables and relationships."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from app.query.nl import LlmNotConfigured


def _fallback_from_metadata(tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic summary from PK/FK when LLM is unavailable."""
    table_briefs = []
    relationships = []
    join_hints = []

    for t in tables:
        name = t.get("name")
        cols = [c.get("name") for c in (t.get("columns") or [])]
        pk = t.get("primary_key") or []
        table_briefs.append(
            {
                "name": name,
                "purpose": f"表 {name}，共 {len(cols)} 个字段",
                "key_fields": pk or cols[:3],
            }
        )
        for fk in t.get("foreign_keys") or []:
            referred = fk.get("referred_table") or ""
            via_cols = ", ".join(fk.get("constrained_columns") or [])
            ref_cols = ", ".join(fk.get("referred_columns") or [])
            relationships.append(
                {
                    "from_table": name,
                    "to_table": referred,
                    "via": f"{via_cols} → {referred}.{ref_cols}",
                    "note": "来自数据库外键约束",
                }
            )
            join_hints.append(
                f"JOIN {referred} ON {name}.{via_cols} = {referred}.{ref_cols}"
            )

    return {
        "overview": (
            f"已基于元数据解析 {len(tables)} 张表"
            + ("；发现外键关系如下。" if relationships else "；未发现显式外键，可能存在逻辑关联。")
        ),
        "tables": table_briefs,
        "relationships": relationships,
        "join_hints": join_hints,
        "warnings": (
            []
            if relationships
            else ["未检测到外键。可配置 LLM_API_KEY 让模型推断可能的逻辑关系。"]
        ),
        "source": "metadata",
    }


def interpret_schema(
    *,
    dialect: str,
    tables: list[dict[str, Any]],
    use_llm: bool = True,
) -> dict[str, Any]:
    fallback = _fallback_from_metadata(tables)
    if not use_llm:
        return fallback
    if not LLM_API_KEY:
        fallback["warnings"] = list(fallback.get("warnings") or []) + [
            "未配置 LLM_API_KEY，当前仅返回元数据关系摘要。"
        ]
        return fallback

    compact = []
    for t in tables:
        compact.append(
            {
                "schema_name": t.get("schema_name"),
                "name": t.get("name"),
                "columns": [
                    {
                        "name": c.get("name"),
                        "type": c.get("type"),
                        "nullable": c.get("nullable"),
                        "primary_key": c.get("primary_key"),
                    }
                    for c in (t.get("columns") or [])
                ],
                "primary_key": t.get("primary_key") or [],
                "foreign_keys": t.get("foreign_keys") or [],
            }
        )

    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL or None)
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a database modeling assistant. "
                    f"Dialect: {dialect}. "
                    "Given real table metadata (columns, PK, FK), explain in Chinese: "
                    "1) each table's purpose, 2) how tables relate, 3) suggested join paths, "
                    "4) caveats for CRUD. "
                    "Return JSON only with keys: "
                    "overview (string), "
                    "tables (array of {name, purpose, key_fields}), "
                    "relationships (array of {from_table, to_table, via, note}), "
                    "join_hints (array of string), "
                    "warnings (array of string)."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(compact, ensure_ascii=False),
            },
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {
            "overview": content,
            "tables": fallback["tables"],
            "relationships": fallback["relationships"],
            "join_hints": fallback["join_hints"],
            "warnings": ["模型返回非 JSON，已保留原文 overview。"],
        }
    parsed["source"] = "llm"
    # Merge FK facts if model omitted them
    if not parsed.get("relationships") and fallback["relationships"]:
        parsed["relationships"] = fallback["relationships"]
    return parsed


def llm_status() -> dict[str, Any]:
    return {
        "configured": bool(LLM_API_KEY),
        "base_url": LLM_BASE_URL,
        "model": LLM_MODEL,
    }
