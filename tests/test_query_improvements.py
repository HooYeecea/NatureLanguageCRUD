# -*- coding: utf-8 -*-
from app.db.samples import format_samples_text, is_safe_ident, quote_ident
from app.query.heuristics import heuristic_mismatch


def test_quote_ident():
    assert quote_ident("sqlite", "sys_role") == '"sys_role"'
    assert quote_ident("mysql", "sys_role") == "`sys_role`"
    assert quote_ident("sqlserver", "sys_role") == "[sys_role]"


def test_reject_unsafe_ident():
    assert is_safe_ident("sys_role")
    assert is_safe_ident("角色表")
    assert not is_safe_ident('a"b')
    assert not is_safe_ident("a;drop")
    assert not is_safe_ident("role name")
    assert not is_safe_ident("sys_role;select")
    assert not is_safe_ident("dbo.sys_role")


def test_format_samples_text():
    text = format_samples_text({"sys_role": [{"role_name": "超级管理员", "perms": "*"}]})
    assert "超级管理员" in text
    assert "sys_role" in text


def test_heuristic_unfiltered_named_entity():
    note = heuristic_mismatch(
        "超级管理员有哪些权限",
        "SELECT role_id, role_name, status FROM sys_role LIMIT 500",
        [
            {"role_id": 1, "role_name": "超级管理员", "status": "0"},
            {"role_id": 2, "role_name": "普通角色", "status": "0"},
            {"role_id": 3, "role_name": "本部门及以下", "status": "0"},
            {"role_id": 100, "role_name": "人力资源", "status": "0"},
        ],
        4,
    )
    assert note
    assert "WHERE" in note


def test_heuristic_ok_when_filtered():
    note = heuristic_mismatch(
        "超级管理员有哪些权限",
        "SELECT perms FROM sys_role WHERE role_name = '超级管理员' LIMIT 10",
        [{"perms": "*"}],
        1,
    )
    assert note is None


def test_heuristic_list_all_allows_unfiltered():
    note = heuristic_mismatch(
        "\u5217\u51fa\u6240\u6709\u89d2\u8272",  # 列出所有角色
        "SELECT role_id, role_name FROM sys_role LIMIT 500",
        [{"role_id": 1, "role_name": "\u8d85\u7ea7\u7ba1\u7406\u5458"}],
        1,
    )
    assert note is None


def test_heuristic_table_alias():
    from app.glossary import glossary_context_text, heuristic_column_alias, heuristic_table_alias, merge_glossary

    assert heuristic_table_alias("sys_role") == "角色"
    assert heuristic_column_alias("perms") == "权限标识"
    existing = [{"table": "sys_role", "alias": "角色表", "synonyms": [], "columns": []}]
    suggested = [{"table": "sys_role", "alias": "角色", "synonyms": ["sys_role"], "columns": []}]
    merged = merge_glossary(existing, suggested)
    assert merged[0]["alias"] == "角色表"
    text = glossary_context_text(merged)
    assert "sys_role" in text
    assert "角色表" in text


def test_explain_guard_whitelist():
    from app.query.guard_explain import explain_guard

    explained = explain_guard("Table not in whitelist: sys_role")
    assert "不在当前允许范围" in explained["message"]
    assert explained["hint"]

    explained = explain_guard("UPDATE requires a WHERE filter under current policy")
    assert "必须带条件" in explained["message"]


def test_mutate_ack_threshold_default():
    from app.policy_store import default_policy_dict

    p = default_policy_dict("c1")
    assert p["confirm_rows_threshold"] == 10


def test_readback_delete_uses_before_rows():
    from app.mutate.builder import MutatePlan
    from app.mutate.executor import readback_rows

    plan = MutatePlan(
        operation="delete",
        table="tasks",
        schema_name=None,
        sql="DELETE FROM tasks WHERE id = 1",
        count_sql="SELECT 1",
        preview_sql="SELECT * FROM tasks WHERE id = 1",
        filters=[{"column": "id", "op": "=", "value": 1}],
    )
    before = [{"id": 1, "title": "x"}]
    # engine unused for delete path
    rows = readback_rows(None, plan, dialect="sqlite", before_rows=before)  # type: ignore[arg-type]
    assert rows == before


def test_critique_cheap_skips_llm_when_ok():
    from app.query.validate import critique_query_result

    out = critique_query_result(
        "列出所有角色",
        "SELECT role_id, role_name FROM sys_role WHERE 1=1 LIMIT 10",
        ["role_id", "role_name"],
        [{"role_id": 1, "role_name": "admin"}],
        1,
        use_llm=False,
    )
    assert out["ok"] is True
    assert out["source"] == "heuristic"


def test_critique_cheap_flags_unfiltered():
    from app.query.validate import critique_query_result

    out = critique_query_result(
        "超级管理员有哪些权限",
        "SELECT role_id, role_name FROM sys_role LIMIT 500",
        ["role_id", "role_name"],
        [{"role_id": 1, "role_name": "超级管理员"}, {"role_id": 2, "role_name": "普通"}],
        2,
        use_llm=False,
    )
    assert out["ok"] is False
    assert "WHERE" in out["reason"] or "整表" in out["reason"]
    assert out["rewrite_hint"]
