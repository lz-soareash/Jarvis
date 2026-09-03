"""Agente remoto (Fase 12.3): conexão persistente + comandos.

O `RemoteAgent` é a camada de orquestração que:
- autentica o device com a credencial (Bearer token) via `authenticate_bearer`;
- abre uma sessão remota e reconhece a sessão JARVIS do device;
- supervisiona a conexão com a Gateway, reconectando com backoff exponencial
  (min → max, com jitter) e parando em revogação (estado terminal REVOKED);
- despacha `COMMAND` recebidos ao executor, que roteia pelo pipeline interno
  (Tool Registry → Permission Engine → AuditLog) — a Gateway NÃO executa tools;
- expõe observabilidade sanitizada (`RemoteHealthState`).

Identidade SEMPRE vem da credencial autenticada; `command_id` dialoga com o
registro persistente (remote_commands) para idempotência atômica.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.db.session import SessionLocal
from app.remote.connection import ConnectionState, RemoteConnection
from app.remote.executor import ApprovalRequired, execute_remote_command
from app.remote.jarvis_session import get_or_create_jarvis_session
from app.remote.protocol import MessageType, RemoteEnvelope, build_message
from app.remote.status import RemoteHealthState
from app.services import audit, ops

logger = logging.getLogger("jarvis.remote.agent")
AUDIT_REMOTE_AGENT_STARTED = "remote_agent.started"
AUDIT_REMOTE_AGENT_REVOKED = "remote_agent.revoked"
OPS_REMOTE_AGENT_STARTED = "remote.agent.started"
OPS_REMOTE_AGENT_REVOKED = "remote.agent.revoked"

_TRANSPORT_TOOL = "transport"


class RemoteAgent:
    """Orquestrador da conexão remota persistente do dispositivo."""

    def __init__(
        self,
        *,
        device_id: str,
        device_token: str,
        connection_factory: Callable[[], RemoteConnection],
        heartbeat_interval: float = 30.0,
        connect_timeout: float = 10.0,
        min_reconnect_delay: float = 0.5,
        max_reconnect_delay: float = 60.0,
        jitter: float = 0.1,
    ) -> None:
        self.device_id = device_id
        self._token = device_token  # nunca logado
        self._connection_factory = connection_factory
        self._heartbeat_interval = heartbeat_interval
        self._connect_timeout = connect_timeout
        self._min_reconnect_delay = min_reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._jitter = jitter

        self._running = False
        self._supervisor: asyncio.Task | None = None
        self._gateway: Any | None = None
        self._lock = asyncio.Lock()  # serializa processamento de comandos
        self._session_id: str | None = None  # sessão remota ativa
        self._jarvis_session_id: str | None = None
        self._credential_id: str | None = None
        self._health = RemoteHealthState(
            enabled=True,
            connection_state=ConnectionState.DISCONNECTED.value,
            authenticated=False,
            healthy=False,
            device_id=device_id,
            reconnect_count=0,
        )
        self._connected_at: datetime | None = None
        self._last_heartbeat: datetime | None = None
        self._last_error: str | None = None

    # -- observabilidade ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return self._health.to_dict()

    # -- ciclo de vida --------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._set_health(connection_state=ConnectionState.CONNECTING.value)
        await self._audit_started()
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
        gateway = self._gateway
        self._gateway = None
        if gateway is not None:
            try:
                await gateway.stop()
            except Exception:  # noqa: BLE001
                pass
        self._set_health(connection_state=ConnectionState.DISCONNECTED.value, authenticated=False)

    # -- supervisor (reconnect com backoff) -----------------------------------

    async def _supervise(self) -> None:
        from app.remote.gateway import RemoteGateway

        delay = self._min_reconnect_delay
        while self._running:
            authed = await self._authenticate()
            if authed is False:  # credencial revogada / device inativo → terminal
                break
            if authed is None:  # falha transitória de auth? backoff
                delay = self._backoff(delay)
                if not await self._wait(delay):
                    break
                continue

            connection = self._connection_factory()
            gateway = RemoteGateway(
                connection,
                device_id=self.device_id,
                device_token=self._token,
                heartbeat_interval=self._heartbeat_interval,
                command_handler=self._handle_command,
            )
            self._gateway = gateway
            try:
                await gateway.start()
            except Exception as exc:  # noqa: BLE001
                self._set_health(
                    connection_state=ConnectionState.RECONNECTING.value,
                    healthy=False,
                    last_error=_brief(exc),
                )
                delay = self._backoff(delay)
                if not await self._wait(delay):
                    break
                continue

            self._set_health(
                connection_state=ConnectionState.CONNECTED.value,
                authenticated=True,
                healthy=True,
                last_error=None,
            )
            self._health.connected_at = _utcnow()
            delay = self._min_reconnect_delay  # reset do backoff após sucesso
            self._publish_connection("connected")

            # Fase 12.7 — re-entrega direcionada: no (re)connect, envia resultados
            # pendentes do outbox que ficaram para trás quando o device estava off.
            await self._flush_pending_results()

            # Aguarda desconexão (receive/heartbeat quebram) mantendo a nova
            # sessão ecossistema; detecta revogação via re-auth periódica.
            disconnected = asyncio.Event()
            monitor = asyncio.create_task(self._monitor_disconnect(gateway, disconnected))
            try:
                await disconnected.wait()
            finally:
                monitor.cancel()
                try:
                    await monitor
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

            if not self._running:
                break
            # Se a credencial foi revogada durante a conexão → terminal.
            if await self._is_revoked():
                self._set_health(
                    connection_state=ConnectionState.DISCONNECTED.value,
                    authenticated=False,
                    healthy=False,
                    revocation="revoked",
                )
                await self._audit_revoked()
                break

            self._set_health(
                connection_state=ConnectionState.RECONNECTING.value,
                authenticated=False,
                healthy=False,
            )
            delay = self._backoff(delay)
            if not await self._wait(delay):
                break
        # fim do loop
        self._gateway = None

    async def _monitor_disconnect(self, gateway, disconnected: asyncio.Event) -> None:
        """Aguarda a conexão cair; também faz re-auth periódica (heartbeat de credencial)."""
        while self._running:
            try:
                # A cada ciclo curto verificamos se a conexão ainda responde.
                await asyncio.sleep(min(self._heartbeat_interval / 2.0, 1.0))
                if gateway.manager._receive_task is None or gateway.manager._receive_task.done():
                    disconnected.set()
                    return
            except Exception:  # noqa: BLE001
                disconnected.set()
                return

    # -- autenticação / revogação ---------------------------------------------

    async def _authenticate(self) -> bool | None:
        """Autentica na primeira conexão. Retorna True/False/None.

        True → pronto para abrir gateway; False → revogado (terminal); None →
        falha transitória (reconectar).
        """
        from app.remote.auth import RemoteAuthError, authenticate_bearer

        try:
            with SessionLocal() as db:
                authed = authenticate_bearer(
                    db,
                    self._token,
                    transport_meta={"transport": "ws", "device_id": self.device_id},
                    claimed_device_id=self.device_id,
                )
                self._session_id = authed.session_id
                self._credential_id = authed.credential.id
                self._jarvis_session_id = get_or_create_jarvis_session(db, authed.device)
                self._health.active_session = authed.session_id
                self._health.authenticated = True
                self._health.revocation = None
                return True
        except RemoteAuthError:
            # Credencial inválida/revogada ou device inativo → terminal (não
            # fazer reconnect infinito com credencial revogada).
            self._set_health(
                connection_state=ConnectionState.DISCONNECTED.value,
                authenticated=False,
                healthy=False,
                revocation="revoked",
                last_error="autenticação negada",
            )
            await self._audit_revoked()
            return False
        except Exception as exc:  # noqa: BLE001
            self._set_health(last_error=_brief(exc))
            return None

    async def _is_revoked(self) -> bool:
        from app.remote.auth import RemoteAuthError, authenticate_bearer

        try:
            with SessionLocal() as db:
                authenticate_bearer(
                    db,
                    self._token,
                    claimed_device_id=self.device_id,
                )
                return False
        except RemoteAuthError:
            return True
        except Exception:  # noqa: BLE001
            return False

    def _backoff(self, current: float) -> float:
        """Retorna o próximo delay de backoff exponencial (com jitter)."""
        nxt = min(current * 2.0, self._max_reconnect_delay)
        jittered = nxt * (1.0 - self._jitter) + random.random() * (2.0 * self._jitter * nxt)
        return max(0.0, min(jittered, self._max_reconnect_delay))

    async def _wait(self, delay: float) -> bool:
        """Aguarda `delay` s, retornando False se o agente foi parado."""
        self._health.reconnect_count += 1
        self._set_health(connection_state=ConnectionState.RECONNECTING.value, healthy=False)
        try:
            await asyncio.wait_for(self._stop_waiter(), timeout=delay)
            return False  # parado
        except asyncio.TimeoutError:
            return True  # tempo esgotado, continue

    async def _stop_waiter(self) -> None:
        while self._running:
            await asyncio.sleep(0.05)

    # -- despacho de comandos -------------------------------------------------

    async def _handle_command(self, envelope: RemoteEnvelope) -> RemoteEnvelope:
        command_id = envelope.command_id or ""
        payload = envelope.payload or {}
        try:
            async with self._lock:
                with SessionLocal() as db:
                    from app.models.remote import Credential, Device
                    from app.remote.devices import get_device

                    # Validação leve de identidade/revogação: carrega a credencial
                    # autenticada na conexão e confere se continua ativa (revogação
                    # mid-connection é respeitada). NÃO cria nova sessão por comando.
                    credential = db.get(Credential, self._credential_id) if self._credential_id else None
                    device = get_device(db, self.device_id) if credential is not None else None
                    if (
                        credential is None
                        or device is None
                        or not credential.is_active
                        or device.status != "active"
                    ):
                        self._set_health(revocation="revoked", healthy=False)
                        return self._build_reply(
                            command_id, {"status": "revoked", "message": "credencial revogada"}
                        )

                    jarvis_session_id = get_or_create_jarvis_session(db, device)
                    result = await execute_remote_command(
                        db,
                        device=device,
                        credential=credential,
                        session=None,
                        jarvis_session_id=jarvis_session_id,
                        command_id=command_id,
                        type_name=envelope.type.value,
                        payload=payload,
                    )
                    return self._build_reply(command_id, result)
        except ApprovalRequired as exc:
            return self._build_reply(
                command_id,
                {"status": "approval_required", "approval_id": exc.approval_id, "tool": exc.type_name},
            )
        except Exception as exc:  # noqa: BLE001 — falha sanitizada
            return self._build_reply(
                command_id,
                {"status": "error", "message": _brief(exc)},
            )

    def _build_reply(self, command_id: str, payload: dict[str, Any]) -> RemoteEnvelope:
        return build_message(
            MessageType.COMMAND_RESULT,
            device_id=self.device_id,
            message_id=None,
            command_id=command_id,
            payload=payload,
        )

    def deliver_result(self, command_id: str, payload: dict[str, Any]) -> bool:
        """Fase 12.4/12.7 — entrega best-effort de um resultado à Gateway ativa.

        Retorna True se havia conexão ativa e o envio foi disparado. NUNCA lança
        e NUNCA é requisito para a execução: o resultado já foi persistido de
        forma transacional antes desta chamada (e, na Fase 12.7, também no outbox
        p/ re-entrega no reconnect). Se não houver conexão ativa (device offline),
        o cliente recupera via endpoint de consulta (`GET /remote/commands/...`).
        """
        gateway = self._gateway
        if gateway is None or not getattr(gateway, "connected", False):
            return False
        try:
            envelope = self._build_reply(command_id, payload)
            self._spawn_send(gateway, envelope)
            return True
        except Exception as exc:  # noqa: BLE001 — push é best-effort
            logger.warning("push de resultado falhou (command=%s): %s", command_id, _brief(exc))
            return False

    def _spawn_send(self, gateway: Any, envelope: RemoteEnvelope) -> None:
        """Dispara o envio sem bloquear/esperar (fire-and-forget)."""
        try:
            asyncio.get_running_loop().create_task(self._safe_send(gateway, envelope))
        except RuntimeError:  # sem loop ativo
            pass

    async def _safe_send(self, gateway: Any, envelope: RemoteEnvelope) -> None:
        try:
            await gateway.send_message(envelope)
        except Exception as exc:  # noqa: BLE001
            logger.warning("envio de resultado falhou: %s", _brief(exc))

    async def _flush_pending_results(self) -> None:
        """Fase 12.7 — re-entrega direcionada dos resultados pendentes do outbox.

        Roda após a conexão ser estabelecida: entrega os `COMMAND_RESULT` que
        ficaram no outbox (device esteve offline) e marca como entregues os que
        foram enviados com sucesso. Best-effort — nunca lança e não interrompe o
        supervisor por falha de re-entrega. Usa UMA sessão para listar/marcar, de
        modo que as entradas permanecem ligadas à mesma transação.
        """
        from app.remote import outbox as outbox_service

        try:
            with SessionLocal() as db:
                pending = outbox_service.list_undelivered(db, device_id=self.device_id)
                for entry in pending:
                    gateway = self._gateway
                    if gateway is None or not getattr(gateway, "connected", False):
                        return  # desconectou durante o flush; reste p/ próxima
                    payload = outbox_service.payload_of(entry)
                    envelope = self._build_reply(entry.command_id, payload)
                    try:
                        await gateway.send_message(envelope)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "re-entrega falhou (command=%s): %s",
                            entry.command_id,
                            _brief(exc),
                        )
                        continue
                    outbox_service.mark_delivered(db, entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("flush de resultados pendentes falhou: %s", _brief(exc))

    # -- helpers --------------------------------------------------------------

    def _set_health(self, **kw: Any) -> None:
        h = self._health
        for k, v in kw.items():
            if hasattr(h, k):
                setattr(h, k, v)

    def _publish_connection(self, state: str) -> None:
        """Fase 12.5 — streama transições de conexão ao barramento SSE (sanitizado)."""
        from app.remote.events import publish_event

        publish_event("remote.connection." + state, {"device_id": self.device_id})

    async def _audit_started(self) -> None:
        try:
            with SessionLocal() as db:
                audit.log_action(db, action=AUDIT_REMOTE_AGENT_STARTED, tool=_TRANSPORT_TOOL, allowed=True)
                ops.record_event(db, event_type=OPS_REMOTE_AGENT_STARTED, provider="remote", status="ok", meta={"device_id": self.device_id})
        except Exception:  # noqa: BLE001
            pass

    async def _audit_revoked(self) -> None:
        try:
            with SessionLocal() as db:
                audit.log_action(db, action=AUDIT_REMOTE_AGENT_REVOKED, tool=_TRANSPORT_TOOL, allowed=False, detail="credencial revogada")
                ops.record_event(db, event_type=OPS_REMOTE_AGENT_REVOKED, provider="remote", status="failed", meta={"device_id": self.device_id})
        except Exception:  # noqa: BLE001
            pass
        self._publish_connection("revoked")


def _brief(exc: BaseException, limit: int = 160) -> str:
    return f"{type(exc).__name__}: {exc}"[:limit]


def _utcnow() -> datetime:
    from app.models.session import utcnow

    return utcnow()
