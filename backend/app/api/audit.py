from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session as OrmSession

from app.db.session import get_db
from app.schemas.audit import AuditEntryOut
from app.services import audit as audit_service

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit", response_model=list[AuditEntryOut])
def list_audit(
    limit: int = Query(100, ge=1, le=500),
    db: OrmSession = Depends(get_db),
) -> list[AuditEntryOut]:
    """Trilho de auditoria: execuções de ferramentas e decisões de permissão."""
    return [
        AuditEntryOut.model_validate(e) for e in audit_service.list_audit(db, limit=limit)
    ]