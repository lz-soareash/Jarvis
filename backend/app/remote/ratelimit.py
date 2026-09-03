"""Rate limiting (Fase 12.1) — foundation p/ o transporte remoto.

Token bucket simples e sem dependências, utilizável como limitador opcional de
saída no `RemoteConnectionManager` (heartbeat/hello/acks). A versão final com
limites por device/tipo e justiça entre sessões chega junto do controle de
acesso na Gateway (fases 12.2/12.3) — aqui fica o primitivo testável.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Token bucket: `capacity` tokens de rajada, refill contínuo por segundo.

    `allow()` não bloqueia (decide na hora); `acquire()` espera (async) até
    conseguir um token — seguro para uso direto dentro do event loop.
    """

    def __init__(self, capacity: float, refill_per_second: float) -> None:
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("capacity e refill_per_second devem ser positivos")
        self.capacity = capacity
        self.refill_rate = refill_per_second
        self._tokens = float(capacity)
        self._last = time.monotonic()

    def allow(self) -> bool:
        """Consome um token se disponível; senão nega sem bloquear."""
        now = time.monotonic()
        self._tokens = min(
            self.capacity, self._tokens + (now - self._last) * self.refill_rate
        )
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    async def acquire(self) -> None:
        """Aguarda (async) até haver um token disponível."""
        while not self.allow():
            await asyncio.sleep(0.05)