from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app import audit, meta_db, policy_store
from app.analysis_store import analysis_context_text, get_analysis
from app.db.engines import create_db_engine
from app.db.schema import get_engine_cfg, introspect_schema
from app.models import (
    MutateConfirmRequest,
    MutateExecuteResult,
    MutatePreviewResult,
    MutateRequest,
    NlMutateRequest,
)
from app.mutate import (
    build_mutate_plan,
    create_pending,
    execute_plan,
    get_pending,
    is_expired,
    mark_cancelled,
    mark_executed,
    nl_to_mutate_plan,
    preview_plan,
)
from app.query import LlmNotConfigured, SqlGuardError

router = APIRouter(prefix="/api/connections", tags=["mutate"])


def _load_connection(connection_id: str) -> dict:
    data = meta_db.get_connection(connection_id, include_password=True)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")
    return data


def _plan_to_dict(plan) -> dict:
    return asdict(plan)


def _preview_from_plan(
    connection_id: str,
    connection: dict,
    policy: dict,
    plan,
    *,
    action: str = "mutate.preview",
    explanation: str | None = None,
    reply: str | None = None,
    prompt: str | None = None,
) -> MutatePreviewResult:
    max_rows = int(policy.get("max_rows_per_mutation") or 100)
    engine = create_db_engine(get_engine_cfg(connection))
    try:
        stats = preview_plan(engine, plan, max_rows=max_rows)
    except Exception as exc:  # noqa: BLE001
        audit.write_audit(
            action=action,
            status="error",
            connection_id=connection_id,
            summary="preview failed",
            detail={"error": str(exc), "operation": plan.operation, "table": plan.table, "prompt": prompt},
        )
        raise HTTPException(status_code=400, detail=f"Preview failed: {exc}") from exc
    finally:
        engine.dispose()

    pending = create_pending(connection_id, _plan_to_dict(plan))
    if stats["blocked"]:
        mark_cancelled(pending["id"])

    audit.write_audit(
        action=action,
        status="blocked" if stats["blocked"] else "success",
        connection_id=connection_id,
        summary=(
            stats["block_reason"]
            if stats["blocked"]
            else f"preview {plan.operation} on {plan.table} ({stats['affected_count']} rows)"
        ),
        detail={
            "preview_id": pending["id"],
            "operation": plan.operation,
            "table": plan.table,
            "sql": plan.sql,
            "affected_count": stats["affected_count"],
            "blocked": stats["blocked"],
            "prompt": prompt,
        },
    )

    return MutatePreviewResult(
        preview_id=pending["id"],
        operation=plan.operation,
        table=plan.table,
        schema_name=plan.schema_name,
        sql=plan.sql,
        affected_count=stats["affected_count"],
        sample_rows=stats["sample_rows"],
        blocked=stats["blocked"],
        block_reason=stats["block_reason"],
        expires_at=pending["expires_at"],
        explanation=explanation,
        reply=reply,
    )


@router.post("/{connection_id}/mutate/preview", response_model=MutatePreviewResult)
def mutate_preview(connection_id: str, body: MutateRequest):
    connection = _load_connection(connection_id)
    policy = policy_store.get_policy(connection_id)
    try:
        plan = build_mutate_plan(
            dialect=connection["dialect"],
            policy=policy,
            operation=body.operation,
            table=body.table,
            schema_name=body.schema_name,
            values=body.values,
            set_values=body.set_values,
            filters=[f.model_dump() for f in body.filters],
        )
    except SqlGuardError as exc:
        audit.write_audit(
            action="mutate.preview",
            status="error",
            connection_id=connection_id,
            summary=exc.message,
            detail={"operation": body.operation, "table": body.table},
        )
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return _preview_from_plan(connection_id, connection, policy, plan)


@router.post("/{connection_id}/mutate/nl", response_model=MutatePreviewResult)
def mutate_nl(connection_id: str, body: NlMutateRequest):
    connection = _load_connection(connection_id)
    policy = policy_store.get_policy(connection_id)

    engine = create_db_engine(get_engine_cfg(connection))
    try:
        overview = introspect_schema(engine, connection_id, connection["dialect"])
        schema_tables = [t.model_dump() for t in overview.tables]
    except Exception as exc:  # noqa: BLE001
        audit.write_audit(
            action="mutate.nl",
            status="error",
            connection_id=connection_id,
            summary="schema introspection failed",
            detail={"error": str(exc), "prompt": body.prompt},
        )
        raise HTTPException(status_code=400, detail=f"Schema introspection failed: {exc}") from exc
    finally:
        engine.dispose()

    try:
        plan, explanation, reply = nl_to_mutate_plan(
            body.prompt,
            dialect=connection["dialect"],
            policy=policy,
            schema_tables=schema_tables,
            analysis_context=analysis_context_text(
                get_analysis(
                    connection_id,
                    [
                        {"table": t.get("table"), "schema_name": t.get("schema_name")}
                        for t in (policy.get("tables") or [])
                    ],
                )
            ),
        )
    except LlmNotConfigured as exc:
        audit.write_audit(
            action="mutate.nl",
            status="error",
            connection_id=connection_id,
            summary="LLM not configured",
            detail={"prompt": body.prompt},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SqlGuardError as exc:
        audit.write_audit(
            action="mutate.nl",
            status="error",
            connection_id=connection_id,
            summary=exc.message,
            detail={"prompt": body.prompt},
        )
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        audit.write_audit(
            action="mutate.nl",
            status="error",
            connection_id=connection_id,
            summary="LLM request failed",
            detail={"error": str(exc), "prompt": body.prompt},
        )
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc

    if plan is None:
        audit.write_audit(
            action="mutate.nl",
            status="success",
            connection_id=connection_id,
            summary="no mutation proposed",
            detail={"prompt": body.prompt, "reply": reply},
        )
        raise HTTPException(
            status_code=400,
            detail=reply or "No mutation proposed for this prompt",
        )

    return _preview_from_plan(
        connection_id,
        connection,
        policy,
        plan,
        action="mutate.nl",
        explanation=explanation,
        reply=reply,
        prompt=body.prompt,
    )


@router.post("/{connection_id}/mutate/confirm", response_model=MutateExecuteResult)
def mutate_confirm(connection_id: str, body: MutateConfirmRequest):
    connection = _load_connection(connection_id)
    policy = policy_store.get_policy(connection_id)
    pending = get_pending(body.preview_id)
    if not pending or pending["connection_id"] != connection_id:
        raise HTTPException(status_code=404, detail="Preview not found")
    if pending["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Preview status is {pending['status']}")
    if is_expired(pending):
        mark_cancelled(pending["id"])
        audit.write_audit(
            action="mutate.confirm",
            status="error",
            connection_id=connection_id,
            summary="preview expired",
            detail={"preview_id": body.preview_id},
        )
        raise HTTPException(status_code=400, detail="Preview expired; request a new preview")

    plan_data = pending["plan"]
    try:
        plan = build_mutate_plan(
            dialect=connection["dialect"],
            policy=policy,
            operation=plan_data["operation"],
            table=plan_data["table"],
            schema_name=plan_data.get("schema_name"),
            values=plan_data.get("values"),
            set_values=plan_data.get("set_values"),
            filters=plan_data.get("filters") or [],
        )
    except SqlGuardError as exc:
        mark_cancelled(pending["id"])
        audit.write_audit(
            action="mutate.confirm",
            status="error",
            connection_id=connection_id,
            summary=exc.message,
            detail={"preview_id": body.preview_id},
        )
        raise HTTPException(status_code=400, detail=exc.message) from exc

    max_rows = int(policy.get("max_rows_per_mutation") or 100)
    engine = create_db_engine(get_engine_cfg(connection))
    try:
        stats = preview_plan(engine, plan, max_rows=max_rows)
        if stats["blocked"]:
            mark_cancelled(pending["id"])
            audit.write_audit(
                action="mutate.confirm",
                status="blocked",
                connection_id=connection_id,
                summary=stats["block_reason"],
                detail={"preview_id": body.preview_id, "sql": plan.sql},
            )
            raise HTTPException(status_code=400, detail=stats["block_reason"])
        result = execute_plan(engine, plan)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        audit.write_audit(
            action="mutate.confirm",
            status="error",
            connection_id=connection_id,
            summary="mutation failed",
            detail={"preview_id": body.preview_id, "sql": plan.sql, "error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=f"Mutation failed: {exc}") from exc
    finally:
        engine.dispose()

    mark_executed(pending["id"])
    audit.write_audit(
        action="mutate.confirm",
        status="success",
        connection_id=connection_id,
        summary=f"executed {plan.operation} on {plan.table}",
        detail={
            "preview_id": pending["id"],
            "operation": plan.operation,
            "table": plan.table,
            "sql": plan.sql,
            "rowcount": result["rowcount"],
        },
    )
    return MutateExecuteResult(
        preview_id=pending["id"],
        operation=plan.operation,
        table=plan.table,
        sql=plan.sql,
        rowcount=int(result["rowcount"] or 0),
        lastrowid=result.get("lastrowid"),
        status="executed",
    )


@router.post("/{connection_id}/mutate/cancel/{preview_id}")
def mutate_cancel(connection_id: str, preview_id: str):
    pending = get_pending(preview_id)
    if not pending or pending["connection_id"] != connection_id:
        raise HTTPException(status_code=404, detail="Preview not found")
    if pending["status"] == "pending":
        mark_cancelled(preview_id)
    audit.write_audit(
        action="mutate.cancel",
        status="success",
        connection_id=connection_id,
        summary="cancelled pending mutation",
        detail={"preview_id": preview_id},
    )
    return {"preview_id": preview_id, "status": "cancelled"}
