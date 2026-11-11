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
        restore_rows(db, "users", None, result["before_rows"])
    with db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
    with db.begin() as conn:
        conn.execute(text("DELETE FROM users"))
    restore_rows(db, "users", None, result["before_rows"])
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
    failed = client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}")
    assert failed.status_code == 400
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM security_rows")).scalar() == 1
        conn.execute(text("DELETE FROM security_rows"))
    restored = client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}")
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored_count"] == 25
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM security_rows")).scalar() == 25
    assert client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}").status_code == 409


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
