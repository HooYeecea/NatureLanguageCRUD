from fastapi import APIRouter, HTTPException

from app import audit, meta_db, policy_store
from app.analysis_store import analysis_context_text, get_analysis
from app.db.engines import create_db_engine
from app.db.schema import get_engine_cfg, introspect_schema
from app.models import (
    NlQueryRequest,
    QueryResult,
    RawSqlQueryRequest,
    StructuredQueryRequest,
)
from app.db.samples import fetch_table_samples, format_samples_text
from app.query import (
    LlmNotConfigured,
    SqlGuardError,
    build_structured_select,
    critique_query_result,
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


def _run_guarded(
    connection_id: str,
    connection: dict,
    guarded,
    *,
    action: str,
    dry_run: bool,
    explanation: str | None = None,
    reply: str | None = None,
    prompt: str | None = None,
    retried: bool = False,
    original_sql: str | None = None,
    validation_ok: bool | None = None,
    validation_note: str | None = None,
) -> QueryResult:
    if dry_run:
        audit.write_audit(
            action=action,
            status="success",
            connection_id=connection_id,
            summary=f"dry-run query on {', '.join(guarded.tables)}",
            detail={
                "sql": guarded.sql,
                "tables": guarded.tables,
                "limit": guarded.limit,
                "dry_run": True,
                "prompt": prompt,
            },
        )
        return QueryResult(
            sql=guarded.sql,
            tables=guarded.tables,
            limit=guarded.limit,
            dry_run=True,
            explanation=explanation,
            reply=reply,
            retried=retried,
            original_sql=original_sql,
            validation_ok=validation_ok,
            validation_note=validation_note,
        )

    engine = create_db_engine(get_engine_cfg(connection))
    try:
        result = execute_select(engine, guarded.sql)
    except Exception as exc:  # noqa: BLE001
        audit.write_audit(
            action=action,
            status="error",
            connection_id=connection_id,
            summary="query execution failed",
            detail={"sql": guarded.sql, "error": str(exc), "prompt": prompt},
        )
        raise HTTPException(status_code=400, detail=f"Query execution failed: {exc}") from exc
    finally:
        engine.dispose()

    audit.write_audit(
        action=action,
        status="success",
        connection_id=connection_id,
        summary=f"query returned {result['row_count']} rows",
        detail={
            "sql": guarded.sql,
            "tables": guarded.tables,
            "limit": guarded.limit,
            "row_count": result["row_count"],
            "dry_run": False,
            "prompt": prompt,
        },
    )
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
        retried=retried,
        original_sql=original_sql,
        validation_ok=validation_ok,
        validation_note=validation_note,
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
        audit.write_audit(
            action="query.structured",
            status="error",
            connection_id=connection_id,
            summary=exc.message,
            detail={"table": body.table},
        )
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return _run_guarded(
        connection_id,
        connection,
        guarded,
        action="query.structured",
        dry_run=body.dry_run,
    )


@router.post("/{connection_id}/query/sql", response_model=QueryResult)
def query_raw_sql(connection_id: str, body: RawSqlQueryRequest):
    connection = _load_connection(connection_id)
    policy = policy_store.get_policy(connection_id)
    try:
        guarded = guard_select_sql(body.sql, dialect=connection["dialect"], policy=policy)
    except SqlGuardError as exc:
        audit.write_audit(
            action="query.sql",
            status="error",
            connection_id=connection_id,
            summary=exc.message,
            detail={"sql": body.sql},
        )
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return _run_guarded(
        connection_id,
        connection,
        guarded,
        action="query.sql",
        dry_run=body.dry_run,
    )


@router.post("/{connection_id}/query/nl", response_model=QueryResult)
def query_natural_language(connection_id: str, body: NlQueryRequest):
    connection = _load_connection(connection_id)
    policy = policy_store.get_policy(connection_id)

    engine = create_db_engine(get_engine_cfg(connection))
    try:
        overview = introspect_schema(engine, connection_id, connection["dialect"])
        schema_tables = [t.model_dump() for t in overview.tables]
        allowed_names = {t.get("table") for t in (policy.get("tables") or []) if t.get("allow_select")}
        if allowed_names:
            schema_tables = [t for t in schema_tables if t.get("name") in allowed_names]
        samples = fetch_table_samples(engine, connection["dialect"], schema_tables, policy)
        sample_ctx = format_samples_text(samples)
    except Exception as exc:  # noqa: BLE001
        audit.write_audit(
            action="query.nl",
            status="error",
            connection_id=connection_id,
            summary="schema introspection failed",
            detail={"error": str(exc), "prompt": body.prompt},
        )
        raise HTTPException(status_code=400, detail=f"Schema introspection failed: {exc}") from exc
    finally:
        engine.dispose()

    analysis = get_analysis(
        connection_id,
        [
            {"table": t.get("table"), "schema_name": t.get("schema_name")}
            for t in (policy.get("tables") or [])
        ],
    )
    analysis_ctx = analysis_context_text(analysis)

    try:
        guarded, explanation, reply = nl_to_guarded_sql(
            body.prompt,
            dialect=connection["dialect"],
            policy=policy,
            schema_tables=schema_tables,
            analysis_context=analysis_ctx,
            sample_context=sample_ctx,
        )
    except LlmNotConfigured as exc:
        audit.write_audit(
            action="query.nl",
            status="error",
            connection_id=connection_id,
            summary="LLM not configured",
            detail={"prompt": body.prompt},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SqlGuardError as exc:
        audit.write_audit(
            action="query.nl",
            status="error",
            connection_id=connection_id,
            summary=exc.message,
            detail={"prompt": body.prompt},
        )
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        audit.write_audit(
            action="query.nl",
            status="error",
            connection_id=connection_id,
            summary="LLM request failed",
            detail={"error": str(exc), "prompt": body.prompt},
        )
        raise HTTPException(status_code=502, detail="大模型请求失败，请检查 API 设置后重试") from exc

    if guarded is None:
        audit.write_audit(
            action="query.nl",
            status="success",
            connection_id=connection_id,
            summary="no query generated",
            detail={"prompt": body.prompt, "reply": reply},
        )
        return QueryResult(sql="", dry_run=body.dry_run, reply=reply or "No query generated.")

    first = _run_guarded(
        connection_id,
        connection,
        guarded,
        action="query.nl",
        dry_run=body.dry_run,
        explanation=explanation,
        reply=reply,
        prompt=body.prompt,
    )
    if body.dry_run or not guarded.sql:
        return first

    critique = critique_query_result(
        body.prompt,
        first.sql,
        first.columns,
        first.rows,
        first.row_count,
    )
    if critique.get("ok"):
        first.validation_ok = True
        first.validation_note = None
        return first

    try:
        guarded2, explanation2, reply2 = nl_to_guarded_sql(
            body.prompt,
            dialect=connection["dialect"],
            policy=policy,
            schema_tables=schema_tables,
            analysis_context=analysis_ctx,
            sample_context=sample_ctx,
            previous_sql=first.sql,
            rewrite_hint=critique.get("rewrite_hint") or critique.get("reason"),
        )
    except Exception:  # noqa: BLE001
        first.validation_ok = False
        first.validation_note = critique.get("reason") or "结果可能未准确回答问题，可编辑 SQL 后重跑。"
        return first

    if guarded2 is None or guarded2.sql == first.sql:
        first.validation_ok = False
        first.validation_note = (
            critique.get("reason")
            or reply2
            or "结果可能未准确回答问题，可编辑 SQL 后重跑。"
        )
        return first

    try:
        second = _run_guarded(
            connection_id,
            connection,
            guarded2,
            action="query.nl.retry",
            dry_run=False,
            explanation=explanation2 or explanation,
            reply=reply2,
            prompt=body.prompt,
            retried=True,
            original_sql=first.sql,
            validation_ok=None,
            validation_note=critique.get("reason"),
        )
    except HTTPException:
        first.validation_ok = False
        first.validation_note = (
            critique.get("reason") or "自动改写后的 SQL 执行失败，已保留首次结果，可编辑后再跑。"
        )
        return first
    retry_check = critique_query_result(
        body.prompt,
        second.sql,
        second.columns,
        second.rows,
        second.row_count,
    )
    second.validation_ok = bool(retry_check.get("ok"))
    if second.validation_ok:
        second.validation_note = "已根据校验自动改写 SQL 后再查询。"
    else:
        second.validation_note = (
            retry_check.get("reason")
            or critique.get("reason")
            or "改写后仍可能不准确，可直接编辑 SQL 再执行。"
        )
    return second
