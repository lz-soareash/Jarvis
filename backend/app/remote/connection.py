"""Abstração de transporte com a Gateway (Fase 12.1).

O PC é sempre o CLIENTE (WebSocket outbound); nenhuma porta de entrada é aberta.
`RemoteConnection` é o contrato; `WebSocketConnection` é a implementação real
(lazy import de `websockets`); `InMemoryConnection`/`MemoryPipe` são variantes
em processo usadas pelos testes sem rede alguma.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from app.remote.protocol import RemoteEnvelope, decode_message, encode_message

logger = logging.getLogger("jarvis.remote.connection")


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"


class RemoteConnection(ABC):
    """Contrato de conexão com a Gateway (troca de envelopes do protocolo)."""

    def __init__(self) -> None:
        self.state: ConnectionState = ConnectionState.DISCONNECTED

    @property
    def is_connected(self) -> bool:
        return self.state == ConnectionState.CONNECTED

    @abstractmethod
    async def connect(self) -> None:
        """Estabelece a conexão (bloqueia até CONNECTED ou levanta erro)."""

    @abstractmethod
    async def send(self, envelope: RemoteEnvelope) -> None:
        """Envia um envelope; levanta ConnectionError se não houver conexão."""

    @abstractmethod
    async def receive(self) -> RemoteEnvelope:
        """Aguarda e retorna o próximo envelope; levanta ConnectionError se fechar."""

    @abstractmethod
    async def close(self) -> None:
        """Fecha de imediato."""

    async def shutdown(self) -> None:
        """Encerramento gracioso: STOPPING → DISCONNECTED (nunca trava em receive)."""
        if self.state == ConnectionState.DISCONNECTED:
            return
        self.state = ConnectionState.STOPPING
        try:
            await asyncio.wait_for(self.close(), timeout=5.0)
        finally:
            self.state = ConnectionState.DISCONNECTED


class WebSocketConnection(RemoteConnection):
    """Conexão WebSocket real (outbound, PC → Gateway).

    `websockets` é importado de forma preguiçosa (fornecido por
    `uvicorn[standard]`), então módulos sem rede não dependem dele.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        recv_timeout: float | None = None,
    ) -> None:
        super().__init__()
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self._recv_timeout = recv_timeout
        self._ws: Any = None

    async def connect(self) -> None:
        import websockets  # lazy — dependência opcional fora de uso real

        self.state = ConnectionState.CONNECTING
        try:
            self._ws = await websockets.connect(
                self.url,
                additional_headers=self.headers,
                open_timeout=self.timeout,
            )
        except Exception:
            self.state = ConnectionState.DISCONNECTED
            raise
        self.state = ConnectionState.CONNECTED
        logger.info("Conexão WebSocket estabelecida com %s", self.url)

    async def send(self, envelope: RemoteEnvelope) -> None:
        self._require_connected()
        await self._ws.send(encode_message(envelope))

    async def receive(self) -> RemoteEnvelope:
        self._require_connected()
        # Fase 12.8: timeout no recv p/ evitar bloqueio indefinido do loop em
        # conexão meio-aberta (silenciosa). Em TimeoutError, levanta
        # ConnectionError para o receive loop tratar como desconexão — o
        # supervisor fará a reconexão/backoff (não morre em silêncio).
        try:
            timeout = self._recv_timeout if self._recv_timeout is not None else self.timeout
            raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ConnectionError("timeout aguardando mensagem da Gateway") from exc
        return decode_message(raw)

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=5.0)
            except Exception:  # noqa: BLE001 — fechamento é best-effort
                pass
            self._ws = None
        self.state = ConnectionState.DISCONNECTED

    def _require_connected(self) -> None:
        if self.state != ConnectionState.CONNECTED or self._ws is None:
            raise ConnectionError("conexão não estabelecida")


# ---------------------------------------------------------------------------
# Transporte em processo (sem rede) — fakes e testes
# ---------------------------------------------------------------------------


class MemoryPipe:
    """Meia conexão em memória: envia para o par, recebe do par.

    `recv_from_peer` desbloqueia tanto ao chegar mensagem quanto ao pipe fechar
    (via evento), permitindo shutdown gracioso sem travar loops de receive.
    """

    def __init__(self) -> None:
        self.local_queue: asyncio.Queue[RemoteEnvelope] = asyncio.Queue()
        self.remote_queue: asyncio.Queue[RemoteEnvelope] = asyncio.Queue()
        self.closed = asyncio.Event()

    async def send_to_peer(self, envelope: RemoteEnvelope) -> None:
        await self.remote_queue.put(envelope)

    async def recv_from_peer(self) -> RemoteEnvelope:
        get_task = asyncio.create_task(self.local_queue.get())
        close_task = asyncio.create_task(self.closed.wait())
        try:
            done, _pending = await asyncio.wait(
                {get_task, close_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if get_task in done:
                return get_task.result()
            raise ConnectionError("pipe fechado")
        finally:
            for task in (get_task, close_task):
                if not task.done():
                    task.cancel()

    def close(self) -> None:
        self.closed.set()


def create_duplex() -> tuple[MemoryPipe, MemoryPipe]:
    """Cria um par de pipes conectados (A → B e B → A)."""
    a = MemoryPipe()
    b = MemoryPipe()
    a.remote_queue = b.local_queue
    b.remote_queue = a.local_queue
    return a, b


class InMemoryConnection(RemoteConnection):
    """Conexão em processo (pares de pipes) — usada por fakes e testes."""

    def __init__(self, pipe: MemoryPipe) -> None:
        super().__init__()
        self.pipe = pipe
        self.sent: list[RemoteEnvelope] = []

    async def connect(self) -> None:
        if self.pipe.closed.is_set():
            raise ConnectionError("pipe fechado")
        self.state = ConnectionState.CONNECTED

    async def send(self, envelope: RemoteEnvelope) -> None:
        if self.state != ConnectionState.CONNECTED:
            raise ConnectionError("conexão não estabelecida")
        await self.pipe.send_to_peer(envelope)
        self.sent.append(envelope)

    async def receive(self) -> RemoteEnvelope:
        if self.state != ConnectionState.CONNECTED:
            raise ConnectionError("conexão não estabelecida")
        return await self.pipe.recv_from_peer()

    async def close(self) -> None:
        if self.state == ConnectionState.DISCONNECTED:
            return
        self.pipe.close()
        self.state = ConnectionState.DISCONNECTED