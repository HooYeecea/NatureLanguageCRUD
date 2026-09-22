from fastapi import APIRouter, HTTPException

from app import audit, meta_db
from app import policy as policy_guard
from app import policy_store
from app.db.engines import create_db_engine
from app.db.schema import get_engine_cfg, introspect_schema
from app.models import (
    AccessPolicyOut,
    AccessPolicyUpsert,
    PolicyCheckRequest,
    PolicyCheckResult,
    TablePolicy,
)

router = APIRouter(prefix="/api/connections", tags=["policies"])


def _require_connection(connection_id: str) -> dict:
    data = meta_db.get_connection(connection_id)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")
    return data


@router.get("/{connection_id}/policy", response_model=AccessPolicyOut)
def get_policy(connection_id: str):
    _require_connection(connection_id)
    return AccessPolicyOut(**policy_store.get_policy(connection_id))


@router.put("/{connection_id}/policy", response_model=AccessPolicyOut)
def put_policy(connection_id: str, body: AccessPolicyUpsert):
    _require_connection(connection_id)
    seen: set[tuple[str | None, str]] = set()
    for t in body.tables:
        key = (t.schema_name, t.table)
        if key in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate table policy: {t.schema_name + '.' if t.schema_name else ''}{t.table}",
            )
        seen.add(key)

    saved = policy_store.upsert_policy(connection_id, body.model_dump())
    audit.write_audit(
        action="policy.update",
        status="success",
        connection_id=connection_id,
        summary=f"updated policy ({len(saved.get('tables') or [])} tables)",
        detail={
            "table_count": len(saved.get("tables") or []),
            "max_rows_per_mutation": saved.get("max_rows_per_mutation"),
            "max_rows_per_query": saved.get("max_rows_per_query"),
        },
    )
    return AccessPolicyOut(**saved)


@router.post("/{connection_id}/policy/allow-all", response_model=AccessPolicyOut)
def allow_all_tables(
    connection_id: str,
    allow_delete: bool = False,
    allow_insert: bool = True,
    allow_update: bool = True,
    allow_select: bool = True,
):
    """
    Convenience: whitelist every table discovered from live schema.
    Engine stays restricted; config is intentionally loose for MVP onboarding.
    """
    data = meta_db.get_connection(connection_id, include_password=True)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")

    engine = create_db_engine(get_engine_cfg(data))
    try:
        overview = introspect_schema(engine, connection_id, data["dialect"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Schema introspection failed: {exc}") from exc
    finally:
        engine.dispose()

    tables = [
        TablePolicy(
            table=t.name,
            schema_name=t.schema_name,
            allowed_columns=None,
            denied_columns=[],
            allow_select=allow_select,
            allow_insert=allow_insert,
            allow_update=allow_update,
            allow_delete=allow_delete,
        )
        for t in overview.tables
    ]
    body = AccessPolicyUpsert(tables=tables)
    saved = policy_store.upsert_policy(connection_id, body.model_dump())
    audit.write_audit(
        action="policy.allow_all",
        status="success",
        connection_id=connection_id,
        summary=f"allow-all policy ({len(tables)} tables)",
        detail={
            "table_count": len(tables),
            "allow_delete": allow_delete,
            "allow_insert": allow_insert,
            "allow_update": allow_update,
            "allow_select": allow_select,
        },
    )
    return AccessPolicyOut(**saved)


@router.post("/{connection_id}/policy/check", response_model=PolicyCheckResult)
def check_policy(connection_id: str, body: PolicyCheckRequest):
    _require_connection(connection_id)
    policy = policy_store.get_policy(connection_id)
    ok, reason, cols = policy_guard.check_access(
        policy,
        table=body.table,
        operation=body.operation,
        schema_name=body.schema_name,
        columns=body.columns,
    )
    return PolicyCheckResult(allowed=ok, reason=reason, effective_columns=cols)
