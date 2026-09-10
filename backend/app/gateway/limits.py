"""Defesas locais mínimas do Gateway WAN (Fase 23).

O relé NÃO decide confiança (quem decide é o Core). Mesmo assim aplica limites
de recurso por origem para não virar bucha de canhão:

- token bucket de conexões por IP (anti flood de handshake) + teto simultâneo
  por IP;
- teto de payload por envelope (anti oversize em memória).
"""

from __future__ import annotations

import logging
import threading

from app.gateway.config import get_settings
from app.remote.ratelimit import RateLimiter

logger = logging.getLogger("jarvis.gateway.limits")

_GLOBAL_KEY = "__global__"


class GatewayLimits:
    """Limites locais de recurso (por processo; um event loop)."""

    def __init__(self, settings=None) -> None:
        self._settings = settings or get_settings()
        self._lock = threading.Lock()
        self._buckets: dict[str, RateLimiter] = {}
        self._per_ip: dict[str, int] = {}
        self.rejected_connects = 0
        self.oversized_envelopes = 0

    def allow_connect(self, origin: str | None) -> bool:
        """Decide se um handshake vindo de `origin` (IP) é aceito."""
        origin = origin or "local"
        with self._lock:
            bucket = self._buckets.get(origin)
            if bucket is None:
                bucket = RateLimiter(
                    self._settings.connect_rate_capacity,
                    self._settings.connect_rate_refill_per_sec,
                )
                self._buckets[origin] = bucket
            global_bucket = self._buckets.get(_GLOBAL_KEY)
            if global_bucket is None:
                global_bucket = RateLimiter(
                    self._settings.connect_rate_capacity * 4,
                    self._settings.connect_rate_refill_per_sec * 4,
                )
                self._buckets[_GLOBAL_KEY] = global_bucket
            if not (bucket.allow() and global_bucket.allow()):
                self.rejected_connects += 1
                return False
            current = self._per_ip.get(origin, 0)
            if current >= max(1, self._settings.max_peers_per_ip):
                self.rejected_connects += 1
                return False
            self._per_ip[origin] = current + 1
            return True

    def release_connect(self, origin: str | None) -> None:
        origin = origin or "local"
        with self._lock:
            current = self._per_ip.get(origin, 0)
            if current > 0:
                self._per_ip[origin] = current - 1

    def allow_payload(self, size_bytes: int) -> bool:
        if size_bytes > self._settings.max_payload_bytes:
            self.oversized_envelopes += 1
            return False
        return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._per_ip.clear()
            self.rejected_connects = 0
            self.oversized_envelopes = 0