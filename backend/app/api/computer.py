"""Fase 19 — API do Computer Agent (Computer Use).

Endpoints mínimos: criar/consultar/cancelar tarefa, status e SSE de eventos.
Tudo OFF por padrão: se `computer_agent_enabled=false`, criar tarefa → 503.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.db.session import get_db
from app.schemas.base import APIModel

from app.computer_agent import store, service
from app.computer_agent.agent import ComputerAgent

router = APIRouter(prefix="/api/computer", tags=["computer-agent"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class ComputerTaskCreate(APIModel):
    goal: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None
    autonomy: str | None = None
    explicit_authorization: bool = False


class ComputerTaskOut(APIModel):
    task_id: str
    goal: str
    status: str
    autonomy: str
    step_index: int
    steps_total: int
    actions_total: int
    recoveries: int
    loops_prevented: int
    confirmations_required: int
    pending_approval_ids: list[str]
    error: str | None
    started_at: str
    updated_at: str
    completed_at: str | None
    last_action_summary: str | None
    provider_used: str | None
    provider_reason: str | None
    requested_by: str
    device_id: str | None


def _to_out(task) -> ComputerTaskOut:
    snap = task.snapshot()
    return ComputerTaskOut(**snap)


def _require_enabled() -> None:
    if not settings.computer_agent_enabled:
        raise HTTPException(status_code=503, detail="computer_agent_enabled=false")


def _get_provider():
    from app.ai.providers import get_default_provider
    return get_default_provider()


@router.get("/status")
def status() -> dict:
    """Estado da camada + tarefas correntes (sanitizado)."""
    return {
        "enabled": bool(settings.computer_agent_enabled),
        "autonomy_default": settings.computer_agent_autonomy,
        "limits": {
            "max_steps": settings.computer_agent_max_steps,
            "max_actions": settings.computer_agent_max_actions,
            "max_retries": settings.computer_agent_max_retries,
            "timeout_seconds": settings.computer_agent_timeout_seconds,
        },
        "tasks_active": sum(
            1 for t in store.get_store().list(100)
            if t.status in ("planning", "perceiving", "executing", "observing",
                            "verifying", "recovering", "waiting_confirmation")
        ),
    }


@router.post("/tasks", response_model=ComputerTaskOut, status_code=201)
async def create_task(body: ComputerTaskCreate, db: OrmSession = Depends(get_db)):
    _require_enabled()
    task = service.start_task_record(
        goal=body.goal,
        session_id=body.session_id,
        requested_by="local",
        autonomy=body.autonomy,
    )
    task = task.with_(explicit_authorization=body.explicit_authorization)
    store.get_store().save(task)
    return _to_out(task)


@router.get("/tasks/{task_id}", response_model=ComputerTaskOut)
def get_task(task_id: str, db: OrmSession = Depends(get_db)):
    task = service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa de Computer Use não encontrada")
    return _to_out(task)


@router.post("/tasks/{task_id}/cancel", response_model=ComputerTaskOut)
def cancel_task(task_id: str):
    task = service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    task = task.with_(cancellation_requested=True)
    store.get_store().save(task)
    return _to_out(task)


@router.post("/tasks/{task_id}/run", response_class=StreamingResponse)
async def run_task(
    task_id: str,
    db: OrmSession = Depends(get_db),
    provider: Any = Depends(_get_provider),
):
    """Executa a tarefa emitindo eventos SSE: observação/ação/verificação/estado."""
    _require_enabled()
    task = service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    agent = ComputerAgent()
    generator = service.run_task_stream(db, provider, task, agent=agent)
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/tasks")
def list_tasks(limit: int = 20):
    return [_to_out(t) for t in service.list_tasks(limit)]