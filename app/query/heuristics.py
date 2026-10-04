"""Cheap checks for obviously wrong SELECT results (no LLM)."""

from __future__ import annotations

import re
from typing import Any, Optional

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}")
_SKIP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "select",
    "table",
    "查询",
    "列出",
    "所有",
    "全部",
    "一下",
    "什么",
    "哪些",
    "帮我",
    "看看",
    "告诉",
    "显示",
    "权限",
}
_LIST_ALL = re.compile(r"所有|全部|list all|every\b", re.I)
_PERM_ASK = re.compile(r"权限|perms?|permission|授权")
_PERM_COL = re.compile(r"perm|menu|auth|acl|right|privilege|权限", re.I)


def extract_focus_tokens(prompt: str) -> list[str]:
    tokens = []
    for m in _WORD.finditer(prompt or ""):
        t = m.group(0)
        if t.lower() in _SKIP:
            continue
        if t not in tokens:
            tokens.append(t)
    return tokens[:12]


def heuristic_mismatch(
    prompt: str,
    sql: str,
    rows: list[dict[str, Any]],
    row_count: int,
) -> Optional[str]:
    sql_l = (sql or "").lower()
    has_where = " where " in f" {sql_l} "
    tokens = extract_focus_tokens(prompt)
    wants_all = bool(_LIST_ALL.search(prompt or ""))
    entity_tokens = [t for t in tokens if len(t) >= 3]

    # Named entity + no WHERE is wrong even if that row happens to be in a full dump.
    if not has_where and entity_tokens and not wants_all:
        return (
            "问题点名了具体对象，但 SQL 没有 WHERE，属于整表查询。"
            f"应对 {entity_tokens[0]} 加过滤，而不是返回整张表。"
        )

    if tokens and row_count > 0 and not wants_all:
        blob = " ".join(str(v) for row in rows[:30] for v in row.values()).lower()
        missing = [t for t in tokens if t.lower() not in blob and t.lower() not in sql_l]
        distinctive = [t for t in missing if len(t) >= 3]
        if distinctive and not has_where:
            return f"结果中未见问题关键词：{', '.join(distinctive[:5])}。"

    if _PERM_ASK.search(prompt or "") and rows:
        col_blob = " ".join(rows[0].keys())
        if not _PERM_COL.search(col_blob):
            return "问题在问权限，但结果列里没有权限/菜单相关字段，需要 JOIN 或改选列。"

    if has_where and row_count == 0 and tokens:
        return "带条件查询返回空结果，过滤条件可能过严或字段选错。"
    return None
