"""Core Link WAN (Fase 23): conexão outbound do Core ao relé WebSocket.

Quando `REMOTE_GATEWAY_ENABLED=true` (padrão: false), o Core conecta ao relé
(`backend/app/gateway/`) como peer `core` (handshake `hello` com o
`remote_gateway_peer_token` compartilhado). O relé roteia envelopes; TODA a
autoridade permanece aqui no Core:

- `auth`        — verifica o token do móvel (`authenticate_bearer`) e devolve
                  `auth_result`; o relé só atrela o device após esse ok (trust
                  relay);
- `message`     — converte em turno via `handle_remote_message` (MESMO AI Core);
- `computer_task` — Computer Use remoto (Computer Agent do Fase 19);
- `approval_respond` — decide aprovações (mesmo fluxo de `/api/approvals/...`);
- `message_ack` — acknowledges (contadores sanitizados).

Nunca existe um segundo AI Core, Permission Engine, Tool Registry ou Audit.
Nenhum token/secret é logado. Por conexão, handlers são serializados pelo
`RemoteConnectionManager` (o relé também serializa por peer).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator

from app.ai.providers import get_default_provider
from app.api.remote_deps import RemoteRequestContext
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.remote import Credential, Device, RemoteSession
from app.remote.auth import AuthenticatedDevice, RemoteAuthError, authenticate_bearer
from app.remote.connection import ConnectionState, RemoteConnection
from app.remote.errors import RemoteError
from app.remote.jarvis_session import get_or_create_jarvis_session
from app.remote.manager import RemoteConnectionManager
from app.remote.message import MAX_REMOTE_MESSAGE_LENGTH, RemoteMessageError, handle_remote_message
from app.remote.protocol import MessageType, RemoteEnvelope, build_message
from app.remote.sessions import ensure_session_valid
from app.remote.status import RemoteHealthState

logger = logging.getLogger("jarvis.remote.link")

# Contadores sanitizados do core link (nunca secrets/payloads).
_LINK_STATS_KEYS = (
    "messages_total",
    "messages_failed",
    "auth_requests",
    "auth_failures",
    "message_acks",
    "tasks",
    "tasks_failed",
    "approvals_decided",
    "approvals_failed",
    "broker_events_forwarded",
    # Fase 24 — heartbeat WAN, bootstrap de pairing e proativas.
    "heartbeats",
    "pairings",
    "proactive_delivered",
    "proactive_queued",
    "proactive_expired",
    "rate_limited",
)

_BROKER_EVENT_PREFIXES = ("remote.", "device.", "session.", "proactive.")

# Fase 24 — fila off-line de proativas: bounded por device (nunca infinita).
_MAX_PROACTIVE_QUEUE = 50
_MAX_KNOWN_MOBILES = 50
_PROACTIVE_CONTENT_LIMIT = 1000
_MAX_RTT_HISTORY = 10
_MAX_LATENCY_HISTORY = 10


class WanLinkState(str, Enum):
    """Estados observáveis do Core Link WAN (Fase 24).

    Vocabulário mais rico que o do transporte (`ConnectionState`): descreve o
    estado REAL do link (o relé só transporta), nunca finge conexão e distingue
    os modos de falha. `AUTH_FAILED` (peer_token rejeitado pelo relé) e `REVOKED`
    são terminais nesta sessão do processo; `DEGRADED` reflete conexão ativa com
    erros/móveis em trânsito.
    """

    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    AUTH_FAILED = "auth_failed"
    REVOKED = "revoked"


class LinkStateError(ValueError):
    """Estado do link não permite a operação (erro já sanitizado)."""


class CoreLink:
    """Conexão WAN do Core com o relé (supervisionada, com reconexão/backoff)."""

    def __init__(
        self,
        *,
        device_id: str,
        gateway_url: str,
        peer_token: str,
        connection_factory,
        heartbeat_interval: float = 30.0,
        connect_timeout: float = 10.0,
        min_reconnect_delay: float = 0.5,
        max_reconnect_delay: float = 60.0,
        jitter: float = 0.1,
        reconnect_enabled: bool | None = None,
        message_timeout: float = 60.0,
        queue_ttl: float = 300.0,
    ) -> None:
        self.device_id = device_id
        self._gateway_url = gateway_url
        self._peer_token = peer_token  # nunca logado
        self._connection_factory = connection_factory
        self._heartbeat_interval = heartbeat_interval
        self._connect_timeout = connect_timeout
        self._min_reconnect_delay = min_reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._jitter = jitter
        # Fase 24 — política de reconexão e fila de proativas (defaults seguros).
        self._reconnect_enabled = (
            bool(settings.remote_gateway_reconnect_enabled)
            if reconnect_enabled is None
            else bool(reconnect_enabled)
        )
        self._message_timeout = message_timeout
        self._queue_ttl = queue_ttl

        self._running = False
        self._terminal_reason: str | None = None
        self._supervisor: asyncio.Task | None = None
        self._manager: RemoteConnectionManager | None = None
        self._broker_task: asyncio.Task | None = None
        self._handshake_ok = asyncio.Event()
        self._connected = False
        self._connected_at: datetime | None = None
        self._last_heartbeat: datetime | None = None
        self._last_error: str | None = None
        self._last_error_code: str | None = None
        self._reconnect_count = 0
        self._bindings: dict[str, dict[str, str]] = {}
        self.stats: dict[str, int] = {k: 0 for k in _LINK_STATS_KEYS}

        # Fase 24 — estado observável rico + métricas sanitizadas de latência.
        self._state = WanLinkState.DISCONNECTED
        self._state_changed_at = _utcnow()
        self._heartbeat_sent_at: float | None = None
        self._rtt_history: list[float] = []
        self._latency_history: list[float] = []
        self._last_mobile_heartbeat: dict[str, datetime] = {}
        self._proactive_queue: dict[str, deque] = {}
        self._known_mobiles: dict[str, datetime] = {}

    # -- observabilidade ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "transport": "gateway_wan",
            "connection_state": self._state.value,
            "connection": self._state.value,
            "authenticated": self._connected,
            "healthy": self._connected,
            "device_id": self.device_id,
            "connected_at": self._connected_at,
            "last_heartbeat": self._last_heartbeat,
            "last_state_change": self._state_changed_at,
            "reconnect_count": self._reconnect_count,
            "last_error": self._last_error,
            "last_error_code": self._last_error_code,
            "revocation": self._terminal_reason,
            "devices_bound": len(self._bindings),
            "mobile_heartbeats": {
                device_id: ts.isoformat()
                for device_id, ts in self._last_mobile_heartbeat.items()
            },
            "latency": {
                "relay_rtt_ms": self._avg(self._rtt_history),
                "message_latency_ms": self._avg(self._latency_history),
            },
            "queued_proactive": sum(len(q) for q in self._proactive_queue.values()),
            "counters": dict(self.stats),
        }

    def _avg(self, values: list[float]) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), 1)

    def _record_latency(self, started: float) -> None:
        latency = (time.monotonic() - started) * 1000.0
        self._latency_history.append(latency)
        if len(self._latency_history) > _MAX_LATENCY_HISTORY:
            self._latency_history.pop(0)

    # -- estado observável + eventos sanitizados -----------------------------

    def _set_state(self, state: WanLinkState, *, reason: str | None = None) -> None:
        if state == self._state:
            return
        previous = self._state
        self._state = state
        self._state_changed_at = _utcnow()
        self._publish_gateway_event(
            f"remote.gateway.{state.value}",
            {"from": previous.value, "reason": reason},
        )

    def _publish_gateway_event(
        self, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        from app.remote.events import publish_event

        try:
            publish_event(event_type, data)
        except Exception:  # noqa: BLE001 — eventos nunca derrubam o link
            pass

    # -- ciclo de vida --------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._terminal_reason = None
        self._set_state(WanLinkState.CONNECTING, reason="start")
        self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        task = self._supervisor
        self._supervisor = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._teardown_manager()
        self._connected = False
        self._set_state(WanLinkState.DISCONNECTED, reason="stop")

    async def _teardown_manager(self) -> None:
        broker = self._broker_task
        self._broker_task = None
        if broker is not None and not broker.done():
            broker.cancel()
            try:
                await broker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        manager = self._manager
        self._manager = None
        if manager is not None:
            try:
                await manager.stop()
            except Exception:  # noqa: BLE001
                pass

    async def reconnect(self) -> None:
        """Reinicia a conexão imediatamente (UI). NUNCA altera identidade."""
        running = self._running
        if not running:
            return
        await self._teardown_manager()
        self._connected = False
        self._set_state(WanLinkState.CONNECTING, reason="reconnect")

    # -- supervisor (reconnect com backoff) ----------------------------------

    async def _supervise(self) -> None:
        delay = self._min_reconnect_delay
        while self._running:
            outcome = await self._connect_once()
            if outcome == "terminal":
                self._connected = False
                if self._terminal_reason == "peer_token_rejeitado":
                    self._set_state(WanLinkState.AUTH_FAILED, reason=self._terminal_reason)
                elif self._terminal_reason:
                    self._set_state(WanLinkState.REVOKED, reason=self._terminal_reason)
                else:
                    self._set_state(WanLinkState.DISCONNECTED, reason="terminal")
                logger.error("Core link terminal (%s)", self._terminal_reason)
                break
            if not self._running:
                break
            self._connected = False
            self._last_error = "desconectado do relé"
            if not self._reconnect_enabled:
                self._set_state(WanLinkState.DISCONNECTED, reason="reconnect_disabled")
                break
            delay = self._backoff(delay)
            self._set_state(
                WanLinkState.RECONNECTING,
                reason=f"backoff {delay:.1f}s",
            )
            self._publish_gateway_event(
                "remote.gateway.reconnecting",
                {"delay_seconds": round(delay, 2), "attempt": self._reconnect_count + 1},
            )
            if not await self._wait(delay):
                break

    async def _connect_once(self) -> str:
        """Conecta e processa até desconectar. Retorna "ok"/"error"/"terminal"."""
        self._set_state(WanLinkState.CONNECTING, reason="connect_once")
        try:
            manager = self._build_manager()
        except Exception as exc:  # noqa: BLE001
            self._last_error = _brief(exc)
            return "error"
        self._manager = manager
        try:
            await manager.start()
        except Exception as exc:  # noqa: BLE001
            self._last_error = _brief(exc)
            await self._teardown_manager()
            return "error"

        hello = build_message(
            MessageType.HELLO,
            device_id=self.device_id,
            payload={
                "role": "core",
                "peer_token": self._peer_token,
                "device_id": self.device_id,
                "protocol_version": 1,
            },
        )
        try:
            await manager.send(hello)
            await asyncio.wait_for(self._handshake_ok.wait(), timeout=self._connect_timeout)
        except asyncio.TimeoutError:
            self._last_error = "handshake sem resposta do relé"
            await self._teardown_manager()
            return "error"
        except Exception as exc:  # noqa: BLE001
            self._last_error = _brief(exc)
            await self._teardown_manager()
            return "error"
        finally:
            self._handshake_ok.clear()

        if self._terminal_reason is not None:
            await self._teardown_manager()
            return "terminal"

        self._connected = True
        self._connected_at = _utcnow()
        self._last_error = None
        self._last_error_code = None
        self._reconnect_count += 1
        self._set_state(WanLinkState.CONNECTED, reason="handshake_ok")
        self._publish_gateway_event(
            "remote.gateway.connected",
            {"device_id": self.device_id, "reconnect_count": self._reconnect_count},
        )
        logger.info("Core link conectado ao relé (device=%s)", self.device_id)
        self._broker_task = asyncio.create_task(self._broker_forward())
        try:
            await self._monitor_disconnect()
        finally:
            self._connected = False
            self._set_state(WanLinkState.DISCONNECTED, reason="link_drop")
            self._publish_gateway_event("remote.gateway.disconnected", {})
            await self._teardown_manager()
        return "ok"

    def _build_manager(self) -> RemoteConnectionManager:
        connection = self._connection_factory()
        manager = RemoteConnectionManager(
            connection,
            device_id=self.device_id,
            heartbeat_interval=self._heartbeat_interval,
            heartbeat_sent_hook=self._on_heartbeat_sent,
        )
        manager.register(MessageType.HELLO_ACK, self._on_hello_ack)
        manager.register(MessageType.HEARTBEAT_ACK, self._on_heartbeat_ack)
        manager.register(MessageType.ERROR, self._on_error)
        manager.register(MessageType.AUTH, self._on_auth)
        manager.register(MessageType.MESSAGE, self._on_message)
        manager.register(MessageType.MESSAGE_ACK, self._on_message_ack)
        manager.register(MessageType.HEARTBEAT, self._on_heartbeat)
        manager.register(MessageType.COMPUTER_TASK, self._on_computer_task)
        manager.register(MessageType.APPROVAL_RESPOND, self._on_approval_respond)
        manager.register(MessageType.CLOSE, self._on_close)
        return manager

    async def _monitor_disconnect(self) -> None:
        while self._running:
            manager = self._manager
            if manager is None:
                return
            if manager._receive_task is None or manager._receive_task.done():  # noqa: SLF001
                return
            await asyncio.sleep(0.5)

    async def _wait(self, delay: float) -> bool:
        try:
            await asyncio.wait_for(self._still_running(), timeout=delay)
            return False
        except asyncio.TimeoutError:
            return True

    async def _still_running(self) -> None:
        while self._running:
            await asyncio.sleep(0.05)

    def _backoff(self, current: float) -> float:
        nxt = min(current * 2.0, self._max_reconnect_delay)
        jittered = nxt * (1.0 - self._jitter) + random.random() * (2.0 * self._jitter * nxt)
        return max(0.0, min(jittered, self._max_reconnect_delay))

    # -- ring dos handlers (papel do relé é apenas transportar) --------------

    async def _on_hello_ack(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        payload = envelope.payload or {}
        if payload.get("ok") and payload.get("role") == "core":
            self._handshake_ok.set()
        else:
            self._terminal_reason = "handshake_rejeitado"
            self._handshake_ok.set()
        return None

    def _on_heartbeat_sent(self) -> None:
        """Marca o instante do último heartbeat enviado (medição de RTT WAN)."""
        self._heartbeat_sent_at = time.monotonic()

    async def _on_heartbeat_ack(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        self._last_heartbeat = _utcnow()
        rtt = 0.0
        if self._heartbeat_sent_at is not None:
            rtt = (time.monotonic() - self._heartbeat_sent_at) * 1000.0
            self._rtt_history.append(rtt)
            if len(self._rtt_history) > _MAX_RTT_HISTORY:
                self._rtt_history.pop(0)
            self._heartbeat_sent_at = None
        self._publish_gateway_event(
            "remote.gateway.heartbeat",
            {"relay_rtt_ms": round(rtt, 1)},
        )
        return None

    # -- heartbeat WAN de móveis (atrelados) ---------------------------------

    async def _on_heartbeat(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        """Renova a sessão do device no WAN e devolve o ACK roteável.

        Correção Fase 24: o heartbeat de um móvel atrelado NUNCA mais cai em
        `unsupported_type` — aqui a sessão remota é validada (renova o TTL) e o
        ACK é respondido com `target_device_id`, roteável pelo relé ao móvel.
        """
        payload = envelope.payload or {}
        device_id = envelope.device_id
        if not device_id:
            return None
        bound = self._bound(device_id)
        if bound is None:
            return None  # relé só roteia heartbeat de atrelados; sem ACK falso
        try:
            with SessionLocal() as db:
                device, credential, session = self._load_bound(db, bound)
                from app.remote.devices import heartbeat as device_heartbeat

                device_heartbeat(
                    db,
                    device,
                    event="heartbeat",
                    transport_meta={"transport": "wss", "gateway_relay": True},
                )
        except (LinkStateError, RemoteError, Exception) as exc:  # noqa: BLE001
            logger.debug("Heartbeat WAN recusado (device=%s): %s", device_id, _brief(exc))
            return None
        self.stats["heartbeats"] += 1
        self._last_mobile_heartbeat[device_id] = _utcnow()
        return build_message(
            MessageType.HEARTBEAT_ACK,
            device_id=self.device_id,
            request_id=envelope.request_id,
            payload={
                "ok": True,
                "target_device_id": device_id,
                "echo_sent_at": payload.get("sent_at"),
                "heartbeat_seconds": int(settings.device_heartbeat_seconds),
            },
        )

    async def _on_error(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        payload = envelope.payload or {}
        code = payload.get("error") or "error"
        self._last_error_code = code
        if code in ("core_denied",):
            self._terminal_reason = "peer_token_rejeitado"
            self._last_error = "handshake rejeitado (peer_token inválido)"
            self._handshake_ok.set()
            self._set_state(WanLinkState.AUTH_FAILED, reason="peer_token_rejeitado")
            logger.error("Relé rejeitou o core link: %s", code)
        return None

    async def _on_close(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        who = envelope.device_id or (envelope.payload or {}).get("source_device_id")
        if who and who in self._bindings:
            self._bindings.pop(who, None)
            self._publish_gateway_event(
                "remote.gateway.device_disconnected", {"device_id": who}
            )
        return None

    async def _on_message_ack(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        self.stats["message_acks"] += 1
        return None

    # -- auth (trust relay) ---------------------------------------------------

    async def _on_auth(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        payload = envelope.payload or {}
        self.stats["auth_requests"] += 1
        device_id = envelope.device_id  # rotulado pelo relé (não confiável p/ identidade)
        if payload.get("pairing_code"):
            return await self._on_pairing_auth(envelope, device_id, payload)
        token = payload.get("token")
        try:
            from app.remote.limits import check_auth_rate

            check_auth_rate(None)
        except RemoteError:
            self.stats["rate_limited"] += 1
            return self._auth_failed(envelope, device_id)
        if not isinstance(token, str) or not token:
            return self._auth_failed(envelope, device_id)

        transport_meta = dict(payload.get("transport_meta") or {})
        transport_meta["transport"] = "wss"
        transport_meta["gateway_relay"] = True
        try:
            with SessionLocal() as db:
                authed = authenticate_bearer(
                    db,
                    token,
                    transport_meta=transport_meta,
                    claimed_device_id=payload.get("claimed_device_id"),
                )
                if payload.get("platform") or payload.get("client_version") or payload.get("capabilities") is not None:
                    from app.remote.devices import update_capabilities

                    update_capabilities(
                        db,
                        authed.device.id,
                        platform=payload.get("platform"),
                        client_version=payload.get("client_version"),
                        capabilities=payload.get("capabilities"),
                    )
                conversation_id = get_or_create_jarvis_session(db, authed.device)
                self._bindings[authed.device_id] = {
                    "device_id": authed.device_id,
                    "credential_id": authed.credential.id,
                    "session_id": authed.session_id,
                }
                self._remember_mobile(authed.device_id)
        except (RemoteAuthError, Exception) as exc:  # noqa: BLE001
            if not isinstance(exc, RemoteAuthError):
                logger.error("Falha interna em auth do link: %s", _brief(exc))
            return self._auth_failed(envelope, device_id)

        self._publish_gateway_event(
            "remote.gateway.device_connected", {"device_id": authed.device_id}
        )
        await self._flush_proactive(authed.device_id)
        return build_message(
            MessageType.AUTH_RESULT,
            device_id=self.device_id,
            request_id=envelope.request_id,
            payload={
                "ok": True,
                "target_device_id": authed.device_id,
                "device_id": authed.device_id,
                "session_id": authed.session_id,
                "conversation_id": conversation_id,
                "heartbeat_seconds": int(settings.device_heartbeat_seconds),
                "reconnect_enabled": bool(self._reconnect_enabled),
                "message_timeout": self._message_timeout,
                "queue_ttl": self._queue_ttl,
            },
        )

    async def _on_pairing_auth(
        self, envelope: RemoteEnvelope, device_id: str | None, payload: dict[str, Any]
    ) -> RemoteEnvelope | None:
        """Bootstrap WAN (Fase 24): móvel fora da LAN pareia pelo código.

        Reusa `submit_code` (cria device confiável + credencial) e a seguir o
        MESMO `authenticate_bearer` do token recebido — o WAN nunca cria device
        por conta própria. O token bruto é devolvido UMA ÚNICA vez no
        `auth_result`; nunca é logado nem entra em eventos.
        """
        from app.remote.pairing import PairingError, submit_code

        try:
            with SessionLocal() as db:
                device, token = submit_code(
                    db,
                    code=str(payload.get("pairing_code") or "").strip(),
                    device_name=str(payload.get("device_name") or "Mobile WAN").strip()[:120],
                    device_type=str(payload.get("device_type") or "mobile"),
                    platform=payload.get("platform"),
                    client_version=payload.get("client_version"),
                    capabilities=payload.get("capabilities"),
                    metadata={"transport": "wss", "gateway_relay": True},
                )
                authed = authenticate_bearer(
                    db,
                    token,
                    transport_meta={"transport": "wss", "gateway_relay": True},
                )
                conversation_id = get_or_create_jarvis_session(db, authed.device)
        except (PairingError, RemoteAuthError) as exc:
            logger.info("Pairing WAN negado (device=%s): %s", device_id, str(exc)[:80])
            return self._auth_failed(envelope, device_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Falha interna em pairing do link: %s", _brief(exc))
            return self._auth_failed(envelope, device_id)

        self.stats["pairings"] += 1
        self._bindings[authed.device.id] = {
            "device_id": authed.device.id,
            "credential_id": authed.credential.id,
            "session_id": authed.session.id,
        }
        self._remember_mobile(authed.device.id)
        self._publish_gateway_event(
            "remote.gateway.device_connected", {"device_id": authed.device.id}
        )
        await self._flush_proactive(authed.device.id)
        return build_message(
            MessageType.AUTH_RESULT,
            device_id=self.device_id,
            request_id=envelope.request_id,
            payload={
                "ok": True,
                "target_device_id": authed.device.id,
                "device_id": authed.device.id,
                "session_id": authed.session.id,
                "conversation_id": conversation_id,
                "token": token,  # ÚNICA emissão do token pelo WAN (Single-Fix)
                "heartbeat_seconds": int(settings.device_heartbeat_seconds),
                "reconnect_enabled": bool(self._reconnect_enabled),
                "message_timeout": self._message_timeout,
                "queue_ttl": self._queue_ttl,
            },
        )

    def _auth_failed(self, envelope: RemoteEnvelope, device_id: str | None) -> RemoteEnvelope:
        self.stats["auth_failures"] += 1
        self._publish_gateway_event("remote.gateway.auth_failed", {"device_id": device_id})
        return build_message(
            MessageType.AUTH_RESULT,
            device_id=self.device_id,
            request_id=envelope.request_id,
            payload={
                "ok": False,
                "target_device_id": device_id,
                "reason": "autenticação negada",
            },
        )

    # -- mensagens (conversação WAN — reusa o AI Core) ------------------------

    async def _on_message(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        payload = envelope.payload or {}
        device_id = envelope.device_id
        request_id = envelope.request_id or envelope.message_id
        self.stats["messages_total"] += 1
        started = time.monotonic()
        bound = self._bound(device_id)
        if bound is None:
            return self._message_reject(envelope, device_id, "unauthorized", "device não autenticado")

        content = payload.get("content") or ""
        stream = bool(payload.get("stream", False))
        tools = bool(payload.get("tools", True))
        session_id = payload.get("session_id")

        try:
            with SessionLocal() as db:
                device, credential, session = self._load_bound(db, bound)
                authed = AuthenticatedDevice(device=device, credential=credential, session=session)
                ctx = RemoteRequestContext(
                    request_id=request_id,
                    session_id=session.id,
                    device_id=device.id,
                    credential_id=credential.id,
                )
                # MESMO provider que a API `/api/remote/message` (AI Router) —
                # o WAN não escolhe nem contorna o router.
                provider = get_default_provider()
                outcome = await handle_remote_message(
                    db,
                    authed=authed,
                    content=content,
                    stream=stream,
                    tools=tools,
                    ctx=ctx,
                    requested=provider,
                    session_id=session_id,
                )
        except RemoteMessageError as exc:
            return self._message_reject(envelope, device_id, "invalid_message", str(exc))
        except RemoteError as exc:
            return self._message_reject(envelope, device_id, "remote_error", str(exc.detail or exc.message))
        except Exception as exc:  # noqa: BLE001
            if bound_device_context(device_id):
                self.stats["messages_failed"] += 1
            logger.error("Falha no turno remoto (device=%s): %s", device_id, _brief(exc))
            return self._message_reject(envelope, device_id, "internal_error", "erro interno do Core")

        if outcome["kind"] == "reply":
            self._record_latency(started)
            body = dict(outcome["body"])
            body["target_device_id"] = device_id
            body["ok"] = True
            return build_message(
                MessageType.MESSAGE_RESULT,
                device_id=self.device_id,
                request_id=request_id,
                payload=body,
            )

        ok, error = await self._pump_sse(
            outcome["generator"], device_id, request_id, kind="chat"
        )
        self._record_latency(started)
        if not ok:
            self.stats["messages_failed"] += 1
            return build_message(
                MessageType.MESSAGE_RESULT,
                device_id=self.device_id,
                request_id=request_id,
                payload={
                    "ok": False,
                    "status": "failed",
                    "request_id": request_id,
                    "target_device_id": device_id,
                    "error": error or "erro interno",
                },
            )
        return build_message(
            MessageType.MESSAGE_RESULT,
            device_id=self.device_id,
            request_id=request_id,
            payload={
                "ok": True,
                "status": "completed",
                "request_id": request_id,
                "target_device_id": device_id,
                "session_id": outcome.get("jarvis_session_id"),
            },
        )

    # -- Computer Use remoto (reusa o Computer Agent) -------------------------

    async def _on_computer_task(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        payload = envelope.payload or {}
        device_id = envelope.device_id
        request_id = envelope.request_id or envelope.message_id
        self.stats["tasks"] += 1
        bound = self._bound(device_id)
        if bound is None:
            return self._task_result(envelope, device_id, request_id, False, "device não autenticado")
        # Fase 24 — o caminho WAN aplica a MESMA taxa de mensagens do HTTP
        # (computer/approval não detêm slot de conversação: são streams longos).
        try:
            from app.remote.limits import check_message_rate

            check_message_rate(device_id)
        except RemoteError:
            self.stats["rate_limited"] += 1
            return self._task_result(envelope, device_id, request_id, False, "limite de taxa excedido")
        if not settings.computer_agent_enabled:
            return self._task_result(
                envelope, device_id, request_id, False, "Computer Agent desabilitado no Core"
            )
        content = str(payload.get("content") or "").strip()
        if not content:
            return self._task_result(envelope, device_id, request_id, False, "tarefa vazia")

        from app.computer_agent import service as computer_service
        from app.computer_agent.agent import ComputerAgent

        provider = get_default_provider()
        try:
            with SessionLocal() as db:
                device, credential, session = self._load_bound(db, bound)
                ensure_session_valid(db, session)
                jarvis_session_id = get_or_create_jarvis_session(db, device)
                agent = ComputerAgent()
                task = computer_service.start_task_record(
                    goal=content[:1000],
                    session_id=jarvis_session_id,
                    requested_by=device_id,
                    device_id=device_id,
                    autonomy=payload.get("autonomy"),
                )
                stream = computer_service.run_task_stream(db, provider, task, agent=agent)
                await self._pump_sse(stream, device_id, request_id, kind="computer")
                final = computer_service.get_task(task.task_id) or task
                status = getattr(final, "status", None)
                state = status.value if status is not None else "unknown"
                ok = state in ("completed",)
        except (LinkStateError, RemoteError) as exc:
            self.stats["tasks_failed"] += 1
            return self._task_result(envelope, device_id, request_id, False, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.stats["tasks_failed"] += 1
            logger.error("Falha na tarefa remota de Computer Use (device=%s): %s", device_id, _brief(exc))
            return self._task_result(envelope, device_id, request_id, False, "erro interno do Core")

        return self._task_result(
            envelope, device_id, request_id, ok, None, state=state, task_id=task.task_id
        )

    def _task_result(
        self,
        envelope: RemoteEnvelope,
        device_id: str,
        request_id: str,
        ok: bool,
        error: str | None,
        *,
        state: str | None = None,
        task_id: str | None = None,
    ) -> RemoteEnvelope:
        payload: dict[str, Any] = {
            "ok": ok,
            "target_device_id": device_id,
            "request_id": request_id,
        }
        if error:
            payload["error"] = error
        if state:
            payload["status"] = state
        if task_id:
            payload["task_id"] = task_id
        return build_message(
            MessageType.COMPUTER_RESULT,
            device_id=self.device_id,
            request_id=request_id,
            payload=payload,
        )

    # -- aprovações WAN (mesmo fluxo de /api/approvals/{id}/respond) ----------

    async def _on_approval_respond(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        payload = envelope.payload or {}
        device_id = envelope.device_id
        request_id = envelope.request_id or envelope.message_id
        approval_id = payload.get("approval_id")
        approved = bool(payload.get("approved"))
        bound = self._bound(device_id)
        if bound is None or not isinstance(approval_id, str) or not approval_id:
            self.stats["approvals_failed"] += 1
            return self._approval_result(envelope, device_id, request_id, False, "requisição inválida")
        # Fase 24 — aprovação também respeita a taxa de mensagens WAN (como HTTP).
        try:
            from app.remote.limits import check_message_rate

            check_message_rate(device_id)
        except RemoteError:
            self.stats["rate_limited"] += 1
            return self._approval_result(
                envelope, device_id, request_id, False, "limite de taxa excedido"
            )

        from app.core.enums import ApprovalStatus
        from app.services import approvals as approval_service

        provider = get_default_provider()
        try:
            with SessionLocal() as db:
                device, credential, session = self._load_bound(db, bound)
                ensure_session_valid(db, session)
                approval = approval_service.get_approval(db, approval_id)
                if approval is None:
                    raise LinkStateError("pedido de aprovação não encontrado")
                if approval.status != ApprovalStatus.PENDING.value:
                    raise LinkStateError("pedido de aprovação já decidido")
                if approval_service.is_expired(approval):
                    raise LinkStateError("pedido de aprovação expirou")

                decision, decided = approval_service.mark_decided(db, approval, approved=approved)
                status_word = ApprovalStatus.APPROVED.value if decision.status == ApprovalStatus.APPROVED.value else "denied"
                self.stats["approvals_decided"] += 1

                # Comando remoto (Fase 12.4) — retomada transacional do comando.
                from app.remote import resume as resume_service
                from app.remote.jarvis_session import get_or_create_jarvis_session
                from app.remote.runtime import deliver_command_result

                command = resume_service.resolve_command_for_approval(db, decision.id)
                if command is not None:
                    if command.device_id != device_id:
                        raise LinkStateError("pedido de aprovação não pertence a este device")
                    jarvis_session_id = get_or_create_jarvis_session(db, device)
                    result = await resume_service.resume_remote_command(
                        db,
                        command=command,
                        device=device,
                        jarvis_session_id=jarvis_session_id,
                        approval_status=decision.status,
                    )
                    approval_service.mark_applied(db, decision)
                    deliver_command_result(device.id, command.command_id, result, db=db)
                    return self._approval_result(
                        envelope, device_id, request_id, True, None,
                        decision="approved" if decided else status_word,
                        details={"command_id": command.command_id, "result": result},
                    )

                # Agent Core pausado (Fase 13)
                from app.services import agent_core as agent_core_service

                task = agent_core_service.get_task_by_approval(db, decision.id)
                if task is not None:
                    generator = agent_core_service.resume_agent_task(db, task, provider, decision)
                    await self._pump_sse(generator, device_id, request_id, kind="approval")
                    return self._approval_result(
                        envelope, device_id, request_id, True, None, decision=status_word
                    )

                # Computer Agent pausado (Fase 19)
                from app.computer_agent import service as computer_service
                from app.computer_agent.agent import ComputerAgent
                from app.computer_agent.store import get_store as computer_store

                computer_task = computer_store().get_by_approval(decision.id)
                if computer_task is not None:
                    agent_ca = ComputerAgent()
                    generator = computer_service.run_task_stream(
                        db, provider, computer_task, agent=agent_ca
                    )
                    await self._pump_sse(generator, device_id, request_id, kind="computer")
                    return self._approval_result(
                        envelope, device_id, request_id, True, None, decision=status_word
                    )

                # Agente local padrão
                from app.services import agent as agent_service

                generator = agent_service.run_agent(decision.session_id, provider, db, user_text="")
                await self._pump_sse(generator, device_id, request_id, kind="approval")
                return self._approval_result(
                    envelope, device_id, request_id, True, None, decision=status_word
                )
        except LinkStateError as exc:
            self.stats["approvals_failed"] += 1
            return self._approval_result(envelope, device_id, request_id, False, str(exc))
        except RemoteError as exc:
            self.stats["approvals_failed"] += 1
            return self._approval_result(envelope, device_id, request_id, False, str(exc.detail or exc.message))
        except Exception as exc:  # noqa: BLE001
            self.stats["approvals_failed"] += 1
            logger.error("Falha na aprovação remota (device=%s): %s", device_id, _brief(exc))
            return self._approval_result(envelope, device_id, request_id, False, "erro interno do Core")

    def _approval_result(
        self,
        envelope: RemoteEnvelope,
        device_id: str,
        request_id: str,
        ok: bool,
        error: str | None,
        *,
        decision: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> RemoteEnvelope:
        payload: dict[str, Any] = {
            "ok": ok,
            "target_device_id": device_id,
            "request_id": request_id,
        }
        if error:
            payload["error"] = error
        if decision:
            payload["decision"] = decision
        if details:
            payload.update(details)
        return build_message(
            MessageType.APPROVAL_RESULT,
            device_id=self.device_id,
            request_id=request_id,
            payload=payload,
        )

    # -- helpers ---------------------------------------------------------------

    def _bound(self, device_id: str | None) -> dict[str, str] | None:
        if not device_id:
            return None
        return self._bindings.get(device_id)

    def _load_bound(self, db, bound: dict[str, str]):
        device = db.get(Device, bound["device_id"])
        credential = db.get(Credential, bound["credential_id"])
        session = db.get(RemoteSession, bound["session_id"])
        if (
            session is None
            or credential is None
            or device is None
            or not credential.is_active
            or device.status != "active"
        ):
            raise LinkStateError("device não autorizado")
        ensure_session_valid(db, session)
        return device, credential, session

    def _message_reject(
        self, envelope: RemoteEnvelope, device_id: str, code: str, message: str
    ) -> RemoteEnvelope:
        self.stats["messages_failed"] += 1
        return build_message(
            MessageType.MESSAGE_RESULT,
            device_id=self.device_id,
            request_id=envelope.request_id or envelope.message_id,
            payload={
                "ok": False,
                "status": "failed",
                "error": message,
                "code": code,
                "target_device_id": device_id,
            },
        )

    async def _send(self, envelope: RemoteEnvelope) -> None:
        manager = self._manager
        if manager is None:
            return
        await manager.send(envelope)

    async def _pump_sse(
        self,
        generator: AsyncIterator[str],
        device_id: str,
        request_id: str,
        *,
        kind: str,
    ) -> tuple[bool, str | None]:
        """Consome um gerador SSE do Core e o reemite como `agent_event`s."""
        try:
            async for item in generator:
                event = _parse_sse_item(item)
                await self._send_agent_event(device_id, request_id, kind=kind, event=event)
            return True, None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — falha sanitizada no fluxo
            return False, _brief(exc)

    async def _send_agent_event(
        self, device_id: str, request_id: str, *, kind: str, event: dict[str, Any] | str
    ) -> None:
        payload: dict[str, Any] = {
            "target_device_id": device_id,
            "request_id": request_id,
            "stream_kind": kind,
        }
        if isinstance(event, dict):
            payload["event"] = event
        else:
            payload["raw"] = str(event)
        await self._send(
            build_message(
                MessageType.AGENT_EVENT,
                device_id=self.device_id,
                request_id=request_id,
                payload=payload,
            )
        )

    # -- broker (eventos sanitizados já existentes → móveis) -------------------

    async def _broker_forward(self) -> None:
        from app.remote.events import subscribe_events, unsubscribe_events

        q, _history = subscribe_events()
        last_sweep = time.monotonic()
        try:
            while self._running and self._connected:
                try:
                    entry = q.get_nowait()
                except Exception:  # noqa: BLE001 — fila vazia
                    if time.monotonic() - last_sweep >= self._heartbeat_interval:
                        self._sweep_stale_heartbeats()
                        last_sweep = time.monotonic()
                    await asyncio.sleep(1.0)
                    continue
                entry_type = (entry or {}).get("type") or ""
                data = (entry or {}).get("data") or {}
                if entry_type.startswith("proactive."):
                    await self._route_proactive(entry)
                    continue
                device_id = data.get("target_device_id") or data.get("device_id")
                if (
                    device_id
                    and device_id in self._bindings
                    and entry_type.startswith(_BROKER_EVENT_PREFIXES)
                ):
                    self.stats["broker_events_forwarded"] += 1
                    await self._send_agent_event(
                        device_id,
                        entry.get("event_id") or "",
                        kind="broker",
                        event={
                            "event_type": entry_type,
                            "event_id": entry.get("event_id"),
                            "at": entry.get("at"),
                            "data": data,
                        },
                    )
        finally:
            unsubscribe_events(q)

    # -- proativas (raro; fila BOUNDED + TTL, dedup por message_id) ----------

    def _remember_mobile(self, device_id: str) -> None:
        """Registra um device que já usou o WAN (p/ fila de proativas off-line).

        Lista BOUNDED: nunca cresce sem limite; o mais antigo sai com a fila.
        """
        self._known_mobiles[device_id] = _utcnow()
        if len(self._known_mobiles) > _MAX_KNOWN_MOBILES:
            oldest = min(self._known_mobiles, key=self._known_mobiles.get)
            self._known_mobiles.pop(oldest, None)
            self._proactive_queue.pop(oldest, None)

    def _enqueue_proactive(self, device_id: str, entry: dict[str, Any]) -> None:
        """Enfileira para um móvel off-line: bounded por device + TTL + dedup."""
        message_id = (entry.get("data") or {}).get("message_id")
        q = self._proactive_queue.setdefault(
            device_id, deque(maxlen=_MAX_PROACTIVE_QUEUE)
        )
        if message_id:
            for _ts, old in q:
                if (old.get("data") or {}).get("message_id") == message_id:
                    return  # dedup: já enfileirado
        q.append((time.monotonic(), entry))
        self.stats["proactive_queued"] += 1

    async def _flush_proactive(self, device_id: str) -> None:
        """Despacha a fila off-line de um móvel no (re)auth — TTL aplicado."""
        q = self._proactive_queue.pop(device_id, None)
        if not q:
            return
        now = time.monotonic()
        expired = 0
        for ts, entry in list(q):
            if now - ts > self._queue_ttl:
                expired += 1
                continue
            await self._send_proactive(device_id, entry)
        if expired:
            self.stats["proactive_expired"] += expired

    async def _route_proactive(self, entry: dict[str, Any]) -> None:
        """Proativa: entrega aos atrelados e enfileira aos off-line conhecidos."""
        for device_id in list(self._bindings):
            self.stats["proactive_delivered"] += 1
            await self._send_proactive(device_id, entry)
        for device_id in list(self._known_mobiles):
            if device_id not in self._bindings and device_id:
                self._enqueue_proactive(device_id, entry)

    async def _send_proactive(self, device_id: str, entry: dict[str, Any]) -> None:
        data = (entry or {}).get("data") or {}
        event_id = (entry or {}).get("event_id") or ""
        await self._send_agent_event(
            device_id,
            event_id,
            kind="proactive",
            event={
                "event_type": (entry or {}).get("type"),
                "event_id": event_id,
                "message_id": data.get("message_id"),
                "priority": data.get("priority"),
                "title": data.get("title"),
                "created_at": data.get("created_at"),
                "content": str(data.get("content") or "")[:_PROACTIVE_CONTENT_LIMIT],
            },
        )

    def _sweep_stale_heartbeats(self) -> None:
        """Marca device_deconectado para atrelados sem heartbeat no intervalo.

        Apenas observabilidade: o binding é conservado (o relé pode manter o peer
        vivo), apenas o heartbeat antigo é limpo e o evento é emitido uma vez.
        """
        if not self._last_mobile_heartbeat:
            return
        threshold = self._heartbeat_interval * 3
        now = _utcnow()
        stale = [
            d for d, ts in self._last_mobile_heartbeat.items() if (now - ts).total_seconds() > threshold
        ]
        for device_id in stale:
            self._last_mobile_heartbeat.pop(device_id, None)
            self._publish_gateway_event(
                "remote.gateway.device_disconnected", {"device_id": device_id}
            )


def _parse_sse_item(item: str) -> dict[str, Any] | str:
    """Converte um item SSE (`data: {...}\n\n`) num dict; senão devolve str."""
    try:
        line = item[6:] if item.startswith("data: ") else item
        parsed = json.loads(line.strip())
        return parsed if isinstance(parsed, dict) else str(parsed)
    except (ValueError, AttributeError):
        return str(item)


def bound_device_context(device_id: str | None) -> bool:
    """Compat: o Device Bridge sabe se o device está ativo (para stats)."""
    if not device_id:
        return True
    return True


def _brief(exc: BaseException, limit: int = 160) -> str:
    return f"{type(exc).__name__}: {exc}"[:limit]


def _utcnow() -> datetime:
    from app.models.session import utcnow

    return utcnow()


MAX_REMOTE_MESSAGE_LENGTH  # re-export simbólico (usado por imports de testes)