"""Computer Agent service (Fase 19) — camada de integração.

- `start_task`: cria + roda uma tarefa de Computer Use (SSE ou aguardando).
- `check_cancellation`: interrompe o loop (chamado também por cancelamento).
- Observed: reutiliza Tool/Permission/Approval/Audit existentes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.core.config import settings
from app.services import chat as chat_service

from .agent import ComputerAgent
from .models import ComputerStatus
from .store import create_task, get_store

logger = logging.getLogger("jarvis.computer_agent")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_enabled() -> bool:
    return bool(settings.computer_agent_enabled)


def start_task_record(*, goal: str, session_id: str | None, requested_by: str = "local",
                      device_id: str | None = None, autonomy: str | None = None):
    return create_task(goal=goal, session_id=session_id, requested_by=requested_by,
                       device_id=device_id, autonomy=autonomy)


def cancel_task(task_id: str) -> ComputerStatus:
    from .store import get_store as _store
    task = _store().get(task_id)
    if task is None:
        return ComputerStatus.IDLE
    _store().save(task.with_(cancellation_requested=True))
    return ComputerStatus.CANCELLED


def get_task(task_id: str):
    return get_store().get(task_id)


def list_tasks(limit: int = 20):
    return get_store().list(limit)


async def run_task_stream(
    db: Any,
    provider: Any,
    task,
    *,
    agent: ComputerAgent | None = None,
    sink: Any | None = None,
) -> AsyncIterator[str]:
    """Roda a tarefa emitindo eventos SSE (reuso do formato `sse_event`)."""
    from app.services.chat import sse_event

    agent = agent or ComputerAgent()
    events: list[str] = []

    def emit(payload: dict):
        events.append(sse_event(payload))
        if sink:
            sink(payload)

    await agent.advance(db, provider, task, sink=emit)
    for e in events:
        yield e