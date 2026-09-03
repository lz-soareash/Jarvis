"""Serviço de sessões remotas (Fase 12.2).

`RemoteSession` é separada do device: permite encerrar/revogar uma sessão
individual sem afetar credenciais, e revogar device/credencial invalida as
sessões associadas. Sessões guardam apenas metadados de transporte sanitizados.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.enums import RemoteSessionStatus
from app.models.remote import Credential, Device, RemoteSession
from app.models.session import utcnow
from app.remote.identity_events import (
    AUDIT_SESSION_ENDED,
    OPS_SESSION_ENDED,
    log_identity_event,
)

_ACTIVE = (RemoteSessionStatus.ACTIVE.value, RemoteSessionStatus.AUTHENTICATED.value)


def create_session(
    db: OrmSession,
    *,
    device: Device,
    credential: Credential | None = None,
    transport_meta: dict[str, Any] | None = None,
) -> RemoteSession:
    """Abre uma sessão ativa para o device (chamada pelo autenticador)."""
    session = RemoteSession(
        device_id=device.id,
        credential_id=credential.id if credential else None,
        status=RemoteSessionStatus.ACTIVE.value,
        last_seen_at=utcnow(),
        transport_meta_json=json.dumps(transport_meta or {}, ensure_ascii=False, default=str),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: OrmSession, session_id: str) -> RemoteSession | None:
    return db.get(RemoteSession, session_id)


def list_sessions(
    db: OrmSession, *, device_id: str | None = None, active_only: bool = False
) -> list[RemoteSession]:
    stmt = select(RemoteSession).order_by(RemoteSession.created_at.desc())
    if device_id is not None:
        stmt = stmt.where(RemoteSession.device_id == device_id)
    if active_only:
        stmt = stmt.where(RemoteSession.status.in_(_ACTIVE))
    return list(db.scalars(stmt).all())


def touch_session(db: OrmSession, session: RemoteSession) -> None:
    session.last_seen_at = utcnow()
    db.commit()


def touch_device_safe(db: OrmSession, device: Device) -> None:
    """Atualiza `last_seen_at` do device (sem auditoria — ruído não-auditável)."""
    device.last_seen_at = utcnow()
    db.commit()


def end_session(db: OrmSession, session_id: str) -> RemoteSession:
    """Encerra uma sessão (status ENDED). Registra auditoria sanitizada."""
    session = db.get(RemoteSession, session_id)
    if session is None:
        raise KeyError(f"session não encontrada: {session_id}")
    if session.status not in _ACTIVE and session.status != RemoteSessionStatus.ENDED.value:
        session.ended_at = utcnow()
    if session.status != RemoteSessionStatus.ENDED.value:
        session.status = RemoteSessionStatus.ENDED.value
        session.ended_at = utcnow()
    db.commit()
    log_identity_event(
        audit_action=AUDIT_SESSION_ENDED,
        ops_event=OPS_SESSION_ENDED,
        meta={"device_id": session.device_id, "session_id": session.id},
    )
    return session


def revoke_sessions_by_credential(db: OrmSession, credential_id: str) -> int:
    """Revoga sessões ativas vinculadas a uma credencial (rotação/revogação)."""
    now = utcnow()
    sessions = list(
        db.scalars(
            select(RemoteSession).where(
                RemoteSession.credential_id == credential_id,
                RemoteSession.status.in_(_ACTIVE),
            )
        ).all()
    )
    for session in sessions:
        session.status = RemoteSessionStatus.REVOKED.value
        session.revoked_at = now
    db.commit()
    return len(sessions)


def revoke_sessions_by_device(db: OrmSession, device_id: str) -> int:
    """Revoga sessões ativas de um device (usado na revogação de device)."""
    now = utcnow()
    sessions = list(
        db.scalars(
            select(RemoteSession).where(
                RemoteSession.device_id == device_id,
                RemoteSession.status.in_(_ACTIVE),
            )
        ).all()
    )
    for session in sessions:
        session.status = RemoteSessionStatus.REVOKED.value
        session.revoked_at = now
    db.commit()
    return len(sessions)