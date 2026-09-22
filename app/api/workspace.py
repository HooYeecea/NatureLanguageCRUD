from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import audit, meta_db, policy_store
from app.analysis_store import get_analysis, save_analysis
from app.db.engines import create_db_engine
from app.db.schema import get_engine_cfg, introspect_schema
from app.models import AccessPolicyOut, AccessPolicyUpsert, TablePolicy
from app.schema_interpret import interpret_schema, llm_status
from app.settings_store import update_llm_settings

router = APIRouter(tags=["workspace"])


class SettingsOut(BaseModel):
    llm_configured: bool
    llm_base_url: str | None = None
    llm_model: str | None = None
    api_key_masked: str | None = None
    source: str | None = None


class SettingsUpdate(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    clear_api_key: bool = False


class SelectedTable(BaseModel):
    table: str
    schema_name: str | None = None


class SelectTablesRequest(BaseModel):
    tables: list[SelectedTable] = Field(default_factory=list)
    allow_select: bool = True
    allow_insert: bool = True
    allow_update: bool = True
    allow_delete: bool = False


class InterpretRequest(BaseModel):
    tables: list[SelectedTable] = Field(default_factory=list)
    use_llm: bool = True
    force: bool = False  # True = ignore cache and re-analyze


class InterpretResponse(BaseModel):
    connection_id: str
    dialect: str
    selected_tables: list[str]
    overview: str = ""
    tables: list[dict] = Field(default_factory=list)
    relationships: list[dict] = Field(default_factory=list)
    join_hints: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source: str = "metadata"
    cached: bool = False
    updated_at: str | None = None


@router.get("/api/settings", response_model=SettingsOut)
def get_settings():
    status = llm_status()
    return SettingsOut(
        llm_configured=status["configured"],
        llm_base_url=status["base_url"],
        llm_model=status["model"],
        api_key_masked=status.get("api_key_masked"),
        source=status.get("source"),
    )


@router.put("/api/settings", response_model=SettingsOut)
def put_settings(body: SettingsUpdate):
    cfg = update_llm_settings(
        api_key=body.api_key,
        base_url=body.base_url,
        model=body.model,
        clear_api_key=body.clear_api_key,
    )
    audit.write_audit(
        action="settings.llm_update",
        status="success",
        summary="updated LLM settings",
        detail={
            "configured": cfg["configured"],
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "source": cfg["source"],
            "cleared": body.clear_api_key,
        },
    )
    return SettingsOut(
        llm_configured=cfg["configured"],
        llm_base_url=cfg["base_url"],
        llm_model=cfg["model"],
        api_key_masked=cfg.get("api_key_masked"),
        source=cfg.get("source"),
    )


@router.put("/api/connections/{connection_id}/workspace/tables", response_model=AccessPolicyOut)
def select_workspace_tables(connection_id: str, body: SelectTablesRequest):
    data = meta_db.get_connection(connection_id)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")
    if not body.tables:
        raise HTTPException(status_code=400, detail="Select at least one table")

    existing = policy_store.get_policy(connection_id)
    policy_body = AccessPolicyUpsert(
        require_where_for_update=existing.get("require_where_for_update", True),
        require_where_for_delete=existing.get("require_where_for_delete", True),
        max_rows_per_mutation=existing.get("max_rows_per_mutation", 100),
        max_rows_per_query=existing.get("max_rows_per_query", 500),
        tables=[
            TablePolicy(
                table=t.table,
                schema_name=t.schema_name,
                allowed_columns=None,
                denied_columns=[],
                allow_select=body.allow_select,
                allow_insert=body.allow_insert,
                allow_update=body.allow_update,
                allow_delete=body.allow_delete,
            )
            for t in body.tables
        ],
    )
    saved = policy_store.upsert_policy(connection_id, policy_body.model_dump())
    return AccessPolicyOut(**saved)


@router.post(
    "/api/connections/{connection_id}/workspace/interpret",
    response_model=InterpretResponse,
)
def interpret_selected_tables(connection_id: str, body: InterpretRequest):
    data = meta_db.get_connection(connection_id, include_password=True)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")
    if not body.tables:
        raise HTTPException(status_code=400, detail="Select at least one table")

    table_refs = [t.model_dump() for t in body.tables]

    if not body.force:
        cached = get_analysis(connection_id, table_refs)
        if cached:
            return InterpretResponse(
                connection_id=connection_id,
                dialect=data["dialect"],
                selected_tables=cached.get("selected_tables") or [t.table for t in body.tables],
                overview=cached.get("overview") or "",
                tables=cached.get("tables") or [],
                relationships=cached.get("relationships") or [],
                join_hints=cached.get("join_hints") or [],
                warnings=cached.get("warnings") or [],
                source=cached.get("source") or "cache",
                cached=True,
                updated_at=cached["updated_at"].isoformat() if cached.get("updated_at") else None,
            )

    engine = create_db_engine(get_engine_cfg(data))
    try:
        overview = introspect_schema(engine, connection_id, data["dialect"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Schema introspection failed: {exc}") from exc
    finally:
        engine.dispose()

    selected = []
    for t in overview.tables:
        match = False
        for req in body.tables:
            if req.table != t.name:
                continue
            if req.schema_name is None or t.schema_name is None or req.schema_name == t.schema_name:
                match = True
                break
        if match:
            selected.append(t.model_dump())

    if not selected:
        raise HTTPException(status_code=400, detail="None of the selected tables were found in schema")

    try:
        result = interpret_schema(
            dialect=data["dialect"],
            tables=selected,
            use_llm=body.use_llm,
        )
    except Exception as exc:  # noqa: BLE001
        result = interpret_schema(
            dialect=data["dialect"],
            tables=selected,
            use_llm=False,
        )
        warnings = list(result.get("warnings") or [])
        warnings.append(f"LLM interpret failed, used metadata fallback: {exc}")
        result["warnings"] = warnings

    result["dialect"] = data["dialect"]
    saved = save_analysis(connection_id, table_refs, result)

    return InterpretResponse(
        connection_id=connection_id,
        dialect=data["dialect"],
        selected_tables=[t["name"] for t in selected],
        overview=result.get("overview") or "",
        tables=result.get("tables") or [],
        relationships=result.get("relationships") or [],
        join_hints=result.get("join_hints") or [],
        warnings=result.get("warnings") or [],
        source=result.get("source") or "metadata",
        cached=False,
        updated_at=saved["updated_at"].isoformat() if saved and saved.get("updated_at") else None,
    )


@router.get(
    "/api/connections/{connection_id}/workspace/analysis",
    response_model=InterpretResponse,
)
def get_cached_analysis(connection_id: str):
    data = meta_db.get_connection(connection_id)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")

    policy = policy_store.get_policy(connection_id)
    tables = [
        {"table": t.get("table"), "schema_name": t.get("schema_name")}
        for t in (policy.get("tables") or [])
    ]
    if not tables:
        raise HTTPException(status_code=404, detail="No selected tables / cached analysis")

    cached = get_analysis(connection_id, tables)
    if not cached:
        raise HTTPException(status_code=404, detail="No cached analysis for current table set")

    return InterpretResponse(
        connection_id=connection_id,
        dialect=data["dialect"],
        selected_tables=cached.get("selected_tables") or [t["table"] for t in tables],
        overview=cached.get("overview") or "",
        tables=cached.get("tables") or [],
        relationships=cached.get("relationships") or [],
        join_hints=cached.get("join_hints") or [],
        warnings=cached.get("warnings") or [],
        source=cached.get("source") or "cache",
        cached=True,
        updated_at=cached["updated_at"].isoformat() if cached.get("updated_at") else None,
    )
