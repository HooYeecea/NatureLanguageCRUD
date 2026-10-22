"""Build SQLAlchemy engines for supported relational dialects."""

from __future__ import annotations

import hashlib
import threading
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


DEFAULT_PORTS = {
    "mysql": 3306,
    "postgresql": 5432,
    "sqlserver": 1433,
}

_POOL: dict[str, Engine] = {}
_LOCK = threading.Lock()


def build_url(cfg: dict[str, Any]) -> str:
    dialect = cfg["dialect"]
    password = cfg.get("password") or ""
    username = cfg.get("username") or ""
    host = cfg.get("host") or "localhost"
    port = cfg.get("port") or DEFAULT_PORTS.get(dialect)
    database = cfg.get("database") or ""
    options = cfg.get("options") or {}

    if dialect == "sqlite":
        path = options.get("path") or database
        return f"sqlite:///{path}"

    userinfo = ""
    if username:
        userinfo = quote_plus(username)
        if password:
            userinfo += f":{quote_plus(password)}"
        userinfo += "@"

    if dialect == "mysql":
        return f"mysql+pymysql://{userinfo}{host}:{port}/{quote_plus(database)}"

    if dialect == "postgresql":
        return f"postgresql+psycopg2://{userinfo}{host}:{port}/{quote_plus(database)}"

    if dialect == "sqlserver":
        driver = options.get("driver") or "ODBC Driver 18 for SQL Server"
        odbc = (
            f"DRIVER={{{driver}}};SERVER={host},{port};DATABASE={database};"
            f"UID={username};PWD={password};TrustServerCertificate=yes;"
        )
        return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}"

    raise ValueError(f"Unsupported dialect: {dialect}")


def create_db_engine(cfg: dict[str, Any], *, echo: bool = False) -> Engine:
    url = build_url(cfg)
    connect_args: dict[str, Any] = {}
    if cfg["dialect"] == "sqlite":
        connect_args["check_same_thread"] = False
    return create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args=connect_args,
    )


def engine_fingerprint(cfg: dict[str, Any]) -> str:
    options = cfg.get("options") or {}
    raw = "|".join(
        [
            str(cfg.get("dialect") or ""),
            str(cfg.get("host") or ""),
            str(cfg.get("port") or ""),
            str(cfg.get("database") or ""),
            str(cfg.get("username") or ""),
            str(cfg.get("password") or ""),
            str(options.get("path") or ""),
            str(options.get("driver") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get_pooled_engine(cfg: dict[str, Any], *, echo: bool = False) -> Engine:
    """Reuse engines across requests (do not dispose after each call)."""
    key = engine_fingerprint(cfg)
    with _LOCK:
        engine = _POOL.get(key)
        if engine is None:
            engine = create_db_engine(cfg, echo=echo)
            _POOL[key] = engine
        return engine


def invalidate_pooled_engine(cfg: dict[str, Any]) -> None:
    key = engine_fingerprint(cfg)
    with _LOCK:
        engine = _POOL.pop(key, None)
    if engine is not None:
        engine.dispose()


def dispose_all_pooled_engines() -> None:
    with _LOCK:
        engines = list(_POOL.values())
        _POOL.clear()
    for engine in engines:
        engine.dispose()


def test_engine(engine: Engine, *, dispose: bool = False) -> tuple[bool, str, str | None]:
    try:
        with engine.connect() as conn:
            try:
                row = conn.execute(text("SELECT 1")).fetchone()
                if row is None:
                    return False, "Connected but SELECT 1 returned no row", None
            except Exception as exc:  # noqa: BLE001
                return False, f"Query failed: {exc}", None

            version = None
            try:
                dialect = engine.dialect.name
                if dialect == "sqlite":
                    version = conn.execute(text("SELECT sqlite_version()")).scalar()
                elif dialect == "mysql":
                    version = conn.execute(text("SELECT VERSION()")).scalar()
                elif dialect == "postgresql":
                    version = conn.execute(text("SHOW server_version")).scalar()
                elif dialect in ("mssql", "microsoft"):
                    version = conn.execute(text("SELECT @@VERSION")).scalar()
                if version is not None:
                    version = str(version).split("\n")[0][:200]
            except Exception:  # noqa: BLE001
                version = None

        return True, "Connection successful", version
    except Exception as exc:  # noqa: BLE001
        return False, str(exc), None
    finally:
        if dispose:
            engine.dispose()
