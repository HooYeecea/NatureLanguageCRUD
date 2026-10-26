# -*- coding: utf-8 -*-
from app.query.sql_guard import SqlGuardError, guard_select_sql

POLICY = {
    "connection_id": "c1",
    "max_rows_per_query": 100,
    "tables": [
        {
            "table": "users",
            "schema_name": None,
            "allow_select": True,
            "allow_insert": True,
            "allow_update": True,
            "allow_delete": True,
            "allowed_columns": None,
            "denied_columns": [],
        },
        {
            "table": "tasks",
            "schema_name": None,
            "allow_select": True,
            "allow_insert": True,
            "allow_update": True,
            "allow_delete": True,
            "allowed_columns": None,
            "denied_columns": [],
        },
    ],
}


def test_guard_allows_inner_join():
    g = guard_select_sql(
        "SELECT u.id, t.title FROM users u INNER JOIN tasks t ON u.id = t.user_id",
        dialect="sqlite",
        policy=POLICY,
    )
    assert "JOIN" in g.sql.upper()
    assert set(g.tables) >= {"users", "tasks"}


def test_guard_allows_count_group_by():
    g = guard_select_sql(
        "SELECT status, COUNT(*) AS c FROM tasks GROUP BY status",
        dialect="sqlite",
        policy=POLICY,
    )
    assert "COUNT" in g.sql.upper()
    assert g.limit == 100


def test_guard_rejects_cross_join():
    try:
        guard_select_sql(
            "SELECT * FROM users CROSS JOIN tasks",
            dialect="sqlite",
            policy=POLICY,
        )
        assert False, "expected SqlGuardError"
    except SqlGuardError as exc:
        assert "JOIN" in str(exc).upper() or "CROSS" in str(exc).upper()


def test_guard_rejects_window():
    try:
        guard_select_sql(
            "SELECT SUM(id) OVER () FROM users",
            dialect="sqlite",
            policy=POLICY,
        )
        assert False, "expected SqlGuardError"
    except SqlGuardError as exc:
        assert "window" in str(exc).lower()


def test_schema_fingerprint_changes_on_column():
    from app.db.schema import schema_fingerprint

    a = [
        {
            "name": "users",
            "schema_name": None,
            "columns": [{"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True}],
            "primary_key": ["id"],
            "foreign_keys": [],
        }
    ]
    b = [
        {
            "name": "users",
            "schema_name": None,
            "columns": [
                {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
                {"name": "email", "type": "TEXT", "nullable": True, "primary_key": False},
            ],
            "primary_key": ["id"],
            "foreign_keys": [],
        }
    ]
    assert schema_fingerprint(a) != schema_fingerprint(b)
    assert schema_fingerprint(a) == schema_fingerprint(a)
