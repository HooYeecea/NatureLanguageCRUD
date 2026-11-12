import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.query.sql_guard import guard_select_sql, build_structured_select, SqlGuardError
from app.query.executor import execute_select
from app.mutate.builder import build_mutate_plan
from app.mutate.executor import execute_plan, preview_plan, restore_rows


@pytest.fixture
def policy():
    return {"max_rows_per_query": 10, "tables": [
        {"table": "users", "allow_select": True, "allow_update": True,
         "allow_delete": True, "allow_insert": True, "denied_columns": ["salary"]},
        {"table": "tasks", "allow_select": True},
    ]}


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users(id INTEGER PRIMARY KEY, salary INTEGER, password TEXT, flag INTEGER)"))
        conn.execute(text("INSERT INTO users VALUES(1,100,'fake-secret',0),(2,200,'other-secret',0)"))
        conn.execute(text("CREATE TABLE tasks(id INTEGER)"))
        conn.execute(text("INSERT INTO tasks VALUES(1)"))
    yield engine
    engine.dispose()


@pytest.mark.parametrize("sql", [
    "SELECT u.salary FROM users u JOIN tasks t ON u.id=t.id",
    "SELECT id FROM users UNION ALL SELECT salary FROM users",
    "SELECT id+salary AS value FROM users",
    "SELECT id FROM users WHERE salary=100",
    "SELECT id FROM users ORDER BY salary",
    "SELECT id FROM users GROUP BY salary",
    "SELECT SALARY FROM users",
    "SELECT u.* FROM users u",
    "SELECT (SELECT salary FROM users) FROM tasks",
    "WITH x AS (SELECT salary FROM users) SELECT salary FROM x",
    "SELECT password FROM users WHERE password='guess'",
    "SELECT LENGTH(password) FROM users",
    "SELECT id FROM users JOIN tasks USING(password)",
    "SELECT * FROM (SELECT * FROM users) x",
    "SELECT id FROM other_catalog.public.users",
    "SELECT CAST(u AS TEXT) FROM users u",
])
def test_column_bypasses_rejected(sql, policy):
    with pytest.raises(SqlGuardError):
        guard_select_sql(sql, dialect="sqlite", policy=policy)


@pytest.mark.parametrize("sql", [
    "SELECT password AS value FROM users",
    "SELECT PASSWORD AS value FROM users",
    "SELECT x.value FROM (SELECT password AS value FROM users) x",
    "WITH x AS (SELECT password AS value FROM users) SELECT value FROM x",
    "SELECT password AS value FROM users UNION ALL SELECT password FROM users",
])
def test_alias_secrets_never_reach_executor(sql, policy, db):
    guarded = guard_select_sql(sql, dialect="sqlite", policy=policy)
    rows = execute_select(db, guarded.sql)["rows"]
    assert rows and all(set(row.values()) == {"[redacted]"} for row in rows)


@pytest.mark.parametrize("sql,dialect", [
    ("SELECT id INTO tasks FROM users", "sqlserver"),
    ("SELECT pg_sleep(5) FROM users", "postgresql"),
    ("SELECT load_file('/etc/passwd') FROM users", "mysql"),
    ("SELECT id FROM users FOR UPDATE", "postgresql"),
    ("SELECT evil.lower(id) FROM users", "postgresql"),
])
def test_readonly_bypasses_rejected(sql, dialect, policy):
    with pytest.raises(SqlGuardError):
        guard_select_sql(sql, dialect=dialect, policy=policy)


def test_structured_blacklist_and_empty_allowlist(policy, db):
    guarded = build_structured_select(dialect="sqlite", policy=policy, table="users",
        all_columns=["id", "salary", "password", "flag"])
    rows = execute_select(db, guarded.sql)["rows"]
    assert "salary" not in rows[0]
    assert rows[0]["password"] == "[redacted]"
    with pytest.raises(SqlGuardError):
        build_structured_select(dialect="sqlite", policy=policy, table="users")
    policy["tables"][0]["allowed_columns"] = []
    with pytest.raises(SqlGuardError):
        build_structured_select(dialect="sqlite", policy=policy, table="users", all_columns=["id"])


