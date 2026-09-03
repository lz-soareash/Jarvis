"""Gateway remota local (Fase 12.1/12.3): orquestração do transporte.

Conecta o Core como CLIENTE à Gateway externa (WebSocket outbound), reage ao
protocolo (hello/heartbeat/command) e registra auditoria (AuditLog) e
observabilidade (ExecutionEvent).

Fase 12.1: **nunca executava ferramentas** (command → ACK `not_implemented`).
Fase 12.3: a execução real de comandos entra via `command_handler` (callback
fornecido pelo RemoteAgent), que roteia pelo pipeline interno do JARVIS (Tool
Registry → Permission Engine → AuditLog). A Gateway NUNCA executa tools; apenas
transporta o comando ao Core.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.db.session import SessionLocal
from app.remote.connection import RemoteConnection
from app.remote.manager import EVENT_RECEIVED, EVENT_SENT, RemoteConnectionManager
from app.remote.protocol import (
    REMOTE_PROTOCOL_VERSION,
    MessageType,
    RemoteEnvelope,
    build_message,
)
from app.services import audit, ops

logger = logging.getLogger("jarvis.remote.gateway")

# Vocabulário de auditoria do transporte remoto (Fase 12.1).
AUDIT_CONNECTED = "device_connected"
AUDIT_DISCONNECTED = "device_disconnected"
AUDIT_CONNECTION_FAILED = "remote_connection_failed"
AUDIT_MESSAGE_RECEIVED = "remote_message_received"
AUDIT_MESSAGE_SENT = "remote_message_sent"

# Eventos observáveis (ExecutionEvent), prefixo `remote.*` (Fase 12.1).
OPS_CONNECTED = "remote.connected"
OPS_DISCONNECTED = "remote.disconnected"
OPS_CONNECTION_FAILED = "remote.connection_failed"
OPS_MESSAGE_RECEIVED = "remote.message_received"
OPS_MESSAGE_SENT = "remote.message_sent"

_TRANSPORT_TOOL = "transport"


def _brief(exc: BaseException, limit: int = 160) -> str:
    """Descrição curta e não sensível do erro (sem tokens/payloads)."""
    return f"{type(exc).__name__}: {exc}"[:limit]


class RemoteGateway:
    """Gateway local do dispositivo: uma conexão com a Gateway remota."""

    def __init__(
        self,
        connection: RemoteConnection,
        *,
        device_id: str,
        device_token: str,
        heartbeat_interval: float = 30.0,
        command_handler: (
            Callable[[RemoteEnvelope], Awaitable[RemoteEnvelope | None]] | None
        ) = None,
    ) -> None:
        self.device_id = device_id
        self._token = device_token  # nunca logado; usado apenas no transporte
        self._command_handler = command_handler
        self.manager = RemoteConnectionManager(
            connection,
            device_id=device_id,
            heartbeat_interval=heartbeat_interval,
        )
        self.manager.on_event = self._on_event
        self.manager.register(MessageType.HELLO_ACK, self._on_hello_ack)
        self.manager.register(MessageType.HEARTBEAT_ACK, self._on_heartbeat_ack)
        self.manager.register(MessageType.COMMAND, self._on_command)
        self._connected = False
        # Serializa as transações de telemetria remota: o banco de testes é um
        # único StaticPool in-memory (uma conexão compartilhada) e a escrita é
        # síncrona dentro do event loop; sem o lock, transações de tasks
        # diferentes se intercalam e travam o SQLite em :memory:.
        self._telemetry_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        """Conecta, faz o handshake `hello` e marca o device como conectado."""
        try:
            await self.manager.start()
        except Exception as exc:
            await self._record_connection_failed(exc)
            raise
        try:
            hello = build_message(
                MessageType.HELLO,
                device_id=self.device_id,
                payload={"protocol_version": REMOTE_PROTOCOL_VERSION},
            )
            await self.manager.send(hello)
        except Exception as exc:
            await self._record_connection_failed(exc)
            await self.manager.stop()
            raise
        self._connected = True
        await self._record(AUDIT_CONNECTED, OPS_CONNECTED, status="ok", detail="conexão com a Gateway estabelecida")

    async def stop(self) -> None:
        """Encerra a conexão de forma graciosa e registra o desligamento."""
        was_connected = self._connected
        self._connected = False
        if was_connected:
            await self.manager.stop()
            await self._record(AUDIT_DISCONNECTED, OPS_DISCONNECTED, status="ok", detail="conexão com a Gateway encerrada")
        else:
            await self.manager.stop()

    async def send_message(self, envelope: RemoteEnvelope) -> None:
        """Envia um envelope arbitrário (ex.: resposta de um comando)."""
        await self.manager.send(envelope)

    # -- handlers do protocolo -----------------------------------------------

    async def _on_hello_ack(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        # O hello_ack confirma o handshake; nada a responder.
        return None

    async def _on_heartbeat_ack(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        # Mantém a conexão viva; nada a responder.
        return None

    async def _on_command(self, envelope: RemoteEnvelope) -> RemoteEnvelope | None:
        """Roteia um comando ao pipeline interno (Fase 12.3) quando fornecido.

        Sem `command_handler` (padrão 12.1), apenas acusa com
        `not_implemented` — a Gateway NUNCA executa tools por conta própria.
        """
        if self._command_handler is None:
            logger.info(
                "command recebido device=%s command_id=%s — execução não configurada",
                self.device_id,
                envelope.command_id,
            )
            return build_message(
                MessageType.COMMAND_ACK,
                device_id=self.device_id,
                command_id=envelope.command_id,
                payload={"status": "received", "execution": "not_implemented"},
            )
        return await self._command_handler(envelope)

    # -- observabilidade ------------------------------------------------------

    async def _on_event(self, event: str, meta: dict[str, Any]) -> None:
        """Mapeia eventos do manager para auditoria + ExecutionEvent."""
        if event == EVENT_RECEIVED:
            await self._record(AUDIT_MESSAGE_RECEIVED, OPS_MESSAGE_RECEIVED, status="ok", meta=meta)
        elif event == EVENT_SENT:
            await self._record(AUDIT_MESSAGE_SENT, OPS_MESSAGE_SENT, status="ok", meta=meta)

    async def _record_connection_failed(self, exc: BaseException) -> None:
        detail = _brief(exc)
        logger.error("Conexão remota falhou: %s", detail)
        await self._record(AUDIT_CONNECTION_FAILED, OPS_CONNECTION_FAILED, status="failed", detail=detail, allowed=False)

    async def _record(
        self,
        audit_action: str,
        ops_event: str,
        *,
        status: str,
        detail: str | None = None,
        allowed: bool | None = True,
        meta: dict[str, Any] | None = None,
    ) -> None:
        meta = meta or {}
        try:
            async with self._telemetry_lock:
                with SessionLocal() as db:
                    audit.log_action(
                        db,
                        action=audit_action,
                        tool=_TRANSPORT_TOOL,
                        allowed=allowed,
                        detail=detail or str(meta),
                    )
                    ops.record_event(
                        db,
                        event_type=ops_event,
                        provider="remote",
                        status=status,
                        meta=meta,
                    )
        except Exception as exc:  # noqa: BLE001 — telemetria nunca derruba o transporte
            logger.error("Falha ao registrar evento remoto (%s): %s", audit_action, _brief(exc))