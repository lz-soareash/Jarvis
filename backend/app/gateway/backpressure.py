"""Backpressure de saída do Gateway WAN (Fase 23).

Cada conexão tem uma `Mailbox` com fila BOUNDED (`queue.Queue`, threadsafe) de
envelopes a enviar. Um cliente lento (rede ruim, consumo menor que o ritmo de
produção) NUNCA bloqueia o relé nem o Core: ao atingir o teto, o envelope é
descartado e contabilizado (`dropped`) — a camada de aplicação (não o relé)
garante a entrega (ex.: outbox do Core / re-consulta do cliente).
"""

from __future__ import annotations

import logging
import queue as _queue
from typing import Any

logger = logging.getLogger("jarvis.gateway.backpressure")


class Mailbox:
    """Fila bounded de saída com política de descarte (never blocks producer)."""

    def __init__(self, maxsize: int = 128) -> None:
        self._q: _queue.Queue[Any] = _queue.Queue(maxsize=maxsize)
        self._closed = False
        self.dropped = 0

    def put(self, item: Any) -> bool:
        """Enfileira `item`; False se cheio (descartado, contado) ou fechado."""
        if self._closed:
            return False
        try:
            self._q.put_nowait(item)
            return True
        except _queue.Full:
            self.dropped += 1
            logger.warning("Backpressure: mailbox cheia, envelope descartado (%d)", self.dropped)
            return False

    def get(self) -> Any | None:
        """Retorna o próximo item ou None se vazio/fechado (non-blocking)."""
        try:
            return self._q.get_nowait()
        except _queue.Empty:
            return None

    def close(self) -> None:
        self._closed = True

    @property
    def size(self) -> int:
        return self._q.qsize()

    def drain(self) -> list[Any]:
        """Remove e retorna tudo o que houver na fila (uso em encerramentos)."""
        items: list[Any] = []
        while True:
            item = self.get()
            if item is None:
                return items
            items.append(item)