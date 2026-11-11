"""Server-controlled destination boundaries; never configured through the API."""
from pathlib import Path
from urllib.parse import urlsplit
import os

from app import config


def validate_llm_url(value: str) -> str:
    parsed = urlsplit(value)
    trusted = {"api.deepseek.com", "api.openai.com"}
    trusted.update(h.strip().lower() for h in os.getenv("WORKBENCH_LLM_HOSTS", "").split(",") if h.strip())
    if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in trusted
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in (None, 443)):
        raise ValueError("LLM URL must use HTTPS and a server-approved host on port 443")
    return value.rstrip("/")


def validate_connection(cfg: dict) -> dict:
    cfg = dict(cfg)
    if cfg["dialect"] == "sqlite":
        raw = str((cfg.get("options") or {}).get("path") or cfg.get("database") or "")
        if raw == ":memory:" or raw.startswith(":memory:"):
            # A named in-memory test connection must never become a disk filename.
            cfg["database"] = ":memory:"
            cfg["options"] = {**(cfg.get("options") or {}), "path": ":memory:"}
            return cfg
        if not raw or "?" in raw or "#" in raw or raw.startswith("file:"):
            raise ValueError("SQLite URI parameters are not allowed")
        target = Path(raw).resolve()
        roots = [config._DATA_DIR.resolve()]
        roots.extend(Path(p).resolve() for p in os.getenv("WORKBENCH_SQLITE_DIRS", "").split(os.pathsep) if p)
        if not any(target.is_relative_to(root) for root in roots):
            raise ValueError("SQLite path is outside server-approved directories")
        from app import meta_db
        metadata = meta_db.META_DB_PATH.resolve()
        if target == metadata or (target.exists() and metadata.exists() and target.samefile(metadata)):
            raise ValueError("The workbench metadata database cannot be a data connection")
        if target.name in {metadata.name + "-wal", metadata.name + "-shm", metadata.name + "-journal"}:
            raise ValueError("Metadata sidecar access is forbidden")
        cfg["database"] = str(target)
        cfg["options"] = {**(cfg.get("options") or {}), "path": str(target)}
    else:
        host = str(cfg.get("host") or "").strip().lower()
        allowed = {h.strip().lower() for h in os.getenv("WORKBENCH_DB_HOSTS", "").split(",") if h.strip()}
        if host not in allowed or any(c in host for c in "/\\;@?#{}"):
            raise ValueError("Database host is not in WORKBENCH_DB_HOSTS")
        cfg["host"] = host
        port = cfg.get("port")
        if port is not None and not 1 <= int(port) <= 65535:
            raise ValueError("Invalid database port")
    return cfg
