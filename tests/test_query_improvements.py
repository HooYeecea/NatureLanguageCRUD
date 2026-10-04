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
