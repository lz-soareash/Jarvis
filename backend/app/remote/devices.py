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

from app.core.config import settings
from app.core.enums import DeviceStatus, DeviceType
from app.models.remote import Device
from app.models.session import utcnow
from app.remote.identity_events import (
    AUDIT_DEVICE_CREATED,
    AUDIT_DEVICE_CAPABILITIES,
    AUDIT_DEVICE_RENAMED,
    AUDIT_DEVICE_REVOKED,
    AUDIT_CREDENTIAL_REVOKED,
    OPS_DEVICE_CREATED,
    OPS_DEVICE_REGISTERED,
    OPS_DEVICE_RENAMED,
    OPS_DEVICE_REVOKED,
    OPS_CREDENTIAL_REVOKED,
    log_identity_event,
)

logger = logging.getLogger("jarvis.remote")

ACTIVE_STATUSES = (DeviceStatus.ACTIVE.value, DeviceStatus.PAIRED.value)
TRUSTED_STATUSES = (DeviceStatus.ACTIVE.value, DeviceStatus.PAIRED.value)


class DeviceLimitExceeded(ValueError):
    """Atingiu o teto de devices confiáveis (device_max_devices)."""


class DeviceInvalid(ValueError):
    """Registro/atualização de device com dados inválidos."""


def trusted_statuses() -> tuple[str, ...]:
    """Fase 21 — Device Trust: PAIRED/ACTIVE autenticam; demais não.

    Mantém a semântica histórica: `ACTIVE` continua sendo o estado confiável.
    """
    return TRUSTED_STATUSES


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
    platform: str = "web",
    client_version: str | None = None,
    capabilities: list[str] | None = None,
) -> Device:
    """Cria um device com id emitido pelo servidor (ou id explícito, ex.: root)."""
    device = Device(
        id=device_id or _new_uuid(),
        name=name,
        device_type=device_type,
        status=status,
        metadata_json=json_meta(metadata),
        platform=_norm_platform(platform),
        client_version=client_version,
        capabilities_json=json_meta(capabilities) if capabilities else None,
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
    if not device.is_trusted:  # Fase 21 — PAIRED/ACTIVE contam como "vistos"
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
        "platform": device.platform,
        "client_version": device.client_version,
    }


# ---------------------------------------------------------------------------
# Fase 21 — Device Bridge (identidade de clientes finos multiplataforma)
# ---------------------------------------------------------------------------

# Vocabulário fixo e seguro de plataformas — o cliente não inventa plataformas
# livres (evita junk data e normaliza as UIs). Tudo o que cair fora é "outro".
_KNOWN_PLATFORMS = {
    "desktop": "desktop",
    "mobile": "mobile",
    "tablet": "tablet",
    "web": "web",
    "windows": "desktop",
    "android": "mobile",
    "ios": "mobile",
}


def _norm_platform(raw: str | None) -> str:
    return _KNOWN_PLATFORMS.get((raw or "").strip().lower(), "web")


