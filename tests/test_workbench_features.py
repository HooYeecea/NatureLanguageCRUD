import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


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
    with TestClient(main.app) as test_client:
        yield test_client
    dispose_all_pooled_engines()


def connection(client):
    return client.get("/api/connections").json()[0]["id"]


def stub_query(monkeypatch):
    from app.api import query
    from app.query.sql_guard import guard_select_sql
    calls = []
    def generate(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return guard_select_sql("SELECT id FROM users", dialect=kwargs["dialect"], policy=kwargs["policy"]), "用户编号", None
    monkeypatch.setattr(query, "nl_to_guarded_sql", generate)
    return calls


def test_metrics_crud_and_context_in_query(client, monkeypatch):
    from app import meta_db
    cid = connection(client)
    body = {"name": "有效用户", "table": "users", "definition": "只统计符合业务条件的用户", "time_field": "id"}
    created = client.post(f"/api/connections/{cid}/metrics", json=body)
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]
    assert client.get(f"/api/connections/{cid}/metrics").json()["can_edit"]
    calls = stub_query(monkeypatch)
    result = client.post(f"/api/connections/{cid}/query/nl", json={"prompt": "查询有效用户的编号"})
    assert result.status_code == 200, result.text
    assert "有效用户" in calls[0][1]["glossary_context"]
    with meta_db.meta_conn() as conn:
        assert "有效用户" not in conn.execute("SELECT payload FROM workbench_items WHERE id=?", (item_id,)).fetchone()[0]
    body["definition"] = "修改后的统计口径"
    assert client.put(f"/api/connections/{cid}/metrics/{item_id}", json=body).status_code == 200
    assert client.get(f"/api/connections/{cid}/metrics").json()["items"][0]["definition"] == body["definition"]
    assert client.delete(f"/api/connections/{cid}/metrics/{item_id}").status_code == 200
    assert client.get(f"/api/connections/{cid}/metrics").json()["items"] == []


def test_metric_policy_revocation_removes_context(client):
    from app.workbench_features import metric_context
    cid = connection(client)
    assert client.post(f"/api/connections/{cid}/metrics", json={"name":"用户数","table":"users","definition":"COUNT"}).status_code == 200
    assert client.put(f"/api/connections/{cid}/policy", json={"tables":[]}).status_code == 200
    assert client.get(f"/api/connections/{cid}/metrics").json()["items"] == []
    assert metric_context(cid) == ""
    assert client.post(f"/api/connections/{cid}/metrics", json={"name":"外部表","table":"missing","definition":"COUNT"}).status_code == 400


def test_template_render_and_current_policy(client, monkeypatch):
    cid = connection(client)
    calls = stub_query(monkeypatch)
    body = {"name":"按日期查询用户", "prompt":"查询 {{start}} 之后的 {{label}} 用户", "parameters":[
        {"name":"start","label":"开始日期","kind":"date"}, {"name":"label","label":"用户标签","kind":"text"}]}
    created = client.post(f"/api/connections/{cid}/templates", json=body)
    assert created.status_code == 200, created.text
    tid = created.json()["id"]
    url = f"/api/connections/{cid}/templates/{tid}/run"
    assert client.post(url, json={"values":{"start":"2026-11-13","label":"普通"}}).status_code == 200
    assert '"2026-11-13"' in calls[0][0] and '{{start}}' not in calls[0][0]
    for values in [{}, {"start":"invalid","label":"x"}, {"start":"2026-11-13","label":"x","extra":"x"}]:
        assert client.post(url, json={"values":values}).status_code == 422
    assert client.put(f"/api/connections/{cid}/policy", json={"tables":[]}).status_code == 200
    denied = client.post(url, json={"values":{"start":"2026-11-13","label":"普通"}})
    assert denied.status_code == 400, denied.text


def test_template_edit_delete_and_encryption(client):
    from app import meta_db
    cid = connection(client)
    response = client.post(f"/api/connections/{cid}/templates", json={"name":"收藏", "prompt":"查询 fake-private-value 的用户"})
    assert response.status_code == 200
    tid = response.json()["id"]
    with meta_db.meta_conn() as conn:
        assert "fake-private-value" not in conn.execute("SELECT payload FROM workbench_items WHERE id=?", (tid,)).fetchone()[0]
    assert client.put(f"/api/connections/{cid}/templates/{tid}", json={"name":"新名字","prompt":"查看用户编号"}).status_code == 200
    assert client.get(f"/api/connections/{cid}/templates").json()["items"][0]["name"] == "新名字"
    assert client.delete(f"/api/connections/{cid}/templates/{tid}").status_code == 200
    assert client.get(f"/api/connections/{cid}/templates").json()["items"] == []