def plan_for(policy, operation="update"):
    return build_mutate_plan(dialect="sqlite", policy=policy, operation=operation,
        table="users", set_values={"flag": 1} if operation == "update" else None,
        filters=[{"column": "id", "op": ">", "value": 0}], primary_key=["id"])


def test_mutation_live_limit_and_ack_roll_back(policy, db):
    plan = plan_for(policy)
    assert "salary" not in preview_plan(db, plan, max_rows=2)["sample_rows"][0]
    with db.begin() as conn:
        conn.execute(text("INSERT INTO users VALUES(3,300,'fake-third',0)"))
    with pytest.raises(SqlGuardError, match="max_rows"):
        execute_plan(db, plan, max_rows=2, ack_large_impact=True)
    with pytest.raises(SqlGuardError, match="acknowledgement"):
        execute_plan(db, plan, max_rows=3, ack_threshold=3)
    with db.connect() as conn:
        assert conn.execute(text("SELECT SUM(flag) FROM users")).scalar() == 0


def test_complete_snapshot_and_atomic_restore(policy, db):
    with db.begin() as conn:
        conn.execute(text("INSERT INTO users VALUES(:id,100,'fake',0)"), [{"id": n} for n in range(3, 31)])
    result = execute_plan(db, plan_for(policy, "delete"), max_rows=30, ack_large_impact=True)
    assert len(result["before_rows"]) == result["rowcount"] == 30
    with db.begin() as conn:
        conn.execute(text("INSERT INTO users VALUES(30,0,'conflict',0)"))
    with pytest.raises(Exception):
        restore_rows(db, "users", None, result["before_rows"], ack_large_impact=True)
    with db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
    with db.begin() as conn:
        conn.execute(text("DELETE FROM users"))
    restore_rows(db, "users", None, result["before_rows"], ack_large_impact=True)
    with db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 30


def test_snapshot_failure_rolls_back_delete(policy, db):
    def fail(rows, pk):
        raise RuntimeError("snapshot unavailable")
    with pytest.raises(RuntimeError):
        execute_plan(db, plan_for(policy, "delete"), snapshot_writer=fail)
    with db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 2


def test_sensitive_mutation_filter_rejected(policy):
    with pytest.raises(SqlGuardError):
        build_mutate_plan(dialect="sqlite", policy=policy, operation="delete", table="users",
            filters=[{"column": "password", "op": "=", "value": "guess"}], primary_key=["id"])


def test_query_readonly_limit_and_connection_reset(db):
    with pytest.raises(Exception):
        execute_select(db, "DELETE FROM users")
    with pytest.raises(ValueError):
        execute_select(db, "SELECT id FROM users", max_rows=1)
    with db.begin() as conn:
        conn.execute(text("UPDATE users SET flag=1"))


def test_query_timeout(db):
    sql = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT SUM(x) FROM n"
    with pytest.raises(Exception):
        execute_select(db, sql, timeout_seconds=0)
    assert execute_select(db, "SELECT id FROM users")["row_count"] == 2


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import config, meta_db, main, auth
    from app.db.engines import dispose_all_pooled_engines
    monkeypatch.setattr(config, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "META_DB_PATH", tmp_path / "workbench.db")
    monkeypatch.setattr(meta_db, "META_DB_PATH", tmp_path / "workbench.db")
    monkeypatch.setattr(config, "DEMO_DB_PATH", tmp_path / "demo.db")
    monkeypatch.setattr(main, "DEMO_DB_PATH", tmp_path / "demo.db")
    monkeypatch.setattr("app.demo_seed.DEMO_DB_PATH", tmp_path / "demo.db")
    monkeypatch.setattr(config, "WORKBENCH_ENV", "development")
    monkeypatch.setattr(config, "WORKBENCH_API_KEY", "")
    monkeypatch.setattr(auth, "WORKBENCH_API_KEY", "")
    monkeypatch.setattr(config, "WORKBENCH_ADMIN_API_KEY", "")
    dispose_all_pooled_engines()
    with TestClient(main.app) as client:
        yield client
    dispose_all_pooled_engines()


