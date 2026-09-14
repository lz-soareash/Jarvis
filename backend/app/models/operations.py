"""Fase 28 — Remote Operations: modelo de operações coordenadas entre dispositivos.

Uma `RemoteOperation` representa um plano de passos (ex.: "abra o YouTube no PC
e depois o Chrome no celular") criado pelo agente e executado pelo orquestrador
`app.remote.operations`. Apenas METADADOS sanitizados são persistidos — jamais
tokens, credenciais, device_ids brutos ou payloads sensíveis. Cada passo guarda
uma linha curta de status/resultado legível (nunca o content bruto).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.session import utcnow


class RemoteOperation(Base):
    __tablename__ = "remote_operations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Sessão JARVIS à qual a operação pertence (aprovações e continuidade).
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=True, index=True
    )
    requested_action: Mapped[str] = mapped_column(String(160), default="")
    # pending | running | awaiting_confirmation | authorized | success | failed
    # | denied | unsupported | timeout | cancelled
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    # Passos serializados e sanitizados (lista de dicts).
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_remote_operations_session_status", "session_id", "status"),
    )