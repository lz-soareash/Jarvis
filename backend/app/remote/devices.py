"""Serviço de Devices (Fase 12.2): identidade e ciclo de vida.

O `id` do device é sempre emitido pelo servidor (uuid4) ou definido
explicitamente no bootstrap do root device — nunca aceito do cliente.
Revogar um device invalida credenciais e sessões, preservando o histórico.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.enums import DeviceStatus, DeviceType
from app.models.remote import Device
from app.models.session import utcnow
from app.remote.identity_events import (
    AUDIT_DEVICE_CREATED,
    AUDIT_DEVICE_REVOKED,
    AUDIT_CREDENTIAL_REVOKED,
    OPS_DEVICE_CREATED,
    OPS_DEVICE_REVOKED,
    OPS_CREDENTIAL_REVOKED,
    log_identity_event,
)

logger = logging.getLogger("jarvis.remote")

ACTIVE_STATUSES = (DeviceStatus.ACTIVE.value,)


def json_meta(metadata: dict[str, Any] | None) -> str | None:
    if metadata is None:
        return None
    return json.dumps(metadata, ensure_ascii=False, default=str)


def load_meta(encoded: str | None) -> dict[str, Any] | None:
    if not encoded:
        return None
    try:
        return json.loads(encoded)
    except (TypeError, ValueError):
        return None


def create_device(
    db: OrmSession,
    *,
    name: str,
    device_type: str = DeviceType.DESKTOP.value,
    status: str = DeviceStatus.ACTIVE.value,
    device_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    audit: bool = True,
) -> Device:
    """Cria um device com id emitido pelo servidor (ou id explícito, ex.: root)."""
    device = Device(
        id=device_id or _new_uuid(),
        name=name,
        device_type=device_type,
        status=status,
        metadata_json=json_meta(metadata),
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    if audit:
        log_identity_event(
            audit_action=AUDIT_DEVICE_CREATED,
            ops_event=OPS_DEVICE_CREATED,
            meta=_device_meta(device),
        )
    return device


def get_device(db: OrmSession, device_id: str) -> Device | None:
    return db.get(Device, device_id)


def list_devices(db: OrmSession, *, active_only: bool = False) -> list[Device]:
    stmt = select(Device).order_by(Device.created_at.desc())
    if active_only:
        stmt = stmt.where(Device.status.in_(ACTIVE_STATUSES))
    return list(db.scalars(stmt).all())


def touch_device(db: OrmSession, device: Device) -> None:
    """Atualiza `last_seen_at` (sem gerar auditoria — ruído não-auditável)."""
    if device.status != DeviceStatus.ACTIVE.value:
        return
    device.last_seen_at = utcnow()
    db.commit()


def revoke_device(db: OrmSession, device_id: str) -> Device:
    """Revoga device: invalida credenciais e sessões ativas (audit fica no histórico)."""
    from app.core.enums import RemoteSessionStatus

    device = db.get(Device, device_id)
    if device is None:
        raise KeyError(f"device não encontrado: {device_id}")

    if device.status != DeviceStatus.REVOKED.value:
        device.status = DeviceStatus.REVOKED.value
        device.revoked_at = utcnow()

    now = utcnow()
    revoked_creds = 0
    for cred in device.credentials:
        if cred.revoked_at is None:
            cred.revoked_at = now
            revoked_creds += 1
    for sess in device.sessions:
        if sess.revoked_at is None:
            sess.revoked_at = now
            sess.status = RemoteSessionStatus.REVOKED.value
    db.commit()

    log_identity_event(
        audit_action=AUDIT_DEVICE_REVOKED,
        ops_event=OPS_DEVICE_REVOKED,
        meta=_device_meta(device),
    )
    if revoked_creds:
        log_identity_event(
            audit_action=AUDIT_CREDENTIAL_REVOKED,
            ops_event=OPS_CREDENTIAL_REVOKED,
            meta={"device_id": device.id, "revoked_credentials": revoked_creds},
        )
    return device


def _device_meta(device: Device) -> dict[str, Any]:
    return {
        "device_id": device.id,
        "device_type": device.device_type,
        "status": device.status,
    }


def _new_uuid() -> str:
    from uuid import uuid4

    return str(uuid4())