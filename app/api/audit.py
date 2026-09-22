from fastapi import APIRouter, HTTPException, Query

from app import audit
from app.models import AuditLogOut

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
def list_audit_logs(
    connection_id: str | None = None,
    action: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return [
        AuditLogOut(**row)
        for row in audit.list_audits(
            connection_id=connection_id,
            action=action,
            status=status,
            limit=limit,
            offset=offset,
        )
    ]


@router.get("/{log_id}", response_model=AuditLogOut)
def get_audit_log(log_id: str):
    row = audit.get_audit(log_id)
    if not row:
        raise HTTPException(status_code=404, detail="Audit log not found")
    return AuditLogOut(**row)
