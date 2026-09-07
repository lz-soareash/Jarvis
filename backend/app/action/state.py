"""Computer Action Layer (Fase 18) — estado de execução em memória.

Permite observação da camada de ação (quem está executando, últimas ações)
sem depender do banco. Uso interno/exposição sanitizada no observer.
"""

from __future__ import annotations

import threading
from collections import deque, defaultdict
from typing import Any

from .models import ActionStatus, ActionType


class ActionExecutionState:
    """Estado in-memory das execuções de ações (thread-safe).

    Guarda apenas metadata sanitizada — nunca textos completos, keys ou paths.
    """

    def __init__(self, max_history: int = 50) -> None:
        self._lock = threading.Lock()
        self._history: deque[dict[str, Any]] = deque(maxlen=max_history)
        self._active: dict[str, dict[str, Any]] = {}
        self._counts_by_type: dict[str, int] = defaultdict(int)
        self._counts_by_status: dict[str, int] = defaultdict(int)
        self._counts_by_source: dict[str, int] = defaultdict(int)
        self._rate_limited_count = 0

    def start(self, action_id: str, action_type: ActionType, requested_by: str, dry_run: bool) -> None:
        with self._lock:
            self._active[action_id] = {
                "action_type": action_type.value,
                "requested_by": requested_by,
                "dry_run": dry_run,
                "status": ActionStatus.ACCEPTED.value,
            }

    def finish(
        self,
        action_id: str,
        status: ActionStatus,
        duration_ms: int,
        adapter_detail: str | None = None,
    ) -> None:
        with self._lock:
            entry = self._active.pop(action_id, None)
            if entry is None:
                return
            self._counts_by_type[entry["action_type"]] += 1
            self._counts_by_status[status.value] += 1
            self._counts_by_source[entry["requested_by"]] += 1
            record = dict(entry)
            record["status"] = status.value
            record["duration_ms"] = duration_ms
            if adapter_detail:
                record["adapter_detail"] = adapter_detail
            self._history.appendleft(record)

    def record_rate_limited(self) -> None:
        with self._lock:
            self._rate_limited_count += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_count": len(self._active),
                "history_count": len(self._history),
                "counts_by_type": dict(self._counts_by_type),
                "counts_by_status": dict(self._counts_by_status),
                "counts_by_source": dict(self._counts_by_source),
                "rate_limited_count": self._rate_limited_count,
                "last_actions": [dict(h) for h in self._history],
            }


_state: ActionExecutionState | None = None


def get_action_state() -> ActionExecutionState:
    global _state
    if _state is None:
        _state = ActionExecutionState()
    return _state


def reset_action_state() -> None:
    global _state
    _state = None