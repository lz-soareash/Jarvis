"""Schemas da identidade remota (Fase 12.2).

Nunca expõem token_hash, código ou qualquer segredo — apenas metadados
sanitizados. O token bruto aparece em respostas de criação/emissão (uma única
vez) quando o protocolo assim exige.
"""

from datetime import datetime
from typing import Any

from pydantic import Field

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
    # Fase 21 — homólogos do Device Bridge (sanitizados; nunca identidade de HW).
    platform: str = "web"
    client_version: str | None = None
    capabilities: list[str] = []
    conversation_id: str | None = None  # jarvis_session_id (alvo de continuidade)


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
    # Fase 21 — identidade de cliente fino declarada no pareamento (sanitizada,
    # jamais usada p/ elevar permissões). `pending_device_id` ancora o registro
    # PENDING criado antes (id emitido pelo servidor) em vez de criar outro.
    pending_device_id: str | None = None
    platform: str = "web"
    client_version: str | None = None
    capabilities: list[str] = []


class DeviceRegisterIn(APIModel):
    """Fase 21 — registro visível de um dispositivo (status PENDING, sem segredo)."""

    name: str
    device_type: str = "desktop"
    platform: str = "web"
    client_version: str | None = None
    capabilities: list[str] = []
    metadata: dict[str, Any] | None = None


class DeviceRegisterOut(APIModel):
    """Resposta do registro: devolve o device PENDING (id emitido pelo servidor)."""

    device: DeviceOut
    status: str = "pending"  # liberado p/ autenticar SOMENTE após o pareamento
    pairing_hint: str = "pareie pelo código exibido no Core (POST /api/remote/pairings)"


class DeviceRenameIn(APIModel):
    name: str


class DeviceCapabilitiesIn(APIModel):
    """Atualização de identidade do cliente (Fase 21) — sanitizada pelo serviço."""

    platform: str | None = None
    client_version: str | None = None
    capabilities: list[str] | None = None


class DeviceInfoOut(APIModel):
    """Status completo de um device + sessão/conversa ancorada (Fase 21)."""

    device: DeviceOut
    session: dict[str, Any] | None = None  # RemoteSession sanitizada (sem secrets)
    connected: bool = False


class HeartbeatIn(APIModel):
    """Fase 21 — batida de vida do cliente (token no body, como o /auth)."""

    token: str
    event: str = "heartbeat"  # connect | reconnect | heartbeat | disconnect
    claimed_device_id: str | None = None
    transport_meta: dict[str, Any] | None = None
    platform: str | None = None
    client_version: str | None = None
    capabilities: list[str] | None = None


class HeartbeatOut(APIModel):
    """Resposta da batida: estado derivado + âncora de conversa para offline.

    `conversation_id` permite ao cliente fino reentrar na conversa do device
    (session sharing) após reconnect. Nunca expõe secrets.
    """

    ok: bool = True
    device_id: str
    status: str
    connected: bool
    conversation_id: str | None = None
    heartbeat_seconds: int = 30
    reconnect_enabled: bool = True


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
    # Fase 21 — âncora da conversa do device (session sharing/continuação).
    conversation_id: str | None = None


class RemoteMessageIn(APIModel):
    """Corpo de uma mensagem conversacional remota (Fase 16).

    Usa o MESMO modelo de autenticação do `/remote/auth` (token no corpo,
    transport-meta opcional) para não duplicar contratos. O `request_id` vai no
    header `X-Request-ID` — nunca no corpo — e é ecoado na resposta.

    `session_id` (Fase 21): alvo de session sharing — quando informado, cai na
    conversa JARVIS de um device confiável (continuação entre dispositivos);
    sem ele, usa a sessão estável do próprio device. O contexto é sempre o do
    Core; o cliente fino nunca recebe memória/cópia.
    """

    token: str
    content: str
    stream: bool = False
    tools: bool = True  # tools autorizadas respeitam o Permission Engine existente
    claimed_device_id: str | None = None
    transport_meta: dict[str, Any] | None = None
    session_id: str | None = None


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
    # Fase 23 — Core Link WAN (relé WebSocket). Contadores e estado sanitizados.
    gateway: dict | None = None


class Empty(APIModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# Fase 23 — Core Link WAN (relé WebSocket). Nunca expõe secrets nem payloads.
# ---------------------------------------------------------------------------


class RemoteGatewayConnectIn(APIModel):
    url: str | None = None


class RemoteGatewayOut(APIModel):
    enabled: bool
    configured: bool
    running: bool
    transport: str = "gateway_wan"
    url: str = ""
    connection: str = "disconnected"
    connection_state: str = "disabled"
    healthy: bool = False
    device_id: str | None = None
    connected_at: datetime | None = None
    last_state_change: datetime | None = None
    last_heartbeat: datetime | None = None
    reconnect_count: int = 0
    last_error: str | None = None
    last_error_code: str | None = None
    revocation: str | None = None
    devices_bound: int = 0
    mobile_heartbeats: dict[str, Any] = Field(default_factory=dict)
    latency: dict[str, Any] | None = None
    queued_proactive: int = 0
    counters: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fase 25 — VEGA Mobile Control: comandos de dispositivo (Core → móvel).
# Nunca expõe payloads/args sensíveis; apenas estado correlacional sanitizado.
# ---------------------------------------------------------------------------


class MobileCommandIn(APIModel):
    """Disparo de um comando de dispositivo ao móvel (Core → móvel).

    `device_id` é o alvo (deve estar VINCULADO ao Core Link WAN); `capability`
    é validada contra o registry; `args` seguem o schema da capability no Core
    (fail-fast) e a allowlist/permissões do SO no dispositivo. `command_id`
    idempotência: reuso do mesmo id devolve o estado atual em vez de re-despachar.
    """

    device_id: str
    capability: str
    args: dict[str, Any] | None = None
    timeout_ms: int | None = None
    command_id: str | None = None


class MobileCommandOut(APIModel):
    """Status de um comando de dispositivo (piada/painel; nunca payloads brutos)."""

    command_id: str
    status: str  # pending | success | failed | denied | unsupported | timeout | cancelled
    device_id: str | None = None
    capability: str | None = None
    timeout_ms: int | None = None
    dispatched_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # `result` é o resultado sanitizado do dispositivo (senão None); `error`
    # é a mensagem sanitizada de falha (senão None). Nunca secretos.
    result: dict[str, Any] | None = None
    error: str | None = None


class CommandDispatchOut(APIModel):
    """Resposta do disparo: o comando foi enfileirado ao móvel (PENDING)."""

    accepted: bool = True
    command: MobileCommandOut