def test_admin_operator_connection_scope(client, monkeypatch):
    from app import config, auth
    cid = client.get("/api/connections").json()[0]["id"]
    monkeypatch.setattr(config, "WORKBENCH_API_KEY", "reader-key")
    monkeypatch.setattr(auth, "WORKBENCH_API_KEY", "reader-key")
    monkeypatch.setattr(config, "WORKBENCH_ADMIN_API_KEY", "admin-key")
    monkeypatch.setattr(config, "WORKBENCH_CONNECTION_IDS", {cid})
    monkeypatch.setattr(config, "WORKBENCH_API_ROLE", "reader")
    headers = {"X-API-Key": "reader-key"}
    assert client.get("/api/connections", headers=headers).status_code == 200
    assert client.post(f"/api/connections/{cid}/query/sql", headers=headers, json={"sql": "SELECT id FROM users"}).status_code == 200
    assert client.put("/api/settings", headers=headers, json={"base_url": "https://evil.test"}).status_code == 403
    assert client.post(f"/api/connections/{cid}/policy/allow-all", headers=headers).status_code == 403
    assert client.post(f"/api/connections/{cid}/mutate/preview", headers=headers, json={}).status_code == 403
    assert client.post("/api/connections/other/query/sql", headers=headers, json={"sql": "SELECT id FROM users"}).status_code == 403
    monkeypatch.setattr(config, "WORKBENCH_API_ROLE", "writer")
    assert client.post(f"/api/connections/{cid}/mutate/preview", headers=headers, json={}).status_code == 422
    assert client.get(f"/api/connections/{cid}/schema", headers={"X-API-Key": "admin-key"}).status_code == 200


def test_metadata_path_and_host_denied(client, tmp_path, monkeypatch):
    from app.security import validate_connection
    for path in [tmp_path / "workbench.db", tmp_path.parent / "outside.db"]:
        response = client.post("/api/connections", json={"name": "blocked", "dialect": "sqlite", "database": str(path)})
        assert response.status_code == 400
    monkeypatch.setenv("WORKBENCH_DB_HOSTS", "db.example")
    with pytest.raises(ValueError):
        validate_connection({"dialect": "postgresql", "host": "127.0.0.1", "database": "postgres"})
    assert validate_connection({"dialect": "postgresql", "host": "db.example", "database": "app"})


def test_endpoint_change_drops_old_key(client, monkeypatch):
    from app import settings_store
    monkeypatch.setattr(settings_store, "LLM_API_KEY", "fake-env-secret")
    monkeypatch.setattr(settings_store, "LLM_BASE_URL", "https://api.deepseek.com")
    assert settings_store.get_llm_settings()["api_key"] == "fake-env-secret"
    settings_store.update_llm_settings(api_key="fake-ui-secret")
    cfg = settings_store.update_llm_settings(base_url="https://api.openai.com/v1")
    assert not cfg["configured"] and not cfg["api_key"]
    cfg = settings_store.update_llm_settings(base_url="https://api.deepseek.com")
    assert not cfg["api_key"]
    assert client.put("/api/settings", json={"base_url": "http://127.0.0.1"}).status_code == 400
    assert client.put("/api/settings", json={"base_url": "https://evil.test"}).status_code == 400


def test_encrypted_payload_and_audit(client):
    from app import secure_payload, audit, meta_db
    from app.mutate.pending import create_pending
    from app.mutate.undo import create_undo, get_undo
    cid = client.get("/api/connections").json()[0]["id"]
    payload = [{"password": "fake-secret", "decimal": Decimal("1.23456789"),
                "date": datetime.now(timezone.utc), "binary": b"\x00\xff"}]
    assert secure_payload.loads(secure_payload.dumps(payload)) == payload
    undo = create_undo(cid, table="users", schema_name=None, dialect="sqlite", rows=payload, primary_key=["id"])
    create_pending(cid, {"values": {"password": "fake-secret"}})
    assert get_undo(undo["id"])["rows"] == payload
    with meta_db.meta_conn() as conn:
        assert "fake-secret" not in conn.execute("SELECT rows_json FROM undo_mutations").fetchone()[0]
        assert "fake-secret" not in conn.execute("SELECT plan_json FROM pending_mutations").fetchone()[0]
    log = audit.write_audit(action="test", summary="fake-secret", detail={"sql": "SELECT 'fake-secret'", "prompt": "fake-secret", "nested": {"password": "fake-secret"}, "row_count": 1})
    assert "fake-secret" not in str(log)
    assert log["detail"]["row_count"] == 1


