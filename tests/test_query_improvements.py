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


def test_secret_key_guard():
    from app.config import secret_key_is_insecure

    assert secret_key_is_insecure("dev-only-change-me")
    assert secret_key_is_insecure("short")
    assert not secret_key_is_insecure("a-sufficiently-long-random-secret")


def test_reject_update_without_primary_key():
    from app.mutate.builder import build_mutate_plan
    from app.query import SqlGuardError

    policy = {
        "connection_id": "c1",
        "tables": [
            {
                "table": "events",
                "schema_name": None,
                "allow_select": True,
                "allow_insert": True,
                "allow_update": True,
                "allow_delete": True,
                "allowed_columns": None,
                "denied_columns": [],
            }
        ],
        "require_where_for_update": True,
        "require_where_for_delete": True,
        "max_rows_per_mutation": 100,
    }
    try:
        build_mutate_plan(
            dialect="sqlite",
            policy=policy,
            operation="update",
            table="events",
            set_values={"status": "done"},
            filters=[{"column": "id", "op": "=", "value": 1}],
            primary_key=[],
        )
        assert False, "expected SqlGuardError"
    except SqlGuardError as exc:
        assert "primary key" in str(exc).lower()


def test_parse_mutate_sql_update():
    from app.mutate.sql_parse import parse_mutate_sql

    parsed = parse_mutate_sql(
        "UPDATE tasks SET status = 'done' WHERE id = 3",
        dialect="sqlite",
    )
    assert parsed["operation"] == "update"
    assert parsed["table"] == "tasks"
    assert parsed["set_values"] == {"status": "done"}
    assert parsed["filters"] == [{"column": "id", "op": "=", "value": 3}]


def test_parse_mutate_sql_insert():
    from app.mutate.sql_parse import parse_mutate_sql

    parsed = parse_mutate_sql(
        "INSERT INTO tasks (title, priority) VALUES ('fix timeout', 1)",
        dialect="sqlite",
    )
    assert parsed["operation"] == "insert"
    assert parsed["values"] == {"title": "fix timeout", "priority": 1}


def test_engine_pool_reuses_same_fingerprint():
    from app.db.engines import dispose_all_pooled_engines, get_pooled_engine

    dispose_all_pooled_engines()
    cfg = {
        "dialect": "sqlite",
        "host": None,
        "port": None,
        "database": ":memory:",
        "username": None,
        "password": None,
        "options": {"path": ":memory:pk-pool-test"},
    }
    a = get_pooled_engine(cfg)
    b = get_pooled_engine(cfg)
    assert a is b
    dispose_all_pooled_engines()


def test_redact_secret_columns_and_heuristics():
    from app.db.redact import REDACTED, redact_rows

    rows = redact_rows(
        [{"email": "a@b.com", "password_hash": "x", "api_token": "t", "name": "n"}],
        secret_columns={"email"},
    )
    assert rows[0]["email"] == REDACTED
    assert rows[0]["password_hash"] == REDACTED
    assert rows[0]["api_token"] == REDACTED
    assert rows[0]["name"] == "n"


def test_claim_pending_is_single_use(tmp_path, monkeypatch):
    import app.meta_db as meta_db
    from app.mutate.pending import claim_pending, create_pending, get_pending

    monkeypatch.setattr(meta_db, "META_DB_PATH", tmp_path / "claim-meta.db")
    meta_db.init_meta_db()
    created = meta_db.create_connection(
        {
            "name": "claim-test",
            "dialect": "sqlite",
            "database": str(tmp_path / "x.db"),
            "options": {"path": str(tmp_path / "x.db")},
        }
    )
    conn_id = created["id"]
    pending = create_pending(conn_id, {"operation": "update", "table": "t"})
    first, err1 = claim_pending(pending["id"], connection_id=conn_id)
    assert err1 == "" and first is not None
    assert first["status"] == "executing"
    second, err2 = claim_pending(pending["id"], connection_id=conn_id)
    assert second is None and err2 == "busy"
    assert get_pending(pending["id"])["status"] == "executing"


def test_preview_plan_rolls_back_dry_run(tmp_path):
    from sqlalchemy import create_engine, text

    from app.mutate.builder import MutatePlan
    from app.mutate.executor import preview_plan

    db = tmp_path / "preview.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, status TEXT)"))
        conn.execute(text("INSERT INTO items (id, status) VALUES (1, 'open')"))

    plan = MutatePlan(
        operation="update",
        table="items",
        schema_name=None,
        sql="UPDATE items SET status = 'closed' WHERE id = 1",
        count_sql="SELECT COUNT(*) FROM items WHERE id = 1",
        preview_sql="SELECT * FROM items WHERE id = 1",
        filters=[{"column": "id", "op": "=", "value": 1}],
        set_values={"status": "closed"},
    )
    stats = preview_plan(engine, plan, max_rows=100)
    assert stats["blocked"] is False
    assert stats["affected_count"] == 1
    assert stats["sample_rows"][0]["status"] == "open"

    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM items WHERE id = 1")).scalar()
    assert status == "open"


def test_auth_keys_match():
    from app.auth import keys_match

    assert keys_match("same-key-value", "same-key-value")
    assert not keys_match("a", "b")
    assert not keys_match("", "x")
