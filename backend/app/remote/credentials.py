"""Serviço de Credentials (Fase 12.2): emissão/rotação/revogação.

O token bruto existe apenas no momento da emissão (retorno único) e nunca é
persistido — o banco guarda somente `sha256:<hash>`.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.enums import DeviceStatus
from app.models.remote import Credential, Device
from app.models.session import utcnow
from app.remote.crypto import derive_secret, generate_token, verify_secret
from app.remote.devices import json_meta, load_meta
from app.remote.identity_events import (
    AUDIT_CREDENTIAL_CREATED,
    AUDIT_CREDENTIAL_REVOKED,
    OPS_CREDENTIAL_CREATED,
    OPS_CREDENTIAL_REVOKED,
    log_identity_event,
)
from app.remote.sessions import revoke_sessions_by_credential

logger = logging.getLogger("jarvis.remote")


class CredentialLimitError(ValueError):
    """Device atingiu o teto de credenciais ativas (rotação obrigatória)."""


def issue_credential(
    db: OrmSession,
    device: Device,
    *,
    expires_at=None,
    metadata: dict[str, Any] | None = None,
    enforce_limit: bool = True,
    known_token: str | None = None,
    audit: bool = True,
) -> tuple[Credential, str]:
    """Emite uma nova credencial para o device; retorna (credential, token).

    `known_token`: bootstrap do root device — a fonte do segredo é a
    configuração (REMOTE_DEVICE_TOKEN), nunca gerada em runtime.

    A autorização de device fica no autenticador: ISSUED para qualquer estado
    (PENDING pode ganhar token antes da aprovação; quem rejeita na hora é o
    `RemoteAuthenticator`, que exige ACTIVE). Revogado não recebe novas.
    """
    if device.status == DeviceStatus.REVOKED.value:
        raise ValueError(f"device revogado não recebe novas credenciais: {device.id}")

    if enforce_limit and active_count(db, device.id) >= settings.remote_max_active_credentials:
        raise CredentialLimitError(f"limite de credenciais ativas excedido ({device.id})")

    token = known_token or generate_token()
    credential = Credential(
        device_id=device.id,
        token_hash=derive_secret(token),
        expires_at=expires_at,
        metadata_json=json_meta(metadata),
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    if audit:
        log_identity_event(
            audit_action=AUDIT_CREDENTIAL_CREATED,
            ops_event=OPS_CREDENTIAL_CREATED,
            meta={"device_id": device.id, "credential_id": credential.id},
        )
    return credential, token


def active_count(db: OrmSession, device_id: str) -> int:
    now = utcnow()
    stmt = select(func.count()).select_from(Credential).where(
        Credential.device_id == device_id,
        Credential.revoked_at.is_(None),
    )
    return db.scalar(stmt) or 0


def get_credential_by_token(db: OrmSession, token: str) -> tuple[Credential, Device] | None:
    """Localiza a credencial por token derivado (nunca busca por valor bruto)."""
    if not token:
        return None
    credential = db.scalar(
        select(Credential).where(Credential.token_hash == derive_secret(token))
    )
    if credential is None:
        return None
    device = db.get(Device, credential.device_id)
    if device is None:
        return None
    return credential, device


def credential_matches_token(credential: Credential, token: str) -> bool:
    return verify_secret(token, credential.token_hash)


def revoke_credential(db: OrmSession, credential_id: str) -> Credential:
    credential = db.get(Credential, credential_id)
    if credential is None:
        raise KeyError(f"credential não encontrada: {credential_id}")
    if credential.revoked_at is None:
        credential.revoked_at = utcnow()
    db.commit()
    revoke_sessions_by_credential(db, credential_id)
    log_identity_event(
        audit_action=AUDIT_CREDENTIAL_REVOKED,
        ops_event=OPS_CREDENTIAL_REVOKED,
        meta={"device_id": credential.device_id, "credential_id": credential.id},
    )
    return credential


def list_credentials(
    db: OrmSession, *, device_id: str | None = None
) -> list[Credential]:
    stmt = select(Credential).order_by(Credential.created_at.desc())
    if device_id is not None:
        stmt = stmt.where(Credential.device_id == device_id)
    return list(db.scalars(stmt).all())


def credential_meta(credential: Credential) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "credential_id": credential.id,
        "device_id": credential.device_id,
        "active": credential.is_active,
        "created_at": credential.created_at,
    }
    if credential.last_used_at is not None:
        meta["last_used_at"] = credential.last_used_at
    if credential.expires_at is not None:
        meta["expires_at"] = credential.expires_at
    if credential.revoked_at is not None:
        meta["revoked_at"] = credential.revoked_at
    return meta