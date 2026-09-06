"""Schemas da Fase 13 — Agentic Core & Workspace (tarefas agênticas)."""

from datetime import datetime

from app.schemas.base import APIModel


class AgentStepOut(APIModel):
    """Um passo do plano de uma tarefa (blueprint imutável)."""

    description: str = ""
    tool: str
    arguments: dict = {}
    verify: dict = {}
    guard: str = "skip"


class AgentTaskCreate(APIModel):
    """Criação de uma nova tarefa agêntica."""

    objective: str


class AgentTaskOut(APIModel):
    """Tarefa agêntica persistida (estado + plano + progresso)."""

    id: str
    session_id: str
    objective: str
    status: str
    steps_done: int = 0
    steps_total: int = 0
    error: str | None = None
    plan: list[AgentStepOut] = []
    progress: list[dict] = []
    created_at: datetime | None = None
    completed_at: datetime | None = None