def test_compose_secret_rejected():
    from app.config import secret_key_is_insecure
    assert secret_key_is_insecure("dev-compose-change-me-16")


def test_delete_and_undo_25_rows_through_api(client):
    from app import meta_db
    from app.db.engines import get_pooled_engine
    from app.db.schema import get_engine_cfg
    cid = client.get("/api/connections").json()[0]["id"]
    engine = get_pooled_engine(get_engine_cfg(meta_db.get_connection(cid, include_password=True)))
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE security_rows(id INTEGER PRIMARY KEY, value TEXT)"))
        conn.execute(text("INSERT INTO security_rows VALUES(:id,:value)"),
                     [{"id": n, "value": f"row-{n}"} for n in range(1, 26)])
    response = client.put(f"/api/connections/{cid}/policy", json={"tables": [
        {"table": "security_rows", "allow_select": True, "allow_delete": True, "allow_insert": True}
    ]})
    assert response.status_code == 200
    preview = client.post(f"/api/connections/{cid}/mutate/preview", json={
        "operation": "delete", "table": "security_rows", "filters": [{"column": "id", "op": ">", "value": 0}]})
    assert preview.status_code == 200, preview.text
    executed = client.post(f"/api/connections/{cid}/mutate/confirm", json={
        "preview_id": preview.json()["preview_id"], "ack_large_impact": True})
    assert executed.status_code == 200, executed.text
    assert executed.json()["rowcount"] == 25
    undo_id = executed.json()["undo_id"]
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO security_rows VALUES(25,'conflict')"))
    failed = client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}", json={"ack_large_impact": True})
    assert failed.status_code == 400
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM security_rows")).scalar() == 1
        conn.execute(text("DELETE FROM security_rows"))
    restored = client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}", json={"ack_large_impact": True})
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored_count"] == 25
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM security_rows")).scalar() == 25
    assert client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}", json={"ack_large_impact": True}).status_code == 409


