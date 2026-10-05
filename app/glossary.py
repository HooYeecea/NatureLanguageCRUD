"""Business glossary: short aliases for tables/columns."""

from __future__ import annotations

import re
from typing import Any, Optional

_PREFIXES = ("sys_", "tb_", "t_", "tbl_", "t_sys_")
_TOKEN_ALIAS = {
    "role": "角色",
    "roles": "角色",
    "user": "用户",
    "users": "用户",
    "menu": "菜单",
    "dept": "部门",
    "depart": "部门",
    "department": "部门",
    "dict": "字典",
    "log": "日志",
    "config": "配置",
    "conf": "配置",
    "perm": "权限",
    "perms": "权限",
    "permission": "权限",
    "order": "订单",
    "task": "任务",
    "job": "任务",
    "post": "岗位",
    "notice": "通知",
    "file": "文件",
    "oper": "操作",
    "login": "登录",
    "session": "会话",
    "token": "令牌",
    "name": "名称",
    "status": "状态",
    "type": "类型",
    "remark": "备注",
    "sort": "排序",
    "parent": "父级",
    "create": "创建",
    "update": "更新",
    "time": "时间",
    "id": "编号",
}
_COL_ALIAS = {
    "perms": "权限标识",
    "permission": "权限",
    "role_name": "角色名",
    "role_key": "角色权限字符",
    "user_name": "用户名",
    "nick_name": "昵称",
    "phonenumber": "手机号",
    "dept_name": "部门名",
    "menu_name": "菜单名",
    "parent_id": "父级编号",
    "status": "状态",
    "del_flag": "删除标记",
    "create_time": "创建时间",
    "update_time": "更新时间",
    "remark": "备注",
}


def _strip_prefix(name: str) -> str:
    lower = name.lower()
    for p in _PREFIXES:
        if lower.startswith(p):
            return name[len(p) :]
    return name


def heuristic_table_alias(table: str) -> str:
    stem = _strip_prefix(table or "")
    parts = [p for p in re.split(r"[_\-]+", stem) if p]
    if not parts:
        return table
    return "".join(_TOKEN_ALIAS.get(p.lower(), p) for p in parts)


def heuristic_column_alias(column: str) -> Optional[str]:
    if column in _COL_ALIAS:
        return _COL_ALIAS[column]
    lower = column.lower()
    if lower in _COL_ALIAS:
        return _COL_ALIAS[lower]
    parts = [p for p in re.split(r"[_\-]+", column) if p]
    if not parts:
        return None
    if all(p.lower() in _TOKEN_ALIAS for p in parts):
        return "".join(_TOKEN_ALIAS[p.lower()] for p in parts)
    return None


def _short_purpose(purpose: Optional[str]) -> Optional[str]:
    if not purpose:
        return None
    first = re.split(r"[，。,;；]", purpose.strip())[0].strip()
    first = re.sub(r"^表\s*", "", first)
    if 2 <= len(first) <= 20:
        return first
    return None


def suggest_glossary(
    tables: list[dict[str, Any]],
    analysis: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    purpose_by_name = {}
    for t in (analysis or {}).get("tables") or []:
        if t.get("name"):
            purpose_by_name[t["name"]] = t.get("purpose")
    out = []
    for t in tables:
        name = t.get("name") or t.get("table")
        if not name:
            continue
        schema_name = t.get("schema_name")
        alias = _short_purpose(purpose_by_name.get(name)) or heuristic_table_alias(name)
        synonyms = []
        heu = heuristic_table_alias(name)
        if heu and heu != alias:
            synonyms.append(heu)
        if alias != name:
            synonyms.append(name)
        columns = []
        for c in t.get("columns") or []:
            cname = c.get("name") if isinstance(c, dict) else getattr(c, "name", None)
            if not cname:
                continue
            calias = heuristic_column_alias(cname)
            if calias:
                columns.append({"name": cname, "alias": calias, "synonyms": []})
        out.append(
            {
                "table": name,
                "schema_name": schema_name,
                "alias": alias,
                "synonyms": list(dict.fromkeys(synonyms)),
                "columns": columns,
            }
        )
    return out


def merge_glossary(
    existing: list[dict[str, Any]],
    suggested: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key = {}
    order = []
    for item in existing:
        key = (item.get("schema_name") or "", item.get("table") or "")
        by_key[key] = item
        order.append(key)
    for item in suggested:
        key = (item.get("schema_name") or "", item.get("table") or "")
        if key not in by_key:
            by_key[key] = item
            order.append(key)
            continue
        cur = by_key[key]
        if not (cur.get("alias") or "").strip():
            cur["alias"] = item.get("alias")
        syn = list(cur.get("synonyms") or [])
        for s in item.get("synonyms") or []:
            if s not in syn:
                syn.append(s)
        cur["synonyms"] = syn
        cols = {c.get("name"): c for c in (cur.get("columns") or []) if c.get("name")}
        for c in item.get("columns") or []:
            name = c.get("name")
            if not name:
                continue
            if name not in cols:
                cols[name] = c
            elif not (cols[name].get("alias") or "").strip():
                cols[name]["alias"] = c.get("alias")
        cur["columns"] = list(cols.values())
    return [by_key[k] for k in order if k in by_key]


def glossary_context_text(entries: Optional[list[dict[str, Any]]]) -> str:
    if not entries:
        return ""
    lines = [
        "## Business glossary (map user language to real table/column names; never invent names)"
    ]
    for item in entries:
        table = item.get("table")
        if not table:
            continue
        alias = item.get("alias") or ""
        syn = ", ".join(item.get("synonyms") or [])
        extra = f" alias={alias}" if alias else ""
        if syn:
            extra += f" also_called={syn}"
        lines.append(f"- Table {table}:{extra}")
        for c in item.get("columns") or []:
            cname = c.get("name")
            calias = c.get("alias") or ""
            if cname and calias:
                lines.append(f"    - {cname} = {calias}")
    return "\n".join(lines)
