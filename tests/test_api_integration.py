# -*- coding: utf-8 -*-
"""FastAPI TestClient coverage for auth, cancel, redact, claim, undo."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env_paths(tmp_path, monkeypatch):
    meta = tmp_path / "workbench.db"
    demo = tmp_path / "demo.db"
    monkeypatch.setenv("WORKBENCH_ENV", "development")
    monkeypatch.setattr("app.config.META_DB_PATH", meta)
    monkeypatch.setattr("app.meta_db.META_DB_PATH", meta)
    monkeypatch.setattr("app.config.DEMO_DB_PATH", demo)
    monkeypatch.setattr("app.config.WORKBENCH_API_KEY", "")
    monkeypatch.setattr("app.auth.WORKBENCH_API_KEY", "")
    from app.db.engines import dispose_all_pooled_engines

    dispose_all_pooled_engines()
    yield {"meta": meta, "demo": demo}
    dispose_all_pooled_engines()


@pytest.fixture()
def client(env_paths):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _demo_id(client: TestClient) -> str:
    rows = client.get("/api/connections").json()
    assert rows
    demo = next(c for c in rows if c["name"] == "local-demo-sqlite")
    return demo["id"]


def test_health_and_auth_status_open(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/auth/status").json()["required"] is False


def test_api_key_gate(env_paths, monkeypatch):
    monkeypatch.setattr("app.config.WORKBENCH_API_KEY", "integration-test-key-01")
    monkeypatch.setattr("app.auth.WORKBENCH_API_KEY", "integration-test-key-01")
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/auth/status").json()["required"] is True
        denied = c.get("/api/connections")
        assert denied.status_code == 401
        ok = c.get("/api/connections", headers={"X-API-Key": "integration-test-key-01"})
        assert ok.status_code == 200


def test_query_redacts_secret_columns(client):
    cid = _demo_id(client)
    # Mark a tasks column as secret via policy upsert
    policy = client.get(f"/api/connections/{cid}/policy").json()
    tables = policy.get("tables") or []
    for t in tables:
        if t["table"] == "tasks":
            t["secret_columns"] = ["title"]
            t["allow_delete"] = True
    client.put(
        f"/api/connections/{cid}/policy",
        json={
            "require_where_for_update": True,
            "require_where_for_delete": True,
            "max_rows_per_mutation": 100,
            "max_rows_per_query": 500,
            "confirm_rows_threshold": 10,
            "tables": tables,
        },
    )
    res = client.post(
        f"/api/connections/{cid}/query/sql",
        json={"sql": "SELECT id, title, status FROM tasks LIMIT 5"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["rows"]
    assert body["rows"][0]["title"] == "[redacted]"


def test_mutate_cancel_and_claim(client):
    cid = _demo_id(client)
    policy = client.get(f"/api/connections/{cid}/policy").json()
    tables = policy.get("tables") or []
    for t in tables:
        t["allow_delete"] = True
        t["allow_update"] = True
        t["allow_insert"] = True
    client.put(
        f"/api/connections/{cid}/policy",
        json={
            "require_where_for_update": True,
            "require_where_for_delete": True,
            "max_rows_per_mutation": 100,
            "max_rows_per_query": 500,
            "confirm_rows_threshold": 10,
            "tables": tables,
        },
    )

    preview = client.post(
        f"/api/connections/{cid}/mutate/preview",
        json={
            "operation": "update",
            "table": "tasks",
            "set_values": {"status": "done"},
            "filters": [{"column": "id", "op": "=", "value": 1}],
        },
    )
    assert preview.status_code == 200, preview.text
    preview_id = preview.json()["preview_id"]

    cancelled = client.post(f"/api/connections/{cid}/mutate/cancel/{preview_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    confirm = client.post(
        f"/api/connections/{cid}/mutate/confirm",
        json={"preview_id": preview_id, "ack_large_impact": True},
    )
    assert confirm.status_code in (400, 404)


def test_delete_undo_roundtrip(client):
    cid = _demo_id(client)
    policy = client.get(f"/api/connections/{cid}/policy").json()
    tables = policy.get("tables") or []
    for t in tables:
        t["allow_delete"] = True
        t["allow_insert"] = True
        t["allow_update"] = True
    client.put(
        f"/api/connections/{cid}/policy",
        json={
            "require_where_for_update": True,
            "require_where_for_delete": True,
            "max_rows_per_mutation": 100,
            "max_rows_per_query": 500,
            "confirm_rows_threshold": 10,
            "tables": tables,
        },
    )

    # Insert a disposable row
    ins = client.post(
        f"/api/connections/{cid}/mutate/preview",
        json={
            "operation": "insert",
            "table": "tasks",
            "values": {"title": "undo-me", "status": "TODO", "priority": "低", "assignee_id": 1},
        },
    )
    assert ins.status_code == 200, ins.text
    ins_id = ins.json()["preview_id"]
    done = client.post(
        f"/api/connections/{cid}/mutate/confirm",
        json={"preview_id": ins_id, "ack_large_impact": True},
    )
    assert done.status_code == 200, done.text
    # Find the row
    q = client.post(
        f"/api/connections/{cid}/query/sql",
        json={"sql": "SELECT id FROM tasks WHERE title = 'undo-me' LIMIT 5"},
    )
    assert q.status_code == 200
    rows = q.json()["rows"]
    assert rows
    task_id = rows[0]["id"]

    # Delete it
    dprev = client.post(
        f"/api/connections/{cid}/mutate/preview",
        json={
            "operation": "delete",
            "table": "tasks",
            "filters": [{"column": "id", "op": "=", "value": task_id}],
        },
    )
    assert dprev.status_code == 200, dprev.text
    deleted = client.post(
        f"/api/connections/{cid}/mutate/confirm",
        json={"preview_id": dprev.json()["preview_id"], "ack_large_impact": True},
    )
    assert deleted.status_code == 200, deleted.text
    undo_id = deleted.json().get("undo_id")
    assert undo_id

    # Gone
    gone = client.post(
        f"/api/connections/{cid}/query/sql",
        json={"sql": f"SELECT id FROM tasks WHERE id = {int(task_id)}"},
    )
    assert gone.json()["row_count"] == 0

    # Undo
    undone = client.post(f"/api/connections/{cid}/mutate/undo/{undo_id}")
    assert undone.status_code == 200, undone.text
    assert undone.json()["restored_count"] >= 1

    back = client.post(
        f"/api/connections/{cid}/query/sql",
        json={"sql": f"SELECT title FROM tasks WHERE id = {int(task_id)}"},
    )
    assert back.json()["row_count"] == 1
    assert back.json()["rows"][0]["title"] == "undo-me"
