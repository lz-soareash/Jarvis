"""Proteção contra abuso da camada remota (Fase 16, seções 21-23).

Estratégia simples e extensível, em processo (uma instância por processo):

- `auth`: token buckets por origem (IP) + um bucket global compartilhado —
  limita tentativas de autenticação (anti brute-force no `/remote/auth`);
- `message`: token buckets POR DEVICE autenticado — limita mensagens/streams;
- `concurrency`: contador com trava (thread-safe, sem `asyncio.Semaphore`
  presa a um event loop) — teto de mensagens em processamento simultâneo.

Quando um limite é estourado, os helpers elevam `RemoteError(RATE_LIMITED)`
(429) — a mensagem é idêntica para não revelar o motivo exato ao atacante.
"""

from __future__ import annotations

import logging
import threading

from app.core.config import settings
from app.remote.errors import RemoteError, RemoteErrorCode
from app.remote.ratelimit import RateLimiter

logger = logging.getLogger("jarvis.remote.limits")

_AUTH_GLOBAL_KEY = "__global__"


class RemoteLimitGuard:
    """Guarda de limites da camada remota (rate + concorrência)."""

    def __init__(
        self,
        *,
        auth_capacity: float,
        auth_refill: float,
        message_capacity: float,
        message_refill: float,
        max_concurrent: int,
    ) -> None:
        self._auth_capacity = auth_capacity
        self._auth_refill = auth_refill
        self._message_capacity = message_capacity
        self._message_refill = message_refill
        self._max_concurrent = max(1, max_concurrent)
        self._auth_buckets: dict[str, RateLimiter] = {}
        self._msg_buckets: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()
        self._inflight = 0

    # -- auth (por origem + global) -----------------------------------------

    def allow_auth(self, origin: str | None) -> bool:
        origin = origin or "local"
        with self._lock:
            per_ip = self._auth_buckets.get(origin)
            if per_ip is None:
                per_ip = RateLimiter(self._auth_capacity, self._auth_refill)
                self._auth_buckets[origin] = per_ip
            global_bucket = self._auth_buckets.get(_AUTH_GLOBAL_KEY)
            if global_bucket is None:
                global_bucket = RateLimiter(self._auth_capacity, self._auth_refill)
                self._auth_buckets[_AUTH_GLOBAL_KEY] = global_bucket
        return per_ip.allow() and global_bucket.allow()

    def check_auth(self, origin: str | None) -> None:
        """Eleva RATE_LIMITED se a origem/global estourou o bucket de auth."""
        if not self.allow_auth(origin):
            logger.info("Rate limit de autenticação excedido (origin=%s)", origin or "local")
            raise RemoteError(
                RemoteErrorCode.RATE_LIMITED,
                "muitas tentativas de autenticação; tente novamente mais tarde",
            )

    # -- message (por device) ----------------------------------------------

    def allow_message(self, device_id: str) -> bool:
        key = device_id or "__no_device__"
        with self._lock:
            bucket = self._msg_buckets.get(key)
            if bucket is None:
                bucket = RateLimiter(self._message_capacity, self._message_refill)
                self._msg_buckets[key] = bucket
        return bucket.allow()

    def check_message(self, device_id: str) -> None:
        """Eleva RATE_LIMITED se o device excedeu o teto de mensagens."""
        if not self.allow_message(device_id):
            logger.info("Rate limit de mensagens excedido (device=%s)", device_id or "?")
            raise RemoteError(
                RemoteErrorCode.RATE_LIMITED,
                "limite de mensagens excedido; aguarde e tente novamente",
            )

    # -- concorrência --------------------------------------------------------

    def try_acquire(self) -> bool:
        """Reserva um slot de processamento se houver capacidade; senão False."""
        with self._lock:
            if self._inflight >= self._max_concurrent:
                return False
            self._inflight += 1
            return True

    def release(self) -> None:
        """Libera um slot de processamento reservado por `try_acquire`."""
        with self._lock:
            if self._inflight > 0:
                self._inflight -= 1

    def reset(self) -> None:
        """Limpa buckets/conta de inflight (hermeticidade em testes)."""
        with self._lock:
            self._auth_buckets.clear()
            self._msg_buckets.clear()
            self._inflight = 0


# Instância compartilhada — parâmetros vêm da Settings (0/disabilitada = sem teto).
def _guard() -> RemoteLimitGuard:
    return _shared_guard


def _new_guard_from_settings() -> RemoteLimitGuard:
    return RemoteLimitGuard(
        auth_capacity=max(1.0, settings.remote_auth_rate_capacity),
        auth_refill=max(0.1, settings.remote_auth_rate_refill),
        message_capacity=max(1.0, settings.remote_message_rate_capacity),
        message_refill=max(0.1, settings.remote_message_rate_refill),
        max_concurrent=max(1, settings.remote_max_concurrent_messages),
    )


_shared_guard = _new_guard_from_settings()


def check_auth_rate(origin: str | None = None) -> None:
    _shared_guard.check_auth(origin)


def check_message_rate(device_id: str) -> None:
    _shared_guard.check_message(device_id)


def acquire_slot() -> bool:
    return _shared_guard.try_acquire()


def release_slot() -> None:
    _shared_guard.release()


def reset_limits() -> None:
    """Reset dos limites compartilhados (uso em testes)."""
    global _shared_guard
    _shared_guard.reset()
    _shared_guard = _new_guard_from_settings()