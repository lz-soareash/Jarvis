"""Computer State & Observation Store (Fase 15.3).

Camada de estado do computador consumível pelo Agentic Core, mantida EM MEMÓRIA
(processo) com histórico BOUNDED e TTL — sem persistência de screenshots.
Regras:

- `ObservationStore` guarda as N observações mais recentes (bounded) com TTL;
  obs velhas expiram.
- `ComputerState` agrega a observação atual + última metadata de screenshot.
- Screenshots NUNCA são mantidos: apenas metadata (timestamp/width/height/hash)
  fica na memória e por um período limitado.
- Nada aqui é resiliente a restart (estado não-persistente por design).
"""

import threading
import time
from collections import deque
from datetime import timedelta
from typing import Any

from app.perception.base import ComputerObservation

ObservationTTL = timedelta(minutes=2)


class ObservationStore:
    """Armazena observações em memória, bounded e com TTL (thread-safe)."""

    def __init__(self, max_entries: int = 10, ttl: timedelta = ObservationTTL) -> None:
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._ttl = ttl
        self._entries: deque[tuple[float, ComputerObservation]] = deque()

    def push(self, observation: ComputerObservation) -> None:
        with self._lock:
            self._entries.append((time.monotonic(), observation))
            self._prune_locked()

    def _prune_locked(self) -> None:
        now = time.monotonic()
        # remove expirados do início (mais antigos primeiro)
        while self._entries and now - self._entries[0][0] > self._ttl.total_seconds():
            self._entries.popleft()
        # bounded: mantém só os N mais recentes
        while len(self._entries) > self._max_entries:
            self._entries.popleft()

    def latest(self) -> ComputerObservation | None:
        with self._lock:
            self._prune_locked()
            if not self._entries:
                return None
            return self._entries[-1][1]

    def all(self) -> list[ComputerObservation]:
        with self._lock:
            self._prune_locked()
            return [obs for _, obs in self._entries]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class ComputerState:
    """Estado agregado atual do computador (em memória, bounded).

    Mantém a observação mais recente + metadata da última screenshot. O binário
    de screenshot nunca é retido.
    """

    def __init__(self, store: ObservationStore | None = None) -> None:
        self.store = store or ObservationStore()
        self._lock = threading.Lock()
        self._last_screenshot: dict[str, Any] | None = None

    def record(self, observation: ComputerObservation) -> None:
        self.store.push(observation)
        if observation.screenshot:
            self._remember_screenshot(observation.screenshot.to_meta_dict())

    def _remember_screenshot(self, meta: dict[str, Any]) -> None:
        with self._lock:
            self._last_screenshot = {"at": meta.get("timestamp"), "meta": meta}

    @property
    def last_screenshot(self) -> dict[str, Any] | None:
        with self._lock:
            return self._last_screenshot

    def snapshot(self) -> dict[str, Any]:
        """Resumo sanitizado do estado atual para consumo/pipeline."""
        latest = self.store.latest()
        return {
            "observation": latest.to_dict() if latest else None,
            "history_count": len(self.store.all()),
            "last_screenshot": self.last_screenshot,
        }

    def clear(self) -> None:
        with self._lock:
            self._last_screenshot = None
        self.store.clear()
