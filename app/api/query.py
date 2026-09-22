from fastapi import APIRouter, HTTPException

from app import meta_db, policy_store
from app.db.engines import create_db_engine
from app.db.schema import get_engine_cfg, introspect_schema
from app.models import (
    NlQueryRequest,
    QueryResult,
    RawSqlQueryRequest,
    StructuredQueryRequest,
)
from app.query import (
    LlmNotConfigured,
    SqlGuardError,
    build_structured_select,
    execute_select,
    guard_select_sql,
    nl_to_guarded_sql,
)

router = APIRouter(prefix="/api/connections", tags=["query"])


def _load_connection(connection_id: str) -> dict:
    data = meta_db.get_connection(connection_id, include_password=True)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")
    return data


def _run_guarded(connection: dict, guarded, *, dry_run: bool, explanation: str | None = None, reply: str | None = None) -> QueryResult:
    if dry_run:
        return QueryResult(
            sql=guarded.sql,
            tables=guarded.tables,
            limit=guarded.limit,
            dry_run=True,
            explanation=explanation,
            reply=reply,
        )

    engine = create_db_engine(get_engine_cfg(connection))
    try:
        result = execute_select(engine, guarded.sql)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Query execution failed: {exc}") from exc
    finally:
        engine.dispose()

    return QueryResult(
        sql=guarded.sql,
        tables=guarded.tables,
        limit=guarded.limit,
        dry_run=False,
        columns=result["columns"],
        rows=result["rows"],
        row_count=result["row_count"],
        explanation=explanation,
        reply=reply,
    )


@router.post("/{connection_id}/query/structured", response_model=QueryResult)
def query_structured(connection_id: str, body: StructuredQueryRequest):
    connection = _load_connection(connection_id)
    policy = policy_store.get_policy(connection_id)
    try:
        guarded = build_structured_select(
            dialect=connection["dialect"],
            policy=policy,
            table=body.table,
            schema_name=body.schema_name,
            columns=body.columns,
            filters=[f.model_dump() for f in body.filters],
            order_by=[o.model_dump() for o in body.order_by],
            limit=body.limit,
        )
    except SqlGuardError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return _run_guarded(connection, guarded, dry_run=body.dry_run)


@router.post("/{connection_id}/query/sql", response_model=QueryResult)
def query_raw_sql(connection_id: str, body: RawSqlQueryRequest):
    connection = _load_connection(connection_id)
    policy = policy_store.get_policy(connection_id)
    try:
        guarded = guard_select_sql(body.sql, dialect=connection["dialect"], policy=policy)
    except SqlGuardError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return _run_guarded(connection, guarded, dry_run=body.dry_run)


@router.post("/{connection_id}/query/nl", response_model=QueryResult)
def query_natural_language(connection_id: str, body: NlQueryRequest):
    connection = _load_connection(connection_id)
    policy = policy_store.get_policy(connection_id)

    engine = create_db_engine(get_engine_cfg(connection))
    try:
        overview = introspect_schema(engine, connection_id, connection["dialect"])
        schema_tables = [t.model_dump() for t in overview.tables]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Schema introspection failed: {exc}") from exc
    finally:
        engine.dispose()

    try:
        guarded, explanation, reply = nl_to_guarded_sql(
            body.prompt,
            dialect=connection["dialect"],
            policy=policy,
            schema_tables=schema_tables,
        )
    except LlmNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SqlGuardError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc

    if guarded is None:
        return QueryResult(sql="", dry_run=body.dry_run, reply=reply or "No query generated.")

    return _run_guarded(
        connection,
        guarded,
        dry_run=body.dry_run,
        explanation=explanation,
        reply=reply,
    )
