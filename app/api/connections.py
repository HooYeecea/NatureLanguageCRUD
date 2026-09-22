from fastapi import APIRouter, HTTPException

from app import audit, meta_db
from app.db.engines import create_db_engine, test_engine
from app.db.schema import get_engine_cfg, introspect_schema, introspect_table
from app.models import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionTestResult,
    ConnectionUpdate,
    SchemaOverview,
    TableInfo,
)

router = APIRouter(prefix="/api/connections", tags=["connections"])


def _to_out(data: dict) -> ConnectionOut:
    return ConnectionOut(**data)


@router.get("", response_model=list[ConnectionOut])
def list_connections():
    return [_to_out(c) for c in meta_db.list_connections()]


@router.post("", response_model=ConnectionOut, status_code=201)
def create_connection(body: ConnectionCreate):
    created = meta_db.create_connection(body.model_dump())
    audit.write_audit(
        action="connection.create",
        status="success",
        connection_id=created["id"],
        summary=f"created connection {created['name']}",
        detail={"name": created["name"], "dialect": created["dialect"]},
    )
    return _to_out(created)


@router.get("/{connection_id}", response_model=ConnectionOut)
def get_connection(connection_id: str):
    data = meta_db.get_connection(connection_id)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")
    return _to_out(data)


@router.put("/{connection_id}", response_model=ConnectionOut)
def update_connection(connection_id: str, body: ConnectionUpdate):
    payload = body.model_dump(exclude_unset=True)
    updated = meta_db.update_connection(connection_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Connection not found")
    logged_fields = [k for k in payload.keys() if k != "password"]
    audit.write_audit(
        action="connection.update",
        status="success",
        connection_id=connection_id,
        summary=f"updated connection {updated['name']}",
        detail={"fields": logged_fields},
    )
    return _to_out(updated)


@router.delete("/{connection_id}", status_code=204)
def delete_connection(connection_id: str):
    existing = meta_db.get_connection(connection_id)
    ok = meta_db.delete_connection(connection_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Connection not found")
    audit.write_audit(
        action="connection.delete",
        status="success",
        connection_id=connection_id,
        summary=f"deleted connection {existing['name'] if existing else connection_id}",
        detail={"name": existing["name"] if existing else None},
    )
    return None


@router.post("/{connection_id}/test", response_model=ConnectionTestResult)
def test_connection(connection_id: str):
    data = meta_db.get_connection(connection_id, include_password=True)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")
    engine = create_db_engine(get_engine_cfg(data))
    ok, message, version = test_engine(engine)
    audit.write_audit(
        action="connection.test",
        status="success" if ok else "error",
        connection_id=connection_id,
        summary=message,
        detail={"server_version": version},
    )
    return ConnectionTestResult(ok=ok, message=message, server_version=version)


@router.get("/{connection_id}/schema", response_model=SchemaOverview)
def get_schema(connection_id: str):
    data = meta_db.get_connection(connection_id, include_password=True)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")
    engine = create_db_engine(get_engine_cfg(data))
    try:
        return introspect_schema(engine, connection_id, data["dialect"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Schema introspection failed: {exc}") from exc
    finally:
        engine.dispose()


@router.get("/{connection_id}/schema/{table_name}", response_model=TableInfo)
def get_table_schema(connection_id: str, table_name: str, schema_name: str | None = None):
    data = meta_db.get_connection(connection_id, include_password=True)
    if not data:
        raise HTTPException(status_code=404, detail="Connection not found")
    engine = create_db_engine(get_engine_cfg(data))
    try:
        table = introspect_table(engine, table_name, schema_name)
        if not table:
            raise HTTPException(status_code=404, detail=f"Table not found: {table_name}")
        return table
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Schema introspection failed: {exc}") from exc
    finally:
        engine.dispose()