def test_development_rejects_host_and_origin_attacks(client):
    assert client.post("/api/connections/not-real/policy/allow-all", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/connections", headers={"Host": "evil.example"}).status_code == 403


def test_expired_snapshot_payload_is_erased(client):
    from app import meta_db, secure_payload
    from app.mutate.undo import create_undo
    from app.maintenance import clean_payloads
    cid = client.get("/api/connections").json()[0]["id"]
    undo = create_undo(cid, table="users", schema_name=None, dialect="sqlite",
                       rows=[{"id": 1, "secret": "fake"}], primary_key=["id"])
    with meta_db.meta_conn() as conn:
        conn.execute("UPDATE undo_mutations SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (undo["id"],))
    clean_payloads()
    with meta_db.meta_conn() as conn:
        row = conn.execute("SELECT rows_json,status FROM undo_mutations WHERE id=?", (undo["id"],)).fetchone()
    assert row["status"] == "expired"
    assert secure_payload.loads(row["rows_json"]) == []


@pytest.mark.parametrize("sql", [
    "SELECT CAST(u.* AS TEXT) AS value FROM users u",
    "SELECT u.* || '' AS value FROM users u",
    "SELECT COUNT(u.*) FROM users u",
    "SELECT COALESCE(u.*, NULL) FROM users u",
])
def test_postgres_nested_wildcards_rejected(sql, policy):
    with pytest.raises(SqlGuardError, match="wildcard"):
        guard_select_sql(sql, dialect="postgresql", policy=policy)
    assert "COUNT(*)" in guard_select_sql("SELECT COUNT(*) FROM users", dialect="postgresql", policy=policy).sql


def _mutation_fixture(client, operation="update"):
    from app import meta_db
    from app.db.engines import get_pooled_engine
    from app.db.schema import get_engine_cfg
    cid = client.get("/api/connections").json()[0]["id"]
    engine = get_pooled_engine(get_engine_cfg(meta_db.get_connection(cid, include_password=True)))
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE revision_rows(id INTEGER PRIMARY KEY, value INTEGER)"))
        conn.execute(text("INSERT INTO revision_rows VALUES(1,10),(2,20),(3,30)"))
    rules = {"tables": [{"table": "revision_rows", "allow_select": True, "allow_insert": True,
                         "allow_update": True, "allow_delete": True}],
             "max_rows_per_mutation": 10, "confirm_rows_threshold": 10}
    assert client.put(f"/api/connections/{cid}/policy", json=rules).status_code == 200
    body = {"operation": operation, "table": "revision_rows",
            "filters": [{"column": "id", "op": ">", "value": 0}]}
    if operation == "update":
        body["set_values"] = {"value": 50}
    preview = client.post(f"/api/connections/{cid}/mutate/preview", json=body)
    assert preview.status_code == 200, preview.text
    return cid, engine, preview.json()["preview_id"], rules


def test_connection_retarget_blocks_old_preview_and_undo(client, tmp_path):
    cid, engine, preview_id, _ = _mutation_fixture(client, "delete")
    executed = client.post(f"/api/connections/{cid}/mutate/confirm", json={"preview_id": preview_id})
    assert executed.status_code == 200, executed.text
    undo_id = executed.json()["undo_id"]
    preview = client.post(f"/api/connections/{cid}/mutate/preview", json={
        "operation": "insert", "table": "revision_rows", "values": {"id": 4, "value": 40}}).json()
    other = create_engine("sqlite:///" + str(tmp_path / "other.db"))
    with other.begin() as conn:
        conn.execute(text("CREATE TABLE revision_rows(id INTEGER PRIMARY KEY,value INTEGER)"))
        conn.execute(text("INSERT INTO revision_rows VALUES(1,999)"))
    response = client.put(f"/api/connections/{cid}", json={"database": str(tmp_path / "other.db"), "options": {"path": str(tmp_path / "other.db")}})
    assert response.status_code == 200, response.text
    assert client.post(f"/api/connections/{cid}/mutate/confirm", json={"preview_id": preview["preview_id"]}).status_code == 409
    assert client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}").status_code == 409
    with other.connect() as conn:
        assert conn.execute(text("SELECT * FROM revision_rows")).all() == [(1, 999)]
    other.dispose()


def test_claim_wins_against_cancel_and_retarget(client):
    from app.mutate.pending import claim_pending, mark_cancelled, get_pending
    cid, _, pid, _ = _mutation_fixture(client)
    pending, error = claim_pending(pid, connection_id=cid)
    assert pending and not error
    assert not mark_cancelled(pid)
    assert get_pending(pid)["status"] == "executing"
    assert client.post(f"/api/connections/{cid}/mutate/cancel/{pid}").status_code == 409
    assert client.put(f"/api/connections/{cid}", json={"name": "new"}).status_code == 409
    assert client.delete(f"/api/connections/{cid}").status_code == 409


def test_revise_losing_cancel_race_creates_no_preview(client, monkeypatch):
    from app.api import mutate
    from app import meta_db
    cid, _, pid, _ = _mutation_fixture(client)
    monkeypatch.setattr(mutate, "mark_cancelled", lambda _: False)
    response = client.post(f"/api/connections/{cid}/mutate/revise", json={"preview_id": pid, "sql": "UPDATE revision_rows SET value=90 WHERE id=1"})
    assert response.status_code == 409, response.text
    with meta_db.meta_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pending_mutations").fetchone()[0] == 1


def test_undo_obeys_current_cap_and_ack(client):
    cid, engine, pid, rules = _mutation_fixture(client, "delete")
    executed = client.post(f"/api/connections/{cid}/mutate/confirm", json={"preview_id": pid})
    assert executed.status_code == 200, executed.text
    undo_id = executed.json()["undo_id"]
    url = f"/api/connections/{cid}/mutate/undo/{undo_id}"
    rules.update(max_rows_per_mutation=1, confirm_rows_threshold=1)
    assert client.put(f"/api/connections/{cid}/policy", json=rules).status_code == 200
    assert client.post(url, json={"ack_large_impact": True}).status_code == 400
    rules["max_rows_per_mutation"] = 10
    assert client.put(f"/api/connections/{cid}/policy", json=rules).status_code == 200
    assert client.post(url).status_code == 400
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM revision_rows")).scalar() == 0
    assert client.post(url, json={"ack_large_impact": True}).status_code == 200


def test_request_and_result_budgets(client, db, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", 32)
    assert client.post("/api/connections", content=b"x" * 33).status_code == 413
    monkeypatch.setattr(config, "MAX_RESULT_BYTES", 20)
    with pytest.raises(ValueError, match="byte limit"):
        execute_select(db, "SELECT password FROM users")
    assert execute_select(db, "SELECT id FROM users LIMIT 1")["rows"] == [{"id": 1}]


def test_request_rate_budget_cannot_split_with_arbitrary_headers(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "REQUESTS_PER_MINUTE", 1)
    assert client.get("/api/connections", headers={"X-API-Key": "one"}).status_code == 200
    assert client.get("/api/connections", headers={"X-API-Key": "two"}).status_code == 429


def test_model_budget_counts_actual_calls(client, monkeypatch):
    from app import config
    from app.resources import limited_completion
    from types import SimpleNamespace
    calls = []
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: calls.append(kw))))
    monkeypatch.setattr(config, "LLM_CALLS_PER_HOUR", 1)
    limited_completion(fake, messages=[{"role": "user", "content": "hello"}], max_tokens=99999)
    with pytest.raises(ValueError, match="quota"):
        limited_completion(fake, messages=[])
    assert len(calls) == 1 and calls[0]["max_tokens"] == config.MAX_LLM_OUTPUT_TOKENS


