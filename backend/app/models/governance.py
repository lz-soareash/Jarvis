"""Governança (Fase 4 — Permissions): pedidos de aprovação, política de tools e auditoria."""

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ApprovalStatus, PermissionLevel
from app.db.base import Base
from app.models.session import utcnow


class ApprovalRequest(Base):
    """Um pedido de aprovação para executar uma ferramenta sensível (nível ≥ 2).

    O modelo propõe a chamada; o Core cria o pedido e pausa o turno. Quando o
    usuário decide (`approved`/`denied`), `run_agent` retoma aplicando a decisão.
    `applied` marca que a decisão já foi embutida no histórico do agente.
    """

    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[str] = mapped_column(Text, default="{}")
    risk: Mapped[str] = mapped_column(String(10), default="low")
    permission_level: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.PENDING.value, index=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped["Session"] = relationship(back_populates="approvals")

    @property
    def arguments_dict(self) -> dict:
        try:
            return json.loads(self.arguments or "{}")
        except ValueError:
            return {}


class ToolPolicy(Base):
    """Override persistente do nível de permissão de uma ferramenta (Fase 4).

    Sem registro, vale o nível declarado pela própria ferramenta. Este modelo
    permite elevar (ex.: gravar memória) ou restringir (ex.: exigir confirmação)
    qualquer ferramenta sem tocar no código.
    """

    __tablename__ = "tool_policies"

    tool_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    permission_level: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditLog(Base):
    """Registro imutável de decisões e execuções relevantes (Fase 4).

    Cada execução de ferramenta e cada decisão de permissão geram um registro,
    criando um trilho auditável e local do que o agente fez e por quê.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(60))
    tool: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped["Session"] = relationship()