def register_device(
    db: OrmSession,
    *,
    name: str,
    device_type: str = DeviceType.DESKTOP.value,
    platform: str = "web",
    client_version: str | None = None,
    capabilities: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Device:
    """Fase 21 — registro VISÍVEL de um dispositivo (status PENDING).

    *Sem segredo*: cria um device PENDING (id emitido pelo servidor) que fica
    aguardando o pareamento por código. Quando o código for submetido com
    `pending_device_id`, este registro é promovido a confiável — nunca um
    device PENDING autentica. O teto de pendentes evita acúmulo de órfãos.
    """
    if not name or not name.strip():
        raise DeviceInvalid("nome do dispositivo é obrigatório")
    if cap := [c for c in (capabilities or []) if not isinstance(c, str)]:
        raise DeviceInvalid(f"capabilities devem ser strings ({cap!r})")
    capabilities = [c.strip().lower() for c in (capabilities or []) if c.strip()]
    max_pending = settings.device_max_pending
    if max_pending > 0:  # 0 = sem teto (uso em testes locais pequenos)
        pending = _count_by_status(db, DeviceStatus.PENDING.value)
        if pending >= max_pending:
            raise DeviceLimitExceeded(
                f"atingiu o teto de devices pendentes ({max_pending})"
            )
    return create_device(
        db,
        name=name.strip()[:120],
        device_type=device_type,
        status=DeviceStatus.PENDING.value,
        metadata=metadata,
        platform=platform,
        client_version=client_version,
        capabilities=capabilities,
    )


def rename_device(db: OrmSession, device_id: str, name: str) -> Device:
    """Fase 21 — renomeia um dispositivo (nome de exibição; auditoria)."""
    device = db.get(Device, device_id)
    if device is None:
        raise KeyError(f"device não encontrado: {device_id}")
    if not name or not name.strip():
        raise DeviceInvalid("nome do dispositivo é obrigatório")
    previous = device.name
    device.name = name.strip()[:120]
    db.commit()
    log_identity_event(
        audit_action=AUDIT_DEVICE_RENAMED,
        ops_event=OPS_DEVICE_RENAMED,
        meta={"device_id": device.id, "previous": previous, "name": device.name},
    )
    return device


def update_capabilities(
    db: OrmSession,
    device_id: str,
    *,
    platform: str | None = None,
    client_version: str | None = None,
    capabilities: list[str] | None = None,
) -> Device:
    """Fase 21 — atualiza metadados de identidade do cliente (sanitizado).

    Usado no registro e em heartbeats: o cliente reporta a própria versão e
    capacidades. NUNCA eleva permissões nem toca o status de confiança.
    """
    device = db.get(Device, device_id)
    if device is None:
        raise KeyError(f"device não encontrado: {device_id}")
    cap = [c for c in (capabilities or []) if isinstance(c, str)]
    caps = [c.strip().lower() for c in cap if c.strip()]
    if platform is not None:
        device.platform = _norm_platform(platform)
    if client_version is not None:
        device.client_version = (client_version or "").strip()[:40] or None
    if capabilities is not None:
        device.capabilities_json = json_meta(caps) if caps else None
    db.commit()
    log_identity_event(
        audit_action=AUDIT_DEVICE_CAPABILITIES,
        ops_event=OPS_DEVICE_REGISTERED,
        meta=_device_meta(device),
    )
    return device


def heartbeat(
    db: OrmSession,
    device: Device,
    *,
    event: str = "heartbeat",
    transport_meta: dict[str, Any] | None = None,
) -> Device:
    """Fase 21 — batida de vida do cliente (sem auditoria, sem ruído).

    `event` ∈ {connect, reconnect, heartbeat, disconnect} aciona os eventos
    observáveis `device.connected/reconnected/disconnected` e renova
    `last_seen_at`. A SEMÂNTICA de estado (quando virou connected) vem do
    cliente; o servidor autentica e propaga — nunca inventa estado.
    """
    from app.remote.identity_events import (
        OPS_DEVICE_RECONNECTED,
        OPS_DEVICE_DISCONNECTED,
        OPS_DEVICE_CONNECTED,
    )

    device.last_seen_at = utcnow()
    db.commit()
    if event == "disconnect":
        publish_device_event(OPS_DEVICE_DISCONNECTED, device, transport_meta)
    elif event == "reconnect" and device_reconnect_enabled():
        publish_device_event(OPS_DEVICE_RECONNECTED, device, transport_meta)
    elif event == "connect":
        publish_device_event(OPS_DEVICE_CONNECTED, device, transport_meta)
    return device


def device_reconnect_enabled() -> bool:
    return bool(settings.device_reconnect_enabled)


def publish_device_event(
    event_type: str,
    device: Device,
    transport_meta: dict[str, Any] | None = None,
) -> None:
    from app.remote.events import publish_event

    meta: dict[str, Any] = {
        "device_id": device.id,
        "status": device.status,
        "platform": device.platform,
        "client_version": device.client_version,
    }
    if device.jarvis_session_id:
        meta["conversation_id"] = device.jarvis_session_id
    if transport_meta:
        meta["transport"] = transport_meta
    publish_event(event_type, meta)


def register_device_bridge_events(device: Device) -> None:
    """Fase 21 — evento `device.registered` no barramento (visibilidade imediata)."""
    from app.remote.events import publish_event

    publish_event(
        OPS_DEVICE_REGISTERED,
        {
            "device_id": device.id,
            "status": device.status,
            "platform": device.platform,
            "client_version": device.client_version,
        },
    )


def count_devices(db: OrmSession, *, statuses: tuple[str, ...] | None = None) -> int:
    if statuses is None:
        statuses = TRUSTED_STATUSES
    return len(list(db.scalars(select(Device).where(Device.status.in_(statuses))).all()))


def _count_by_status(db: OrmSession, status: str) -> int:
    return len(list(db.scalars(select(Device).where(Device.status == status)).all()))


def ensure_device_capacity(db: OrmSession) -> None:
    """Fase 21 — garante espaço para confiar um novo device (teto configurável).

    Eleva `DeviceLimitExceeded` quando o número de devices confiáveis já está
    no teto `device_max_devices` (0 = ilimitado, uso em testes).
    """
    max_devices = settings.device_max_devices
    if max_devices and max_devices > 0:
        trusted = count_devices(db)
        if trusted >= max_devices:
            raise DeviceLimitExceeded(
                f"atingiu o teto de devices confiáveis ({max_devices})"
            )


def _new_uuid() -> str:
    from uuid import uuid4

    return str(uuid4())