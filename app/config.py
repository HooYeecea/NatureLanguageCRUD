import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

META_DB_PATH = ROOT_DIR / "workbench.db"
DEMO_DB_PATH = ROOT_DIR / "demo.db"

SECRET_KEY = os.getenv("WORKBENCH_SECRET_KEY", "dev-only-change-me")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

SUPPORTED_DIALECTS = ("sqlite", "mysql", "postgresql", "sqlserver")
