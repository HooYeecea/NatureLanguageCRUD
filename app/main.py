from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import meta_db
from app.api import connections_router
from app.config import DEMO_DB_PATH
from app.demo_seed import ensure_demo_db


def bootstrap() -> None:
    meta_db.init_meta_db()
    ensure_demo_db()
    existing = meta_db.list_connections()
    if not any(c["name"] == "local-demo-sqlite" for c in existing):
        meta_db.create_connection(
            {
                "name": "local-demo-sqlite",
                "dialect": "sqlite",
                "database": str(DEMO_DB_PATH),
                "options": {"path": str(DEMO_DB_PATH)},
            }
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap()
    yield


app = FastAPI(
    title="NL CRUD Workbench",
    description="Natural language CRUD workbench for relational databases",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(connections_router)
