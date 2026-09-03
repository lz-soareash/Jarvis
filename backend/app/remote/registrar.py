"""Bootstrap do root device (Fase 12.2, spec §9).

O primeiro/principal device (o próprio JARVIS ao lado da Gateway) tem identidade
explícita vinda de configuração: `REMOTE_DEVICE_ID` + `REMOTE_DEVICE_TOKEN`.
Regras:
- Idempotente: já existe → garante credencial ativa do token configurado.
- Nenhum admin implícito: o root device é um device comum (ACTIVE) cuja
  autorização continua passando pelo mesmo autenticador e, futuramente, pelo
  Permission Engine. A configuração é a FONTE do segredo inicial; o banco
  guarda apenas o hash (não vira "credential DB" em texto claro).
- Device revogado não é ressuscitado silenciosamente.
- Com `REMOTE_ENABLED=false` (padrão) nada é criado.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.enums import DeviceStatus, DeviceType
from app.models.remote import Credential, Device
from app.remote.crypto import derive_secret
from app.remote.devices import create_device
from app.remote.identity_events import (
    AUDIT_CREDENTIAL_CREATED,
    OPS_CREDENTIAL_CREATED,
    log_identity_event,
)

logger = logging.getLogger("jarvis.remote")


def bootstrap_root_device(db: OrmSession) -> Device | None:
    """Registra/valida o root device a partir da configuração. Retorna None se inativo."""
    if not settings.remote_enabled or not settings.remote_device_id:
        return None

    device_id = settings.remote_device_id
    token = settings.remote_device_token
    device = db.get(Device, device_id)

    if device is None:
        if not token:
            logger.warning(
                "REMOTE_DEVICE_ID definido mas REMOTE_DEVICE_TOKEN vazio — root device não registrado"
            )
            return None
        device = create_device(
            db,
            name="Root device (bootstrap)",
            device_type=DeviceType.DESKTOP.value,
            status=DeviceStatus.ACTIVE.value,
            device_id=device_id,
            metadata={"bootstrap": True},
        )
        logger.info("Root device registrado: %s", device_id)
        return _ensure_credential(db, device, token)

    if device.status == DeviceStatus.REVOKED.value:
        logger.warning("Root device revogado — não será ressuscitado: %s", device_id)
        return device

    if not token:
        return device
    return _ensure_credential(db, device, token)


def _ensure_credential(db: OrmSession, device: Device, token: str) -> Device:
    """Garante uma credencial ativa derivada do token configurado (idempotente)."""
    token_hash = derive_secret(token)
    existing = db.scalar(
        select(Credential).where(Credential.token_hash == token_hash)
    )
    if existing is not None and existing.revoked_at is None:
        if existing.device_id != device.id:
            logger.warning(
                "Token de root já vinculado a outro device (%s) — crédito mantido",
                existing.device_id,
            )
        return device

    credential = Credential(
        device_id=device.id,
        token_hash=token_hash,
        metadata_json='{"bootstrap": true}',
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    log_identity_event(
        audit_action=AUDIT_CREDENTIAL_CREATED,
        ops_event=OPS_CREDENTIAL_CREATED,
        meta={"device_id": device.id, "credential_id": credential.id},
    )
    logger.info("Credencial raiz do device %s garantida", device.id)
    return device