def test_history_sql_and_collections_bounded():
    from app.models import ChatTurn, MutateRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ChatTurn(role="user", content="hello", sql="x" * 32001)
    with pytest.raises(ValidationError):
        MutateRequest(operation="insert", table="users", values={str(n): n for n in range(257)})


def test_commit_unknown_blocks_retry_and_preserves_snapshot(client, monkeypatch):
    from app.mutate import executor
    from app.mutate.pending import get_pending
    from app import meta_db
    cid, _, pid, _ = _mutation_fixture(client, "delete")
    def lost_ack(conn):
        conn.commit()
        raise executor.CommitOutcomeUnknown("simulated lost commit acknowledgement")
    monkeypatch.setattr(executor, "_commit", lost_ack)
    response = client.post(f"/api/connections/{cid}/mutate/confirm", json={"preview_id": pid})
    assert response.status_code == 503, response.text
    assert get_pending(pid)["status"] == "indeterminate"
    assert client.post(f"/api/connections/{cid}/mutate/confirm", json={"preview_id": pid}).status_code == 400
    assert client.get(f"/api/connections/{cid}/mutate/status/{pid}").json()["status"] == "indeterminate"
    with meta_db.meta_conn() as conn:
        assert conn.execute("SELECT status FROM undo_mutations").fetchone()[0] == "preparing"


def test_post_commit_finalization_failure_does_not_repeat_write(client, monkeypatch):
    from app.api import mutate
    cid, engine, pid, _ = _mutation_fixture(client, "delete")
    def fail(_):
        raise RuntimeError("metadata finalization unavailable")
    monkeypatch.setattr(mutate, "activate_undo", fail)
    response = client.post(f"/api/connections/{cid}/mutate/confirm", json={"preview_id": pid})
    assert response.status_code == 500 and "committed" in response.text
    assert client.get(f"/api/connections/{cid}/mutate/status/{pid}").json()["status"] == "executed"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM revision_rows")).scalar() == 0
    assert client.post(f"/api/connections/{cid}/mutate/confirm", json={"preview_id": pid}).status_code == 400



