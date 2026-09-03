"""Schemas da identidade remota (Fase 12.2).

Nunca expõem token_hash, código ou qualquer segredo — apenas metadados
sanitizados. O token bruto aparece em respostas de criação/emissão (uma única
vez) quando o protocolo assim exige.
"""

from datetime import datetime
from typing import Any

from app.schemas.base import APIModel


class DeviceOut(APIModel):
    id: str
    name: str
    device_type: str
    status: str
    created_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class CredentialOut(APIModel):
    id: str
    device_id: str
    active: bool
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class PairingOut(APIModel):
    id: str
    status: str
    attempts: int
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    device_id: str | None = None


class PairingCreateOut(APIModel):
    """Resposta de criação: o `code` é o único momento em que é visto."""

    pairing_id: str
    code: str
    expires_at: datetime
    ttl_seconds: int


class PairingSubmitIn(APIModel):
    code: str
    device_name: str
    device_type: str = "desktop"
    pairing_id: str | None = None
    metadata: dict[str, Any] | None = None


class PairingSubmitOut(APIModel):
    """Sucesso do pairing: `token` é emitido uma única vez aqui."""

    device_id: str
    token: str
    device: DeviceOut


class AuthIn(APIModel):
    token: str
    claimed_device_id: str | None = None
    transport_meta: dict[str, Any] | None = None


class AuthOut(APIModel):
    authenticated: bool = True
    device: DeviceOut
    session_id: str
    credential_id: str


class RemoteStatusOut(APIModel):
    enabled: bool
    configured: bool
    detail: str
    # Observabilidade do agente (Fase 12.3) — nunca expõe secrets.
    connection_state: str | None = None
    authenticated: bool | None = None
    healthy: bool | None = None
    device_id: str | None = None
    connected_at: datetime | None = None
    last_heartbeat: datetime | None = None
    reconnect_count: int | None = None
    last_error: str | None = None
    pending_commands: int | None = None
    active_session: str | None = None
    revocation: str | None = None


class Empty(APIModel):
    ok: bool = True