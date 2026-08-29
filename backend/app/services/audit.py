"""Auditoria (Fase 4): registro imutável de execuções e decisões de permissão."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.models import AuditLog

logger = logging.getLogger("jarvis.audit")


def log_action(
    db: OrmSession,
    *,
    action: str,
    session_id: str | None = None,
    tool: str | None = None,
    allowed: bool | None = None,
    detail: str | None = None,
) -> AuditLog:
    """Registra um evento auditável (execução, decisão, bloqueio, erro)."""
    entry = AuditLog(
        session_id=session_id,
        action=action,
        tool=tool,
        allowed=allowed,
        detail=(detail or "")[:2000],
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    logger.info("Auditoria: %s tool=%s allowed=%s", action, tool, allowed)
    return entry


def list_audit(db: OrmSession, limit: int = 100) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))
    return list(db.scalars(stmt).all())