def test_chunked_body_limit_before_handler(client, monkeypatch):
    import asyncio
    from app import config
    from app.resources import ResourceLimitMiddleware
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", 10)
    async def scenario():
        calls = []
        async def app(scope, receive, send):
            calls.append(True)
        chunks = iter([{"type": "http.request", "body": b"123456", "more_body": True},
                       {"type": "http.request", "body": b"789012", "more_body": False}])
        async def receive():
            return next(chunks)
        sent = []
        async def send(message):
            sent.append(message)
        await ResourceLimitMiddleware(app)({"type": "http", "path": "/api/test", "headers": []}, receive, send)
        assert sent[0]["status"] == 413 and not calls
    asyncio.run(scenario())


def test_concurrent_request_limit_releases_slot(client, monkeypatch):
    import asyncio
    from app import config
    from app.resources import ResourceLimitMiddleware
    monkeypatch.setattr(config, "MAX_CONCURRENT_REQUESTS", 1)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            entered.set()
            await release.wait()
        limiter = ResourceLimitMiddleware(app)
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = []
        async def send(message):
            sent.append(message)
        scope = {"type": "http", "path": "/api/test", "headers": []}
        first = asyncio.create_task(limiter(scope, receive, send))
        await entered.wait()
        await limiter(scope, receive, send)
        assert sent[0]["status"] == 429
        release.set()
        await first
        assert not limiter.active
        await limiter(scope, receive, send)
        assert not limiter.active
    asyncio.run(scenario())


def test_cleanup_preserves_inflight_and_executed_status(client):
    from app import meta_db, secure_payload
    from app.mutate.pending import create_pending, claim_pending, mark_executed, get_pending
    from app.maintenance import clean_payloads
    cid = client.get("/api/connections").json()[0]["id"]
    item = create_pending(cid, {"test": "value"})
    assert not claim_pending(item["id"])[1]
    with meta_db.meta_conn() as conn:
        conn.execute("UPDATE pending_mutations SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (item["id"],))
    clean_payloads()
    assert get_pending(item["id"])["status"] == "executing"
    assert get_pending(item["id"])["plan"] == {"test": "value"}
    mark_executed(item["id"])
    clean_payloads()
    assert get_pending(item["id"])["status"] == "executed"
    assert get_pending(item["id"])["plan"] == {}


def test_legacy_unbound_preview_fails_closed(client):
    from app.mutate.pending import create_pending
    cid = client.get("/api/connections").json()[0]["id"]
    pending = create_pending(cid, {"operation": "insert", "table": "users", "values": {"id": 999}})
    response = client.post(f"/api/connections/{cid}/mutate/confirm", json={"preview_id": pending["id"]})
    assert response.status_code == 409


def test_undo_commit_unknown_is_not_released_for_retry(client, monkeypatch):
    from app.mutate import executor
    from app.mutate.undo import get_undo
    cid, _, pid, _ = _mutation_fixture(client, "delete")
    executed = client.post(f"/api/connections/{cid}/mutate/confirm", json={"preview_id": pid})
    undo_id = executed.json()["undo_id"]
    def lost_ack(conn):
        conn.commit()
        raise executor.CommitOutcomeUnknown("simulated lost commit acknowledgement")
    monkeypatch.setattr(executor, "_commit", lost_ack)
    response = client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}")
    assert response.status_code == 503, response.text
    assert get_undo(undo_id)["status"] == "indeterminate"
    assert client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}").status_code == 409



def test_two_confirm_claims_have_one_winner(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.mutate.pending import claim_pending
    cid, _, pid, _ = _mutation_fixture(client)
    start = Barrier(2)
    def claim():
        start.wait(timeout=5)
        return claim_pending(pid, connection_id=cid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert sum(not error for _, error in results) == 1
    assert sum(error == "busy" for _, error in results) == 1


def test_legacy_unbound_undo_fails_closed(client):
    from app.mutate.undo import create_undo
    cid = client.get("/api/connections").json()[0]["id"]
    undo = create_undo(cid, table="users", schema_name=None, dialect="sqlite", rows=[{"id": 999}], primary_key=["id"])
    assert client.post(f"/api/connections/{cid}/mutate/undo/{undo['id']}").status_code == 409
