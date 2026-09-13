"""API de identidade remota (Fase 12.2).

Todos os endpoints respondem 503 quando `REMOTE_ENABLED=false` (padrão),
mantendo o comportamento atual inalterado. Nenhuma rota expõe segredos: token
bruto aparece apenas na resposta de pairing (emissão única) e em `/remote/auth`
(entrada legítima do transporte). `GET /remote/status` não expõe configuração
sensível.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession
from uuid import uuid4

from app.ai.providers.base import AIProvider
from app.api.deps import get_ai_provider
from app.api.remote_deps import RemoteRequestContext, get_remote_context
from app.core.config import settings
from app.db.session import get_db
from app.remote.auth import RemoteAuthError, authenticate_bearer
from app.remote.credentials import CredentialLimitError, list_credentials, revoke_credential
from app.remote.devices import (
    DeviceInvalid,
    DeviceLimitExceeded,
    heartbeat,
    list_devices,
    load_meta,
    register_device,
    register_device_bridge_events,
    rename_device,
    revoke_device,
    update_capabilities,
)
from app.remote.errors import RemoteError, RemoteErrorCode
from app.remote.jarvis_session import get_or_create_jarvis_session
from app.remote.limits import check_auth_rate
from app.remote.pairing import (
    PairingError,
    PairingInvalid,
    PairingLocked,
    PairingRateLimited,
    create_pairing,
    list_pairings,
    submit_code,
)
from app.remote.sessions import end_session, list_sessions, revoke_session
from app.schemas.remote import (
    AuthIn,
    AuthOut,
    CredentialOut,
    DeviceCapabilitiesIn,
    DeviceInfoOut,
    DeviceOut,
    DeviceRegisterIn,
    DeviceRegisterOut,
    DeviceRenameIn,
    Empty,
    HeartbeatIn,
    HeartbeatOut,
    LanCommandOut,
    LanCommandPollIn,
    LanCommandPollOut,
    LanCommandResultIn,
    LanCommandResultOut,
    MobileCommandIn,
    MobileCommandOut,
    CommandDispatchOut,
    PairingCreateOut,
    PairingOut,
    PairingSubmitIn,
    PairingSubmitOut,
    RemoteMessageIn,
    RemoteStatusOut,
    RemoteGatewayConnectIn,
    RemoteGatewayOut,
)

from app.api.chat import SSE_HEADERS

router = APIRouter(prefix="/api", tags=["remote"])


def _require_remote() -> None:
    if not settings.remote_enabled:
        raise HTTPException(status_code=503, detail="Remote desabilitado (REMOTE_ENABLED=false)")


def _require_devices() -> None:
    """Fase 21 — Device Bridge: exige transporte remoto (mestre) + device_enabled."""
    _require_remote()
    if not settings.device_enabled:
        raise HTTPException(status_code=503, detail="Device Bridge desabilitado (DEVICE_ENABLED=false)")


def _pairing_error_to_status(exc: PairingError) -> HTTPException:
    if isinstance(exc, PairingRateLimited):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, PairingLocked):
        return HTTPException(status_code=423, detail=str(exc))
    if isinstance(exc, (PairingInvalid,)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/remote/status", response_model=RemoteStatusOut)
def remote_status(db: OrmSession = Depends(get_db)) -> RemoteStatusOut:
    enabled = bool(settings.remote_enabled)
    configured = bool(enabled and settings.remote_device_id)
    out: dict = {
        "enabled": enabled,
        "configured": configured,
        "detail": (
            "habilitado e configurado"
            if configured
            else ("habilitado sem device_id" if enabled else "desabilitado")
        ),
    }
    if enabled:
        from app.remote import remote_commands as cmd_service
        from app.remote.runtime import get_remote_status

        snapshot = get_remote_status()
        if snapshot is not None:
            out.update(snapshot)  # observabilidade sanitizada (sem secrets)
        # Fase 12.4 — comandos em andamento (incl. aguardando aprovação) p/ o device.
        if settings.remote_device_id:
            active = cmd_service.list_commands(
                db,
                device_id=settings.remote_device_id,
                active_only=True,
            )
            out["pending_commands"] = len(active)
        # Fase 23 — Core Link WAN (snapshot síncrono/opcional, sem secrets).
        from app.remote.link_runtime import get_remote_link

        link = get_remote_link()
        if link is not None and settings.remote_gateway_enabled:
            snap = link.snapshot()
            out["gateway"] = {
                "enabled": True,
                "configured": True,
                "running": True,
                "transport": "gateway_wan",
                "connection": snap.get("connection"),
                "connection_state": snap.get("connection_state"),
                "healthy": snap.get("healthy"),
                "devices_bound": snap.get("devices_bound", 0),
                "mobile_heartbeats": snap.get("mobile_heartbeats", {}),
                "latency": snap.get("latency"),
                "queued_proactive": snap.get("queued_proactive", 0),
                "last_state_change": snap.get("last_state_change"),
                "last_error_code": snap.get("last_error_code"),
                "reconnect_count": snap.get("reconnect_count", 0),
                "last_error": snap.get("last_error"),
                "revocation": snap.get("revocation"),
                "counters": snap.get("counters", {}),
            }
        return RemoteStatusOut(**out)
    return RemoteStatusOut(**out)


# ---------------------------------------------------------------------------
# Fase 23 — Core Link WAN (relé WebSocket). Estado + conecta/desconecta.
# ---------------------------------------------------------------------------

@router.get("/remote/gateway", response_model=RemoteGatewayOut)
async def remote_gateway_status() -> RemoteGatewayOut:
    """Estado atual do Core Link WAN (nunca expõe secrets/counters brutos)."""
    from app.remote.link_runtime import get_remote_link_status

    return RemoteGatewayOut(**await get_remote_link_status())


@router.post("/remote/gateway/connect", response_model=RemoteGatewayOut)
async def remote_gateway_connect(body: RemoteGatewayConnectIn) -> RemoteGatewayOut:
    """Sobe/reconecta o Core Link WAN. `url` opcional sobrescreve o config
    (persistido best-effort; a identidade e o peer_token NUNCA são tocados)."""
    if not settings.remote_gateway_enabled:
        raise HTTPException(
            status_code=503,
            detail="Core Link WAN desabilitado (REMOTE_GATEWAY_ENABLED=false)",
        )
    from app.remote.link_runtime import (
        get_remote_link_status,
        start_remote_link,
    )

    await start_remote_link(url=body.url)
    return RemoteGatewayOut(**await get_remote_link_status())


@router.post("/remote/gateway/disconnect", response_model=RemoteGatewayOut)
async def remote_gateway_disconnect() -> RemoteGatewayOut:
    """Desconecta o Core Link WAN (não desabilita o config nem apaga prefs)."""
    from app.remote.link_runtime import (
        get_remote_link_status,
        stop_remote_link,
    )

    await stop_remote_link()
    return RemoteGatewayOut(**await get_remote_link_status())


# ---------------------------------------------------------------------------
# Fase 25 — VEGA Mobile Control: comandos de dispositivo (Core → móvel).
# Enviam MOBILE_COMMAND pelo Core Link WAN; resultados correlacionam por
# command_id (idempotente). Nunca expõe payloads/args sensíveis.
# ---------------------------------------------------------------------------


@router.get("/remote/mobile-capabilities", response_model=list[dict])
def remote_mobile_capabilities() -> list[dict]:
    """Lista declarativa das capabilities conhecidas pelo Core (Fase 25)."""
    from app.remote.mobile_capabilities import list_capabilities

    return list_capabilities()


def _command_out_from_summary(summary: dict) -> MobileCommandOut:
    """Converte o resumo interno do CoreLink no schema público (sanitizado)."""
    return MobileCommandOut(**summary)


@router.post("/remote/gateway/command", response_model=CommandDispatchOut)
async def remote_gateway_command(body: MobileCommandIn) -> CommandDispatchOut:
    """Dispara um comando de dispositivo ao móvel (MOBILE_COMMAND via WAN).

    - 503 quando o Core Link WAN está desabilitado (paridade dos demais
      endpoints remotos);
    - 400 quando capability/args são inválidos para o registry (fail-fast no
      Core; o dispositivo ainda aplica allowlist local + permissões do SO);
    - `command_id` é idempotente: reusar o mesmo id devolve o estado atual em
      vez de re-despachar (evita envio duplicado em retry do cliente).
    :raises HTTPException: 503/400 conforme os casos acima.
    """
    if not settings.remote_gateway_enabled:
        raise HTTPException(
            status_code=503,
            detail="Core Link WAN desabilitado (REMOTE_GATEWAY_ENABLED=false)",
        )
    from app.remote.link_runtime import get_remote_link
    from app.remote.link import LinkStateError
    from app.remote.mobile_capabilities import MobileCommandError

    link = get_remote_link()
    if link is None:
        raise HTTPException(status_code=503, detail="Core Link WAN não iniciado")
    command_id = body.command_id or str(uuid4())
    try:
        summary = await link.dispatch_mobile_command(
            command_id=command_id,
            device_id=body.device_id,
            capability=body.capability,
            args=body.args,
            timeout_ms=body.timeout_ms,
        )
    except (MobileCommandError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LinkStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CommandDispatchOut(command=_command_out_from_summary(summary))


@router.get("/remote/gateway/command/{command_id}", response_model=MobileCommandOut)
async def remote_gateway_command_status(command_id: str) -> MobileCommandOut:
    """Consultas o estado de um comando de dispositivo (idempotente/offline-safe)."""
    from app.remote.link_runtime import get_remote_link
    from app.remote.link import LinkStateError

    link = get_remote_link()
    if link is None:
        raise HTTPException(status_code=503, detail="Core Link WAN não iniciado")
    try:
        summary = link.pending_command_summary(command_id)
    except LinkStateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _command_out_from_summary(summary)


@router.post("/remote/pairings", response_model=PairingCreateOut, status_code=201)
def remote_create_pairing(db: OrmSession = Depends(get_db)) -> PairingCreateOut:
    _require_remote()
    try:
        request, code = create_pairing(db)
    except PairingError as exc:
        raise _pairing_error_to_status(exc) from exc
    return PairingCreateOut(
        pairing_id=request.id,
        code=code,
        expires_at=request.expires_at,
        ttl_seconds=settings.remote_pairing_ttl_seconds,
    )


@router.get("/remote/pairings", response_model=list[PairingOut])
def remote_list_pairings(
    active_only: bool = False, db: OrmSession = Depends(get_db)
) -> list:
    _require_remote()
    return list_pairings(db, active_only=active_only)


@router.post(
    "/remote/pairings/validate",
    response_model=PairingSubmitOut,
    status_code=201,
)
def remote_submit_pairing(
    body: PairingSubmitIn, db: OrmSession = Depends(get_db)
) -> PairingSubmitOut:
    _require_remote()
    try:
        device, token = submit_code(
            db,
            code=body.code,
            device_name=body.device_name,
            device_type=body.device_type,
            pairing_id=body.pairing_id,
            metadata=body.metadata,
            pending_device_id=body.pending_device_id,
            platform=body.platform,
            client_version=body.client_version,
            capabilities=body.capabilities,
        )
    except PairingError as exc:
        raise _pairing_error_to_status(exc) from exc
    return PairingSubmitOut(device_id=device.id, token=token, device=DeviceOut(**device_out(device)))


@router.get("/remote/devices", response_model=list[DeviceOut])
def remote_list_devices(
    active_only: bool = False, db: OrmSession = Depends(get_db)
) -> list:
    _require_remote()
    return [DeviceOut(**device_out(d)) for d in list_devices(db, active_only=active_only)]


@router.post("/remote/devices/{device_id}/revoke", response_model=DeviceOut)
def remote_revoke_device(device_id: str, db: OrmSession = Depends(get_db)) -> DeviceOut:
    _require_remote()
    try:
        device = revoke_device(db, device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DeviceOut(**device_out(device))


# ---------------------------------------------------------------------------
# Fase 21 — Device Bridge (identidade/estado de clientes finos). Nenhuma rota
# duplica `/api/mobile/*` ou `/api/desktop/*`: tudo vive em `/api/remote/*`.
# ---------------------------------------------------------------------------

@router.post(
    "/remote/devices/register",
    response_model=DeviceRegisterOut,
    status_code=201,
)
def remote_register_device(
    body: DeviceRegisterIn, db: OrmSession = Depends(get_db)
) -> DeviceRegisterOut:
    """Fase 21 — REGISTER DEVICE: cria device PENDING (sem segredo, id do servidor).

    O device fica VISÍVEL na lista e aguarda o pareamento por código (PAIRING).
    Um device PENDING nunca autentica; é promovido a confiável apenas quando o
    código certo for submetido com `pending_device_id`.
    """
    _require_devices()
    try:
        device = register_device(
            db,
            name=body.name,
            device_type=body.device_type,
            platform=body.platform,
            client_version=body.client_version,
            capabilities=body.capabilities,
            metadata=body.metadata,
        )
    except (DeviceInvalid, DeviceLimitExceeded) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    register_device_bridge_events(device)
    return DeviceRegisterOut(device=DeviceOut(**device_out(device)))


@router.post("/remote/devices/{device_id}/rename", response_model=DeviceOut)
def remote_rename_device(
    device_id: str, body: DeviceRenameIn, db: OrmSession = Depends(get_db)
) -> DeviceOut:
    _require_devices()
    try:
        device = rename_device(db, device_id, body.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DeviceInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DeviceOut(**device_out(device))


@router.post("/remote/devices/{device_id}/capabilities", response_model=DeviceOut)
def remote_update_capabilities(
    device_id: str, body: DeviceCapabilitiesIn, db: OrmSession = Depends(get_db)
) -> DeviceOut:
    """Fase 21 — cliente reporta plataforma/versão/capacidades (sanitizado).

    NUNCA altera permissões, autonomia nem o estado de confiança do device.
    """
    _require_devices()
    try:
        device = update_capabilities(
            db,
            device_id,
            platform=body.platform,
            client_version=body.client_version,
            capabilities=body.capabilities,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DeviceOut(**device_out(device))


@router.get("/remote/devices/{device_id}/info", response_model=DeviceInfoOut)
def remote_device_info(
    device_id: str, db: OrmSession = Depends(get_db)
) -> DeviceInfoOut:
    """Fase 21 — status completo de um device (device + sessão + conexão)."""
    _require_devices()
    from app.models.remote import Device
    from app.remote.sessions import list_sessions

    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device não encontrado: {device_id}")
    open_sessions = list_sessions(db, device_id=device_id, active_only=True)
    first = open_sessions[0] if open_sessions else None
    return DeviceInfoOut(
        device=DeviceOut(**device_out(device)),
        session=(
            {
                "session_id": first.id,
                "status": first.status,
                "created_at": first.created_at,
                "last_seen_at": first.last_seen_at,
            }
            if first
            else None
        ),
        connected=bool(open_sessions),
    )


@router.post("/remote/heartbeat", response_model=HeartbeatOut)
def remote_heartbeat(
    body: HeartbeatIn,
    request: Request,
    db: OrmSession = Depends(get_db),
) -> HeartbeatOut:
    """Fase 21 — CONNECTION LAYER: batida de vida do cliente cio.

    Autentica o device (mesmo contrato Bearer), valida a sessão remota, atualiza
    identidade reportada (se houver) e propaga `device.connected/reconnected/
    disconnected` conforme `body.event`. Devolve a âncora de conversa para o
    cliente fino. Nunca expõe secrets.
    """
    _require_devices()
    origin = request.client.host if request.client else None
    check_auth_rate(origin)
    try:
        authed = authenticate_bearer(
            db,
            body.token,
            transport_meta=body.transport_meta or {"http": True},
            claimed_device_id=body.claimed_device_id,
        )
    except (RemoteAuthError, CredentialLimitError) as exc:
        raise RemoteError(
            RemoteErrorCode.UNAUTHORIZED, "autenticação necessária", detail="auth failed"
        ) from exc

    if body.platform or body.client_version or body.capabilities is not None:
        update_capabilities(
            db,
            authed.device.id,
            platform=body.platform,
            client_version=body.client_version,
            capabilities=body.capabilities,
        )

    from app.remote.sessions import ensure_session_valid

    ensure_session_valid(db, authed.session)

    event = (body.event or "heartbeat").strip().lower() or "heartbeat"
    allowed_events = {"connect", "reconnect", "heartbeat", "disconnect"}
    if event not in allowed_events:
        event = "heartbeat"
    heartbeat(
        db,
        device=authed.device,
        event=event,
        transport_meta=body.transport_meta,
    )
    conversation_id = get_or_create_jarvis_session(db, authed.device)
    return HeartbeatOut(
        device_id=authed.device.id,
        status=authed.device.status,
        connected=event != "disconnect",
        conversation_id=conversation_id,
        heartbeat_seconds=settings.device_heartbeat_seconds,
        reconnect_enabled=bool(settings.device_reconnect_enabled),
    )


@router.get("/remote/credentials", response_model=list[CredentialOut])
def remote_list_credentials(
    device_id: str | None = None, db: OrmSession = Depends(get_db)
) -> list:
    _require_remote()
    return [
        CredentialOut(
            id=c.id,
            device_id=c.device_id,
            active=c.is_active,
            created_at=c.created_at,
            last_used_at=c.last_used_at,
            expires_at=c.expires_at,
            revoked_at=c.revoked_at,
        )
        for c in list_credentials(db, device_id=device_id)
    ]


@router.post("/remote/credentials/{credential_id}/revoke", response_model=CredentialOut)
def remote_revoke_credential(
    credential_id: str, db: OrmSession = Depends(get_db)
) -> CredentialOut:
    _require_remote()
    try:
        credential = revoke_credential(db, credential_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CredentialOut(
        id=credential.id,
        device_id=credential.device_id,
        active=credential.is_active,
        created_at=credential.created_at,
        last_used_at=credential.last_used_at,
        expires_at=credential.expires_at,
        revoked_at=credential.revoked_at,
    )


@router.get("/remote/sessions", response_model=list)
def remote_list_sessions_endpoint(
    device_id: str | None = None,
    active_only: bool = False,
    db: OrmSession = Depends(get_db),
) -> list:
    _require_remote()
    sessions = list_sessions(db, device_id=device_id, active_only=active_only)
    return [
        {
            "id": s.id,
            "device_id": s.device_id,
            "credential_id": s.credential_id,
            "status": s.status,
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            "ended_at": s.ended_at,
            "revoked_at": s.revoked_at,
        }
        for s in sessions
    ]


@router.post("/remote/sessions/{session_id}/end", response_model=Empty)
def remote_end_session(session_id: str, db: OrmSession = Depends(get_db)) -> Empty:
    _require_remote()
    try:
        end_session(db, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Empty(ok=True)


@router.get("/remote/commands/{device_id}/{command_id}", response_model=dict)
def remote_get_command(
    device_id: str, command_id: str, db: OrmSession = Depends(get_db)
) -> dict:
    """Fase 12.4 — consulta o resultado persistido de um comando remoto.

    Fonte de recuperação offline: mesmo que a entrega pela conexão falhe, o
    cliente obtém o resultado EXECUTED/FAILED/DENIED aqui. Nunca expõe secrets
    (apenas o output textual sanitizado e metadados).
    """
    _require_remote()
    from app.remote import remote_commands as cmd_service

    cmd = cmd_service.get_command(db, device_id, command_id)
    if cmd is None:
        raise HTTPException(status_code=404, detail="Comando não encontrado")
    return cmd_service.command_detail(cmd)


@router.post("/remote/auth", response_model=AuthOut)
def remote_authenticate(
    body: AuthIn,
    request: Request,
    db: OrmSession = Depends(get_db),
) -> AuthOut:
    """Autentica um Bearer token (integração futura do transporte).

    Fase 16: aplica rate limiting por origem+global ANTES do lookup (anti
    brute-force). A mensagem de falha é a mesma em todos os casos.
    """
    _require_remote()
    origin = request.client.host if request.client else None
    check_auth_rate(origin)
    try:
        authed = authenticate_bearer(
            db,
            body.token,
            transport_meta=body.transport_meta,
            claimed_device_id=body.claimed_device_id,
        )
    except (RemoteAuthError, CredentialLimitError) as exc:
        raise RemoteError(
            RemoteErrorCode.UNAUTHORIZED, "autenticação negada", detail=f"auth failed"
        ) from exc
    return AuthOut(
        authenticated=True,
        device=DeviceOut(**device_out(authed.device)),
        session_id=authed.session_id,
        credential_id=authed.credential.id,
        conversation_id=get_or_create_jarvis_session(db, authed.device),
    )


@router.post(
    "/remote/sessions/{session_id}/revoke", response_model=Empty
)
def remote_revoke_session(session_id: str, db: OrmSession = Depends(get_db)) -> Empty:
    """Fase 16 — revoga uma sessão remota individual (REVOKED).

    Diferente do `/end` (fechamento normal), revogação invalida a sessão de
    imediato e propaga `session.revoked` ao SSE — nem o device nem as demais
    sessões são afetados (revogação granular, seção 31).
    """
    _require_remote()
    try:
        revoke_session(db, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Empty(ok=True)


@router.post("/remote/message")
async def remote_message(
    body: RemoteMessageIn,
    request: Request,
    ctx: RemoteRequestContext = Depends(get_remote_context),
    db: OrmSession = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
):
    """Fase 16 — mensagem conversacional remota (reutiliza o AI Core).

    Autentica com o mesmo contrato Bearer, valida a sessão remota (TTL/estado),
    aplica limites e então entrega o conteúdo ao `ai_core.handle_message` — o
    MESMO fluxo do chat local (provider agnóstico; tools via Permission Engine).
    Com `stream=true` retorna o SSE existente com `request_id` em cada evento.

    Estrutura da resposta (não-stream): `{"type":"response", "request_id",
    "session_id", "status":"completed", "content": ...}`.
    """
    _require_remote()
    try:
        authed = authenticate_bearer(
            db,
            body.token,
            transport_meta=body.transport_meta or {"http": True},
            claimed_device_id=body.claimed_device_id,
        )
    except (RemoteAuthError, CredentialLimitError) as exc:
        raise RemoteError(
            RemoteErrorCode.UNAUTHORIZED, "autenticação necessária", detail="auth failed"
        ) from exc

    from app.remote.message import RemoteMessageError, handle_remote_message

    try:
        outcome = await handle_remote_message(
            db,
            authed=authed,
            content=body.content,
            stream=body.stream,
            tools=body.tools,
            ctx=ctx,
            requested=provider,
            session_id=body.session_id,
        )
    except RemoteMessageError as exc:
        raise RemoteError(
            RemoteErrorCode.INVALID_REQUEST, str(exc), detail="mensagem inválida"
        ) from exc
    if outcome["kind"] == "stream":
        headers = dict(SSE_HEADERS)
        headers["X-Request-ID"] = ctx.request_id
        return StreamingResponse(
            outcome["generator"], media_type="text/event-stream", headers=headers
        )
    return outcome["body"]


@router.post(
    "/remote/devices/{device_id}/commands/poll", response_model=LanCommandPollOut
)
def remote_device_commands_poll(
    device_id: str,
    body: LanCommandPollIn,
    db: OrmSession = Depends(get_db),
):
    """Fase 26 — POLL de comandos de dispositivo pendentes (canal LAN).

    Autentica com o MESMO Bearer do `/auth`/`/heartbeat` (o `claimed_device_id`
    casa o token com o device). O Core NUNCA confia no `device_id` da URL: o
    resultado vem do device autenticado, e só comandos expedidos para ele são
    devolvidos. Nada de secrets: args já validados no Core.
    """
    _require_remote()
    from app.remote import mobile_inbox

    try:
        authed = authenticate_bearer(
            db,
            body.token,
            transport_meta=body.transport_meta or {"http": True, "lan_poll": True},
            claimed_device_id=body.claimed_device_id,
        )
    except (RemoteAuthError, CredentialLimitError) as exc:
        raise RemoteError(
            RemoteErrorCode.UNAUTHORIZED, "autenticação necessária", detail="auth failed"
        ) from exc

    if authed.device.id != device_id or authed.device.device_type not in (
        "mobile",
        "tablet",
    ):
        raise HTTPException(status_code=404, detail="roteamento de comandos indisponível")

    commands = mobile_inbox.get_mobile_inbox().pending_for(device_id)
    return LanCommandPollOut(
        commands=[
            LanCommandOut(
                command_id=c["command_id"],
                capability=c["capability"],
                args=c.get("args"),
                timeout_ms=c.get("timeout_ms"),
            )
            for c in commands
        ]
    )


@router.post(
    "/remote/devices/{device_id}/commands/result", response_model=LanCommandResultOut
)
def remote_device_commands_result(
    device_id: str,
    body: LanCommandResultIn,
    db: OrmSession = Depends(get_db),
):
    """Fase 26 — POST do resultado de um comando pollado (canal LAN).

    Idempotente por `command_id`/device no inbox. Resultados de comandos que
    nunca foram expedidos a este device são descartados (`dropped=true`) — o
    Core nunca aceita estado alheio.
    """
    _require_remote()
    from app.remote import mobile_inbox
    from app.remote.mobile_capabilities import MOBILE_COMMAND_STATUSES

    try:
        authed = authenticate_bearer(
            db,
            body.token,
            transport_meta=body.transport_meta or {"http": True, "lan_result": True},
            claimed_device_id=body.claimed_device_id,
        )
    except (RemoteAuthError, CredentialLimitError) as exc:
        raise RemoteError(
            RemoteErrorCode.UNAUTHORIZED, "autenticação necessária", detail="auth failed"
        ) from exc

    if authed.device.id != device_id or authed.device.device_type not in (
        "mobile",
        "tablet",
    ):
        raise HTTPException(status_code=404, detail="roteamento de comandos indisponível")

    if body.status not in MOBILE_COMMAND_STATUSES:
        raise RemoteError(
            RemoteErrorCode.INVALID_REQUEST,
            "status de comando inválido",
            detail="status desconhecido",
        )

    summary = mobile_inbox.get_mobile_inbox().submit_result(
        device_id=device_id,
        command_id=body.command_id,
        status=body.status,
        result=body.result,
        error=body.error,
    )
    return LanCommandResultOut(
        ok=summary.get("ok", True),
        dropped=bool(summary.get("dropped")),
        status=summary.get("status"),
    )


def device_out(device) -> dict:
    return {
        "id": device.id,
        "name": device.name,
        "device_type": device.device_type,
        "status": device.status,
        "created_at": device.created_at,
        "last_seen_at": device.last_seen_at,
        "revoked_at": device.revoked_at,
        "metadata": load_meta(device.metadata_json),
        # Fase 21 — identity do Device Bridge (sanitizada; nunca ID/serial de HW).
        "platform": getattr(device, "platform", "web"),
        "client_version": getattr(device, "client_version", None),
        "capabilities": device.capabilities if hasattr(device, "capabilities") else [],
        "conversation_id": getattr(device, "jarvis_session_id", None),
    }


@router.get("/remote/events")
async def remote_events_stream() -> StreamingResponse:
    """Fase 12.5 — SSE de eventos remotos (identidade, conexão, comandos, resultados).

    Ao conectar, reenvia o histórico recente (replay) e então transmite eventos
    ao vivo. Exige `REMOTE_ENABLED=true`. Eventos são sempre sanitizados — nunca
    secrets. Usado pela UI mobile (painel Remote).
    """
    _require_remote()
    from app.remote.events import remote_event_stream
    from app.services.chat import sse_event

    async def _stream():
        async for entry in remote_event_stream():
            yield sse_event(entry)

    return StreamingResponse(_stream(), media_type="text/event-stream")