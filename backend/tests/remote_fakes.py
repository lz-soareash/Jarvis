"""Fakes do transporte remoto para testes (Fase 12.1) — tudo em processo, sem rede.

`FakeGateway` simula o lado SERVIDOR (a Gateway remota real). Os testes conectam
a `RemoteGateway` do Core (cliente) a ela através de pares de `MemoryPipe`,
validando o protocolo de forma hermética.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.remote.connection import (
    ConnectionState,
    InMemoryConnection,
    MemoryPipe,
    RemoteConnection,
)
from app.remote.protocol import MessageType, RemoteEnvelope, build_message

logger = logging.getLogger("jarvis.remote.testfake")


class FailingConnection(RemoteConnection):
    """Conexão que sempre falha no `connect` — simula Gateway inacessível."""

    def __init__(self, exc: Exception | None = None) -> None:
        super().__init__()
        self._exc = exc or ConnectionError("gateway inacessível")

    async def connect(self) -> None:
        self.state = ConnectionState.CONNECTING
        raise self._exc

    async def send(self, envelope: RemoteEnvelope) -> None:
        raise ConnectionError("conexão nunca estabelecida")

    async def receive(self) -> RemoteEnvelope:
        raise ConnectionError("conexão nunca estabelecida")

    async def close(self) -> None:
        self.state = ConnectionState.DISCONNECTED


class RecordingConnection(InMemoryConnection):
    """InMemoryConnection que também registra envelopes recebidos (para asserts)."""

    def __init__(self, pipe: MemoryPipe) -> None:
        super().__init__(pipe)
        self.received: list[RemoteEnvelope] = []

    async def receive(self) -> RemoteEnvelope:
        envelope = await super().receive()
        self.received.append(envelope)
        return envelope


class FakeGateway:
    """Gateway remota simulada (lado servidor) em processo — sem rede nem worker."""

    def __init__(self, pipe: MemoryPipe, *, device_id: str = "fake-gateway") -> None:
        self.connection = InMemoryConnection(pipe)
        self.device_id = device_id
        self.received: list[RemoteEnvelope] = []
        self.commands_seen: list[RemoteEnvelope] = []
        self._run_task: asyncio.Task | None = None

    @property
    def messages_sent(self) -> list[RemoteEnvelope]:
        return self.connection.sent

    async def start(self) -> None:
        await self.connection.connect()
        self._run_task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while True:
                envelope = await self.connection.receive()
                self.received.append(envelope)
                if envelope.type == MessageType.HELLO:
                    await self.connection.send(
                        build_message(
                            MessageType.HELLO_ACK,
                            device_id=self.device_id,
                            payload={"status": "ready"},
                        )
                    )
                elif envelope.type == MessageType.HEARTBEAT:
                    await self.connection.send(
                        build_message(MessageType.HEARTBEAT_ACK, device_id=self.device_id)
                    )
                elif envelope.type == MessageType.COMMAND:
                    self.commands_seen.append(envelope)
                    await self.connection.send(
                        build_message(
                            MessageType.COMMAND_ACK,
                            device_id=self.device_id,
                            command_id=envelope.command_id,
                            payload={"status": "ok"},
                        )
                    )
        except (ConnectionError, asyncio.CancelledError):
            pass

    async def push_command(self, command_id: str, payload: dict[str, Any] | None = None) -> None:
        """Simula a Gateway enviando um comando para o device."""
        await self.connection.send(
            build_message(
                MessageType.COMMAND,
                device_id=self.device_id,
                command_id=command_id,
                payload=payload or {},
            )
        )

    async def push_message(self, envelope: RemoteEnvelope) -> None:
        await self.connection.send(envelope)

    async def stop(self) -> None:
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self.connection.close()


async def wait_for(predicate, timeout: float = 2.0, interval: float = 0.005) -> None:
    """Aguarda até que `predicate()` seja verdadeiro (ou lança AssertionError)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condição não satisfeita no tempo limite")