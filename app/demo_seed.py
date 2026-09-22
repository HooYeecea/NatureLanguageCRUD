"""Ensure local demo.db has sample tables for SQLite smoke tests."""

import sqlite3

from app.config import DEMO_DB_PATH


def ensure_demo_db() -> None:
    conn = sqlite3.connect(DEMO_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            assignee_id INTEGER,
            status TEXT DEFAULT 'TODO',
            priority TEXT DEFAULT '中'
        )
        """
    )
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO users (name, role) VALUES (?, ?)",
            [("张三", "后端开发"), ("李四", "前端开发")],
        )
        cursor.executemany(
            "INSERT INTO tasks (title, assignee_id, status, priority) VALUES (?, ?, ?, ?)",
            [
                ("修复登录接口 Bug", 1, "TODO", "高"),
                ("优化首页加载速度", 2, "IN_PROGRESS", "中"),
                ("写单元测试", 1, "TODO", "低"),
            ],
        )
    conn.commit()
    conn.close()
