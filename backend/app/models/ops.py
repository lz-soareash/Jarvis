"""Observabilidade (Fase 11): eventos estruturados da execução do AI Core.

`ExecutionEvent` é a fonte de dados da Central de Operações: cada evento registra
metadados de uma operação (provedor escolhido, fallback, latência, caminho) de
forma **sanitizada** — nunca conteúdo completo de mensagens, tokens, chaves ou
secrets. Logs em arquivo continuam existindo só para debugging.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.session import utcnow


class ExecutionEvent(Base):
    """Uma ocorrência observável do ciclo de IA (Fase 11).

    `event_type` segue um vocabulário estável: chat.started, provider.selected,
    path.selected, chat.completed, chat.failed. `status` qualifica (ok/fallback/
    failed). `meta_json` guarda apenas metadados sanitizados.
    """

    __tablename__ = "execution_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    __table_args__ = (
        Index("ix_execution_events_type_created", "event_type", "created_at"),
    )