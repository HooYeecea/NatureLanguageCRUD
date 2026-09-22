from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import meta_db
from app import policy_store
from app.api import (
    audit_router,
    connections_router,
    mutate_router,
    policies_router,
    query_router,
    workspace_router,
)
from app.config import DEMO_DB_PATH
from app.demo_seed import ensure_demo_db


def bootstrap() -> None:
    meta_db.init_meta_db()
    ensure_demo_db()
    existing = meta_db.list_connections()
    demo = next((c for c in existing if c["name"] == "local-demo-sqlite"), None)
    if not demo:
        demo = meta_db.create_connection(
            {
                "name": "local-demo-sqlite",
                "dialect": "sqlite",
                "database": str(DEMO_DB_PATH),
                "options": {"path": str(DEMO_DB_PATH)},
            }
        )

    policy = policy_store.get_policy(demo["id"])
    if not policy.get("tables"):
        policy_store.upsert_policy(
            demo["id"],
            {
                "require_where_for_update": True,
                "require_where_for_delete": True,
                "max_rows_per_mutation": 100,
                "max_rows_per_query": 500,
                "tables": [
                    {
                        "table": "users",
                        "schema_name": None,
                        "allowed_columns": None,
                        "denied_columns": [],
                        "allow_select": True,
                        "allow_insert": True,
                        "allow_update": True,
                        "allow_delete": False,
                    },
                    {
                        "table": "tasks",
                        "schema_name": None,
                        "allowed_columns": None,
                        "denied_columns": [],
                        "allow_select": True,
                        "allow_insert": True,
                        "allow_update": True,
                        "allow_delete": False,
                    },
                ],
            },
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(connections_router)
app.include_router(policies_router)
app.include_router(query_router)
app.include_router(mutate_router)
app.include_router(audit_router)
app.include_router(workspace_router)
