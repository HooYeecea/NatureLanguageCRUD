import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# Optional data directory (Docker volume). Defaults to project root.
_DATA_DIR = Path(os.getenv("WORKBENCH_DATA_DIR") or ROOT_DIR)
_DATA_DIR.mkdir(parents=True, exist_ok=True)

META_DB_PATH = _DATA_DIR / "workbench.db"
DEMO_DB_PATH = _DATA_DIR / "demo.db"

WORKBENCH_ENV = (os.getenv("WORKBENCH_ENV") or "development").strip().lower()
SECRET_KEY = os.getenv("WORKBENCH_SECRET_KEY", "dev-only-change-me")
# Optional shared gate for /api/* (empty = disabled in development).
WORKBENCH_API_KEY = (os.getenv("WORKBENCH_API_KEY") or "").strip()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

SUPPORTED_DIALECTS = ("sqlite", "mysql", "postgresql", "sqlserver")

_INSECURE_SECRETS = frozenset(
    {
        "",
        "dev-only-change-me",
        "change-me",
        "change-me-to-a-long-random-string",
        "secret",
        "password",
    }
)


def is_production() -> bool:
    return WORKBENCH_ENV in ("production", "prod")


def secret_key_is_insecure(secret: str | None = None) -> bool:
    value = (secret if secret is not None else SECRET_KEY) or ""
    if value.strip() in _INSECURE_SECRETS:
        return True
    if len(value.strip()) < 16:
        return True
    return False


def assert_secret_key_safe() -> None:
    """Refuse to start in production with a default/short encryption key."""
    if not is_production():
        return
    if secret_key_is_insecure(SECRET_KEY):
        raise RuntimeError(
            "Unsafe WORKBENCH_SECRET_KEY in production. "
            "Set WORKBENCH_SECRET_KEY to a long random string (≥16 chars) in .env, "
            "or set WORKBENCH_ENV=development for local use only."
        )


def api_key_required() -> bool:
    return bool(WORKBENCH_API_KEY)


def assert_api_key_configured() -> None:
    """Production must set WORKBENCH_API_KEY so the API is not open on the LAN."""
    if not is_production():
        return
    if not api_key_required():
        raise RuntimeError(
            "WORKBENCH_API_KEY is required in production. "
            "Set a long random API key in .env, or use WORKBENCH_ENV=development locally."
        )
    if len(WORKBENCH_API_KEY) < 16:
        raise RuntimeError(
            "WORKBENCH_API_KEY is too short in production (use ≥16 characters)."
        )
