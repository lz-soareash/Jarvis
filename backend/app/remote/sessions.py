"""Serviço de sessões remotas (Fase 12.2).

`RemoteSession` é separada do device: permite encerrar/revogar uma sessão
individual sem afetar credenciais, e revogar device/credencial invalida as
sessões associadas. Sessões guardam apenas metadados de transporte sanitizados.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from datetime import datetime, timedelta, timezone

from app.core.enums import RemoteSessionStatus
from app.models.remote import Credential, Device, RemoteSession
from app.models.session import ensure_utc, utcnow
from app.remote.config import remote_session_ttl
from app.remote.errors import RemoteError, RemoteErrorCode
from app.remote.events import publish_event
from app.remote.identity_events import (
    AUDIT_SESSION_ENDED,
    OPS_SESSION_ENDED,
    log_identity_event,
)

logger = logging.getLogger("jarvis.remote.sessions")

_ACTIVE = (RemoteSessionStatus.ACTIVE.value, RemoteSessionStatus.AUTHENTICATED.value)

# Vocabulário de eventos de sessão expirada/revogada (Fase 16) — propaga ao SSE
# para a UI mobile saber do estado sem polling.
OPS_SESSION_EXPIRED = "session.expired"
OPS_SESSION_REVOKED = "session.revoked"


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


# ---------------------------------------------------------------------------
# Fase 16 — ciclo de vida completo: TTL, expiração e revogação individual
# ---------------------------------------------------------------------------

def is_expired(session: RemoteSession, *, ttl: int | None = None) -> bool:
    """Sessão ultrapassou o TTL de atividade (sem contato recente).

    `ttl=0` (config) ou `ttl=None` com config 0 = sem expiração. A checagem é
    puramente de tempo sobre `last_seen_at` — não requer coluna nova nem migração.
    """
    ttl = remote_session_ttl() if ttl is None else ttl
    if ttl is None or ttl <= 0 or session.status not in _ACTIVE:
        return False
    last = (
        session.last_seen_at
        if session.last_seen_at is not None
        else session.created_at
    )
    now_utc = ensure_utc(last)
    age = datetime.now(timezone.utc) - now_utc
    return age.total_seconds() > ttl


def ensure_session_valid(db: OrmSession, session: RemoteSession | None) -> RemoteSession:
    """Valida uma sessão para uso (autenticação/autorização já resolvidas).

    Regras mestre (Fase 16):
    - sessão inexistente        → INVALID_SESSION
    - status final (ended/revoked) → INVALID_SESSION / SESSION_REVOKED
    - além do TTL de atividade    → SESSION_EXPIRED (e é encerrada no banco)
    - device revogado/não-ativo   → FORBIDDEN
    Atualiza `last_seen_at` no sucesso (uso legítimo prolonga a sessão).
    """
    if session is None:
        raise RemoteError(RemoteErrorCode.INVALID_SESSION, "sessão inválida")
    if is_expired(session):
        if session.status in _ACTIVE:
            session.status = RemoteSessionStatus.ENDED.value
            session.ended_at = utcnow()
            db.commit()
        publish_event(
            OPS_SESSION_EXPIRED,
            {"session_id": session.id, "device_id": session.device_id},
        )
        raise RemoteError(RemoteErrorCode.SESSION_EXPIRED, "sessão expirada")
    if session.status == RemoteSessionStatus.REVOKED.value:
        raise RemoteError(RemoteErrorCode.SESSION_REVOKED, "sessão revogada")
    if session.status not in _ACTIVE:
        raise RemoteError(RemoteErrorCode.INVALID_SESSION, "sessão inválida")
    device = db.get(Device, session.device_id)
    # Fase 21 — Device Trust: PAIRED/ACTIVE autorizam a sessão; os demais não.
    if device is None or not device.is_trusted:
        raise RemoteError(RemoteErrorCode.FORBIDDEN, "device não autorizado")
    touch_session(db, session)
    return session


def expire_stale_sessions(db: OrmSession, *, ttl: int | None = None, limit: int = 500) -> int:
    """Encerra (ENDED) sessões ativas além do TTL (limpeza periódica/on-demand).

    Best-effort e assíncrona por natureza: pode ser chamada no fallback de
    autenticação/pairing sem bloquear o fluxo principal. Retorna quantas sessões
    foram expiradas.
    """
    ttl = remote_session_ttl() if ttl is None else ttl
    if ttl is None or ttl <= 0:
        return 0
    now = utcnow()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl)
    stale = list(
        db.scalars(
            select(RemoteSession)
            .where(RemoteSession.status.in_(_ACTIVE))
            .order_by(RemoteSession.last_seen_at.asc())
            .limit(limit)
        ).all()
    )
    expired = 0
    for session in stale:
        last = session.last_seen_at if session.last_seen_at is not None else session.created_at
        if ensure_utc(last) <= cutoff:
            session.status = RemoteSessionStatus.ENDED.value
            session.ended_at = now
            expired += 1
            publish_event(
                OPS_SESSION_EXPIRED,
                {"session_id": session.id, "device_id": session.device_id},
            )
        else:
            continue
    if expired:
        db.commit()
        logger.info("Sessões remotas expiradas por TTL: %s", expired)
    return expired


def revoke_session(db: OrmSession, session_id: str) -> RemoteSession:
    """Revoga uma sessão individual (REVOKED) — auditoria + evento SSE."""
    session = db.get(RemoteSession, session_id)
    if session is None:
        raise KeyError(f"session não encontrada: {session_id}")
    if session.status != RemoteSessionStatus.REVOKED.value:
        session.status = RemoteSessionStatus.REVOKED.value
        session.revoked_at = utcnow()
    db.commit()
    publish_event(
        OPS_SESSION_REVOKED,
        {"session_id": session.id, "device_id": session.device_id},
    )
    log_identity_event(
        audit_action="remote.session.revoked",
        ops_event=OPS_SESSION_REVOKED,
        meta={"device_id": session.device_id, "session_id": session.id},
    )
    return session