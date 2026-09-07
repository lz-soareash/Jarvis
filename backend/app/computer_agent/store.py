"""Registro em memória (bounded) das tarefas do Computer Agent.

Espelha o padrão `ObservationStore` (Fase 15) / `ActionExecutionState`
(Fase 18): estado observável do agente em memória, sem nova infraestrutura de
persistência. Tarefa pausada por confirmação fica resumível via approvals.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from app.core.config import settings

from .models import ComputerStatus, ComputerTaskState, Limits

_MAX_TASKS = 50


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_task_id() -> str:
    return f"catask_{uuid.uuid4().hex[:12]}"


def new_action_id() -> str:
    return f"caaction_{uuid.uuid4().hex[:12]}"


def limits_from_settings() -> Limits:
    return Limits(
        max_steps=int(settings.computer_agent_max_steps),
        max_actions=int(settings.computer_agent_max_actions),
        max_retries=int(settings.computer_agent_max_retries),
        max_plan_retries=int(settings.computer_agent_max_plan_retries),
        timeout_seconds=float(settings.computer_agent_timeout_seconds),
    )


def create_task(*, goal: str, session_id: str | None, requested_by: str,
                device_id: str | None, autonomy: str | None = None) -> ComputerTaskState:
    from .models import AutonomyLevel

    try:
        level = AutonomyLevel.from_str(autonomy or settings.computer_agent_autonomy)
    except ValueError:
        level = AutonomyLevel.C1
    now = _utcnow()
    limits = limits_from_settings()
    return ComputerTaskState(
        task_id=new_task_id(),
        goal=goal.strip()[:1000],
        session_id=session_id,
        status=ComputerStatus.PLANNING,
        autonomy=level,
        started_at=now,
        updated_at=now,
        requested_by=requested_by,
        device_id=device_id,
        execution_deadline=datetime.now(timezone.utc).timestamp()
        + float(settings.computer_agent_timeout_seconds),
        deadline=datetime.now(timezone.utc).timestamp()
        + float(settings.computer_agent_timeout_seconds),
        max_steps=limits.max_steps,
        max_actions=limits.max_actions,
        max_retries=limits.max_retries,
        max_plan_retries=limits.max_plan_retries,
        timeout_seconds=limits.timeout_seconds,
    )


class ComputerTaskStore:
    """Registro bounded de tarefas correntes/recentes (em memória)."""

    def __init__(self, max_tasks: int = _MAX_TASKS) -> None:
        self._tasks: dict[str, ComputerTaskState] = {}
        self._lock = threading.Lock()
        self._max_tasks = max_tasks

    def save(self, task: ComputerTaskState) -> None:
        with self._lock:
            self._tasks[task.task_id] = task.with_(updated_at=_utcnow())
            if len(self._tasks) > self._max_tasks:
                # Remove as mais antigas (por started_at textual iso).
                oldest = sorted(self._tasks.values(), key=lambda t: t.started_at)[: len(self._tasks) - self._max_tasks]
                for t in oldest:
                    self._tasks.pop(t.task_id, None)

    def get(self, task_id: str) -> ComputerTaskState | None:
        with self._lock:
            return self._tasks.get(task_id)

    def get_by_approval(self, approval_id: str) -> ComputerTaskState | None:
        with self._lock:
            for task in self._tasks.values():
                if approval_id in task.pending_approval_ids:
                    return task
        return None

    def list(self, limit: int = 20) -> list[ComputerTaskState]:
        with self._lock:
            items = sorted(self._tasks.values(), key=lambda t: t.started_at, reverse=True)
            return items[:limit]

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()


_store = ComputerTaskStore()


def get_store() -> ComputerTaskStore:
    return _store


def reset_store() -> None:
    get_store().clear()