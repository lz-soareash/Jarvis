"""Fase 13 — Workspace: gestão de tarefas agênticas (Agentic Core).

Endpoints de consulta/gestão do `AgentTask` de uma sessão. A execução da
tarefa é streaming SSE (`POST /tasks`); a decisão de aprovações (quando um
passo exigir nível ≥ 2) flui pelo endpoint `/approvals/{id}/respond`.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider
from app.db.session import get_db
from app.schemas.workspace import AgentTaskCreate, AgentTaskOut
from app.services import agent_core as agent_core_service

from .deps import get_ai_provider

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _to_out(task) -> AgentTaskOut:
    return AgentTaskOut.model_validate(
        {
            "id": task.id,
            "session_id": task.session_id,
            "objective": task.objective,
            "status": task.status,
            "steps_done": task.steps_done,
            "steps_total": task.steps_total,
            "error": task.error,
            "plan": task.plan,
            "progress": task.progress,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
        }
    )


@router.get("/tasks", response_model=list[AgentTaskOut])
def list_tasks(
    session_id: str | None = None,
    db: OrmSession = Depends(get_db),
) -> list[AgentTaskOut]:
    """Lista as tarefas agênticas de uma sessão (ou todas sem filtro)."""
    return [_to_out(t) for t in agent_core_service.list_tasks(db, session_id)]


@router.get("/tasks/{task_id}", response_model=AgentTaskOut)
def get_task(task_id: str, db: OrmSession = Depends(get_db)) -> AgentTaskOut:
    task = agent_core_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    return _to_out(task)


@router.post("/tasks", response_class=StreamingResponse)
async def create_and_run_task(
    session_id: str,
    body: AgentTaskCreate,
    db: OrmSession = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
):
    """Cria e executa uma tarefa agêntica, emitindo eventos SSE."""
    from app.services import chat as chat_service

    if chat_service.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if not (body.objective or "").strip():
        raise HTTPException(status_code=422, detail="objective é obrigatório")

    generator = agent_core_service.run_agent_task(
        db, session_id, provider, body.objective
    )
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/tasks/{task_id}/cancel", response_model=AgentTaskOut)
def cancel_task(task_id: str, db: OrmSession = Depends(get_db)) -> AgentTaskOut:
    task = agent_core_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    agent_core_service.cancel_task(db, task)
    return _to_out(task)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, db: OrmSession = Depends(get_db)) -> None:
    task = agent_core_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    agent_core_service.delete_task(db, task)