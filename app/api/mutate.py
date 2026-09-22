from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app import meta_db, policy_store
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
    explanation: str | None = None,
    reply: str | None = None,
) -> MutatePreviewResult:
    max_rows = int(policy.get("max_rows_per_mutation") or 100)
    engine = create_db_engine(get_engine_cfg(connection))
    try:
        stats = preview_plan(engine, plan, max_rows=max_rows)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Preview failed: {exc}") from exc
    finally:
        engine.dispose()

    pending = create_pending(connection_id, _plan_to_dict(plan))
    if stats["blocked"]:
        mark_cancelled(pending["id"])

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
        raise HTTPException(status_code=400, detail=f"Schema introspection failed: {exc}") from exc
    finally:
        engine.dispose()

    try:
        plan, explanation, reply = nl_to_mutate_plan(
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

    if plan is None:
        # No mutation proposed — fabricate empty preview-like response without pending id
        raise HTTPException(
            status_code=400,
            detail=reply or "No mutation proposed for this prompt",
        )

    return _preview_from_plan(
        connection_id,
        connection,
        policy,
        plan,
        explanation=explanation,
        reply=reply,
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
        raise HTTPException(status_code=400, detail="Preview expired; request a new preview")

    plan_data = pending["plan"]
    try:
        # Rebuild to re-validate against current policy
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
        raise HTTPException(status_code=400, detail=exc.message) from exc

    max_rows = int(policy.get("max_rows_per_mutation") or 100)
    engine = create_db_engine(get_engine_cfg(connection))
    try:
        stats = preview_plan(engine, plan, max_rows=max_rows)
        if stats["blocked"]:
            mark_cancelled(pending["id"])
            raise HTTPException(status_code=400, detail=stats["block_reason"])
        result = execute_plan(engine, plan)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Mutation failed: {exc}") from exc
    finally:
        engine.dispose()

    mark_executed(pending["id"])
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
    return {"preview_id": preview_id, "status": "cancelled"}
