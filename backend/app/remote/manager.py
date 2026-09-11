"""Gerente de conexão: receive loop, heartbeat e despacho (Fase 12.1).

Camada de transporte pura: conhece envelopes, estados e handlers. **Nunca executa
ferramentas** — a decisão de executar cabe a camadas superiores. Emissão de
eventos de observabilidade via `on_event` (message_received / message_sent).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.remote.connection import RemoteConnection
from app.remote.protocol import MessageType, RemoteEnvelope, build_message
from app.remote.ratelimit import RateLimiter

logger = logging.getLogger("jarvis.remote.manager")

# Handler: recebe o envelope e devolve a resposta a ser enviada (ou None).
MessageHandler = Callable[[RemoteEnvelope], Awaitable[RemoteEnvelope | None]]

# Callback de observabilidade: (evento, metadados sanitizados).
EventHook = Callable[[str, dict[str, Any]], Awaitable[None]]

EVENT_RECEIVED = "message_received"
EVENT_SENT = "message_sent"


class RemoteConnectionManager:
    """Gerencia UMA conexão: receive loop, heartbeat e despacho por tipo."""

    def __init__(
        self,
        connection: RemoteConnection,
        *,
        device_id: str,
        heartbeat_interval: float = 30.0,
        send_limiter: RateLimiter | None = None,
        heartbeat_sent_hook: Callable[[], None] | None = None,
    ) -> None:
        self.connection = connection
        self.device_id = device_id
        self.heartbeat_interval = heartbeat_interval
        self.send_limiter = send_limiter
        # Gancho chamado após cada envio de heartbeat (Fase 24: medição de RTT
        # do link Core ↔ relé — nunca toca payloads/secrets).
        self.heartbeat_sent_hook = heartbeat_sent_hook
        self.handlers: dict[MessageType, MessageHandler] = {}
        self.on_event: EventHook | None = None
        self._running = False
        self._receive_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

    def register(self, msg_type: MessageType, handler: MessageHandler) -> None:
        """Registra o handler para um tipo de mensagem (último vence)."""
        self.handlers[msg_type] = handler

    async def start(self) -> None:
        """Conecta e inicia os loops de receive e heartbeat."""
        if self._running:
            return
        self._running = True
        try:
            await self.connection.connect()
        except Exception:
            self._running = False
            raise
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def send(self, envelope: RemoteEnvelope) -> None:
        """Envia um envelope e emite observabilidade de envio."""
        if self.send_limiter is not None:
            await self.send_limiter.acquire()
        await self.connection.send(envelope)
        await self._emit(EVENT_SENT, envelope)

    async def send_heartbeat(self) -> None:
        """Envia um heartbeat explícito (útil para testes/rotinas manuais)."""
        envelope = build_message(MessageType.HEARTBEAT, device_id=self.device_id)
        if self.send_limiter is not None:
            await self.send_limiter.acquire()
        await self.connection.send(envelope)
        await self._emit(EVENT_SENT, envelope)
        if self.heartbeat_sent_hook is not None:
            self.heartbeat_sent_hook()

    async def stop(self) -> None:
        """Encerramento gracioso: para loops e fecha a conexão (STOPPING→DISCONNECTED)."""
        if not self._running:
            return
        self._running = False
        for task in (self._heartbeat_task, self._receive_task):
            if task is not None and not task.done():
                task.cancel()
        await self.connection.shutdown()
        for task in (self._heartbeat_task, self._receive_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._heartbeat_task = None
        self._receive_task = None

    # -- internals ----------------------------------------------------------

    async def _receive_loop(self) -> None:
        while self._running:
            try:
                envelope = await self.connection.receive()
            except asyncio.CancelledError:
                break
            except ConnectionError as exc:
                logger.info("Conexão encerrada (receive): %s", exc)
                break
            except Exception as exc:  # noqa: BLE001 — transporte frágil não derruba processo
                logger.error("Erro no receive loop: %s", exc)
                break
            await self._dispatch(envelope)

    async def _dispatch(self, envelope: RemoteEnvelope) -> None:
        await self._emit(EVENT_RECEIVED, envelope)
        handler = self.handlers.get(envelope.type)
        if handler is None:
            logger.warning("Tipo sem handler registrado: %s", envelope.type.value)
            await self._send_reply(MessageType.ERROR, envelope, {"error": "unsupported_type"})
            return
        try:
            reply = await handler(envelope)
        except Exception as exc:  # noqa: BLE001
            logger.error("Handler %s falhou: %s", envelope.type.value, exc)
            await self._send_reply(MessageType.ERROR, envelope, {"error": "handler_error"})
            return
        if reply is not None:
            await self.connection.send(reply)
            await self._emit(EVENT_SENT, reply)

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.heartbeat_interval)
            if not self._running:
                break
            try:
                await self.send_heartbeat()
            except ConnectionError as exc:
                # Conexão encerrada — o receive loop também quebrará; encerra
                # p/ permitir reconexão supervisionada.
                logger.info("Heartbeat falhou (conexão encerrada): %s", exc)
                break
            except Exception as exc:  # noqa: BLE001
                # Fase 12.8: falha transitória NÃO derruba o heartbeat —
                # registra e tenta de novo no próximo ciclo (evita o ciclo
                # completo de reconexão por um único blip).
                logger.error("Erro no heartbeat (transitório): %s", exc)

    async def _send_reply(
        self,
        msg_type: MessageType,
        original: RemoteEnvelope | None,
        payload: dict[str, Any],
    ) -> None:
        reply = build_message(
            msg_type,
            device_id=self.device_id,
            command_id=original.command_id if original else None,
            payload=payload,
        )
        if self.send_limiter is not None:
            await self.send_limiter.acquire()
        await self.connection.send(reply)
        await self._emit(EVENT_SENT, reply)

    async def _emit(self, event: str, envelope: RemoteEnvelope) -> None:
        if self.on_event is None:
            return
        meta: dict[str, Any] = {
            "type": envelope.type.value,
            "message_id": envelope.message_id,
        }
        if envelope.command_id:
            meta["command_id"] = envelope.command_id
        await self.on_event(event, meta)