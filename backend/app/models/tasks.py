"""Tarefas agênticas (Fase 13 — Agentic Core & Workspace).

`AgentTask` persiste a execução de uma tarefa composta (windshield da camada
AGENT CORE: TASK → PLAN → EXECUTE → OBSERVE → VERIFY → RECOVER/CONTINUE →
COMPLETION). Cada tarefa pertence a uma sessão de chat e guarda:

- o `objective` (texto bruto do usuário);
- o `plan_json` (lista de passos: tool + arguments + verificação);
- o status de ciclo de vida (`TaskStatus`);
- o progresso por passo (`progress_json`).

É a boca de observabilidade e auditoria da camada agêntica, sem duplicar o que
já existe em `execution_events`/`audit_logs` — apenas o contêiner do plano.
"""

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import TaskStatus
from app.db.base import Base
from app.models.session import utcnow


class AgentTask(Base):
    """Uma tarefa composta (plano + execução) dentro de uma sessão."""

    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default=TaskStatus.PLANNED.value, index=True
    )
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_done: Mapped[int] = mapped_column(Integer, default=0)
    steps_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Aprovação que governa a pausa corrente da tarefa (Fase 13): quando um
    # passo de nível ≥ 2 é pausado, este registro liga a decisão do usuário ao
    # passo exato a ser retomado — o ApprovalRequest é a ponte (`resume`).
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def plan(self) -> list[dict]:
        if not self.plan_json:
            return []
        try:
            data = json.loads(self.plan_json)
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    @property
    def progress(self) -> list[dict]:
        if not self.progress_json:
            return []
        try:
            data = json.loads(self.progress_json)
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []