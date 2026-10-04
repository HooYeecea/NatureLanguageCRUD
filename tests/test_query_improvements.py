from app.db.samples import format_samples_text, quote_ident
from app.query.heuristics import heuristic_mismatch


def test_quote_ident():
    assert quote_ident("sqlite", 'a"b') == '"a""b"'
    assert quote_ident("mysql", "a`b") == "`a``b`"
    assert quote_ident("sqlserver", "a]b") == "[a]]b]"


def test_format_samples_text():
    text = format_samples_text({"sys_role": [{"role_name": "超级管理员", "perms": "*"}]})
    assert "超级管理员" in text
    assert "sys_role" in text


def test_heuristic_unfiltered_dump():
    note = heuristic_mismatch(
        "超级管理员有哪些权限",
        "SELECT * FROM sys_role LIMIT 500",
        [{"role_id": i, "role_name": f"r{i}"} for i in range(12)],
        12,
    )
    assert note


def test_heuristic_ok_when_filtered():
    note = heuristic_mismatch(
        "超级管理员有哪些权限",
        "SELECT perms FROM sys_role WHERE role_name = '超级管理员' LIMIT 10",
        [{"perms": "*"}],
        1,
    )
    assert note is None
