"""Turn sql_guard / policy failures into Chinese messages and rewrite hints."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.query.sql_guard import SqlGuardError


def explain_guard(message: str) -> dict[str, str]:
    text = (message or "").strip()
    lower = text.lower()

    def pack(msg: str, hint: str) -> dict[str, str]:
        return {"message": msg, "hint": hint}

    if "multiple sql" in lower:
        return pack("一次只能执行一条 SQL。", "请只保留一条 SELECT，不要用分号拼接多条语句。")
    if "only select" in lower or "forbidden sql" in lower or "select into" in lower:
        return pack(
            "当前查询通道只允许 SELECT。",
            "请改成只读查询；写入请切换到「写入」模式并走预览确认。",
        )
    if "parse error" in lower or "parse returned empty" in lower:
        return pack("SQL 无法解析。", "请检查表名/字段名是否写错，或改用自然语言重新提问。")
    if "sql is empty" in lower:
        return pack("没有生成可用的 SQL。", "请把问题说得更具体，例如要查哪张表、哪个名称。")
    if "could not determine target tables" in lower:
        return pack("无法识别 SQL 里的表。", "请使用当前选表范围内的真实表名。")
    if "not in whitelist" in lower:
        table = text.split(":")[-1].strip() if ":" in text else ""
        return pack(
            f"表不在当前允许范围内{('：' + table) if table else '。'}",
            "请回到选表步骤勾选该表，或改用已选中的表。",
        )
    if "not allowed" in lower and "operation" in lower:
        return pack("当前策略不允许这个操作。", "查询请用 SELECT；写入请确认该表已开放 insert/update/delete。")
    if "column denied" in lower or "filter column denied" in lower:
        return pack("查询用到了被禁止的字段。", "请去掉该列，或在策略中放开该字段。")
    if "column not in whitelist" in lower or "filter column not allowed" in lower:
        return pack("字段不在白名单中。", "请只选择策略允许的列。")
    if "select *" in lower:
        return pack(
            "配置了字段黑白名单时不允许 SELECT *。",
            "请显式写出需要的列名。",
        )
    if "requires a where" in lower or "requires where" in lower:
        return pack(
            "按安全策略，更新/删除必须带条件。",
            "请说明要改哪一条，例如按主键、名称或状态过滤。",
        )
    if "insert requires values" in lower:
        return pack("新增缺少字段值。", "请说明要写入哪些列以及对应的值。")
    if "update requires set_values" in lower or "set_values" in lower:
        return pack("更新缺少 SET 内容。", "请说明要把哪些字段改成什么值。")
    if "unsupported dialect" in lower:
        return pack("不支持该数据库类型。", "请使用 SQLite / MySQL / PostgreSQL / SQL Server。")
    if "unsupported filter op" in lower:
        return pack("过滤条件运算符不受支持。", "请使用 =、!=、>、>=、<、<= 或 like。")
    if "no selectable columns" in lower:
        return pack("没有可查询的字段。", "请检查该表的字段白名单是否过严。")
    if "access denied" in lower:
        return pack("访问被策略拒绝。", "请回到选表步骤确认表和操作权限。")
    if text:
        return pack(text if _looks_chinese(text) else "该 SQL 未通过安全校验。", "请换一种问法，或编辑 SQL 后重跑。")
    return pack("该 SQL 未通过安全校验。", "请换一种问法，或编辑 SQL 后重跑。")


def _looks_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def http_guard_error(exc: SqlGuardError) -> HTTPException:
    explained = explain_guard(exc.message)
    return HTTPException(
        status_code=400,
        detail={"message": explained["message"], "hint": explained["hint"]},
    )


def format_guard_hint(exc: SqlGuardError) -> str:
    explained = explain_guard(exc.message)
    return f"{explained['message']} {explained['hint']}".strip()