def test_identity_isolation_and_metric_admin_only(client, monkeypatch):
    from app import config, auth
    cid = connection(client)
    admin_template = client.post(f"/api/connections/{cid}/templates", json={"name":"管理员收藏","prompt":"查看用户"}).json()
    monkeypatch.setattr(config, "WORKBENCH_API_KEY", "reader-key")
    monkeypatch.setattr(auth, "WORKBENCH_API_KEY", "reader-key")
    monkeypatch.setattr(config, "WORKBENCH_ADMIN_API_KEY", "admin-key")
    monkeypatch.setattr(config, "WORKBENCH_CONNECTION_IDS", {cid})
    monkeypatch.setattr(config, "WORKBENCH_API_ROLE", "reader")
    h = {"X-API-Key":"reader-key"}
    assert client.get(f"/api/connections/{cid}/templates", headers=h).json()["items"] == []
    path = f"/api/connections/{cid}/templates/{admin_template['id']}"
    assert client.post(path + "/run", headers=h, json={"values":{}}).status_code == 404
    assert client.delete(path, headers=h).status_code == 404
    assert client.put(path, headers=h, json={"name":"attack","prompt":"查看用户"}).status_code == 404
    assert client.post(f"/api/connections/{cid}/templates", headers=h, json={"name":"自己的收藏","prompt":"查看用户"}).status_code == 200
    assert client.post(f"/api/connections/{cid}/metrics", headers=h, json={"name":"x","table":"users","definition":"x"}).status_code == 403
    assert not client.get(f"/api/connections/{cid}/metrics", headers=h).json()["can_edit"]
    assert client.get("/api/connections/other/templates", headers=h).status_code == 403


@pytest.mark.parametrize("body", [
    {"name":"x","prompt":"查询 {{date}}"},
    {"name":"x","prompt":"查询 {{x}}", "parameters":[{"name":"x","label":"x"},{"name":"x","label":"x"}]},
    {"name":"x","prompt":"查询 {{bad-name}}", "parameters":[{"name":"bad-name","label":"x"}]},
])
def test_invalid_template_parameters_rejected(client, body):
    cid = connection(client)
    assert client.post(f"/api/connections/{cid}/templates", json=body).status_code == 422


def test_vague_period_asks_without_running_query(client, monkeypatch):
    from app.api import query
    cid = connection(client)
    def forbidden(*args, **kwargs):
        raise AssertionError("No SQL or model call should occur")
    monkeypatch.setattr(query, "nl_to_guarded_sql", forbidden)
    monkeypatch.setattr(query, "_run_guarded", forbidden)
    response = client.post(f"/api/connections/{cid}/query/nl", json={"prompt":"查看最近的用户"})
    assert response.status_code == 200, response.text
    assert not response.json()["sql"]
    assert response.json()["clarification"]["options"] == ["最近7天","最近30天","全部时间"]


def test_clarification_answer_continues_original_query(client, monkeypatch):
    cid = connection(client)
    calls = stub_query(monkeypatch)
    response = client.post(f"/api/connections/{cid}/query/nl", json={"prompt":"查看最近的用户。用户补充：最近7天"})
    assert response.status_code == 200 and response.json()["sql"]
    assert "最近7天" in calls[0][0]


def test_model_clarification_takes_precedence_over_sql(monkeypatch):
    import app.query.nl as nl
    def call(name, payload):
        return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(payload)))
    message = SimpleNamespace(tool_calls=[call("run_select_sql", {"sql":"SELECT id FROM users"}), call("ask_clarification", {"question":"净额还是含税额？", "options":["净额","含税额"]})])
    monkeypatch.setattr(nl, "_client", lambda: object())
    monkeypatch.setattr(nl, "limited_completion", lambda *args, **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=message)]))
    guarded, explanation, reply = nl.nl_to_guarded_sql("统计销售额", dialect="sqlite", policy={"tables":[]}, schema_tables=[])
    assert guarded is None and reply["clarification"]["question"] == "净额还是含税额？"


def test_api_structured_clarification_and_template_context(client, monkeypatch):
    from app.api import query
    cid = connection(client)
    monkeypatch.setattr(query, "nl_to_guarded_sql", lambda *args, **kwargs: (None,None,{"clarification":{"question":"哪个状态？","options":["正常","禁用"]}}))
    template = client.post(f"/api/connections/{cid}/templates", json={"name":"模板","prompt":"查询 {{label}}", "parameters":[{"name":"label","label":"标签"}]}).json()
    response = client.post(f"/api/connections/{cid}/templates/{template['id']}/run", json={"values":{"label":"测试用户"}})
    assert response.status_code == 200, response.text
    assert response.json()["clarification"]["question"] == "哪个状态？"
    assert "测试用户" in response.json()["clarification_prompt"]


def test_mutation_status_permission_and_no_plan_disclosure(client, monkeypatch):
    from app import config, auth
    from app.mutate.pending import create_pending
    cid = connection(client)
    pending = create_pending(cid, {"private":"fake-private-value"})
    url = f"/api/connections/{cid}/mutate/status/{pending['id']}"
    response = client.get(url)
    assert response.status_code == 200 and response.json()["status"] == "pending"
    assert "fake-private-value" not in response.text
    monkeypatch.setattr(config,"WORKBENCH_API_KEY","operator")
    monkeypatch.setattr(auth,"WORKBENCH_API_KEY","operator")
    monkeypatch.setattr(config,"WORKBENCH_ADMIN_API_KEY","admin")
    monkeypatch.setattr(config,"WORKBENCH_CONNECTION_IDS",{cid})
    monkeypatch.setattr(config,"WORKBENCH_API_ROLE","reader")
    h={"X-API-Key":"operator"}
    assert client.get(url,headers=h).status_code == 403
    monkeypatch.setattr(config,"WORKBENCH_API_ROLE","writer")
    assert client.get(url,headers=h).status_code == 200
