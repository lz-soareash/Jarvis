"""Modelos do Proactive Agent (Fase 17) — proatividade controlada.

Três tabelas cobrem o ciclo evento → decisão → entrega de forma auditável e
idempotente (sem duplicar os eventos existentes de `execution_events`):

- `proactive_inbox_events`: deduplicação por `event_id` UNIQUE (duas chegadas do
  mesmo evento = uma só decisão) + registro da decisão tomada e do resultado.
  É a "fila de entrada" do engine, não uma cópia dos eventos de domínio.
- `proactive_schedules`: agendamentos persistentes (one-shot, intervalo ou
  cron-lite diário) que sobrevivem a restart — o worker recalcula `next_run_at`
  a partir do banco, nunca de estado em memória.
- `proactive_messages`: mensagens proativas entregues à UI/Remote. `dedup_key`
  UNIQUE garante que um mesmo evento nunca notifica duas vezes (idempotência).

Nenhum desses modelos armazena secrets: payloads/mensagens passam por
sanitização antes de chegar aqui.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.session import utcnow


def _uuid() -> str:
    return str(uuid.uuid4())


class ProactiveInboxEvent(Base):
    """Evento de entrada do engine proativo (dedup UNIQUE por `event_id`)."""

    __tablename__ = "proactive_inbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Identificador do evento de origem (produzido pela fonte). UNIQUE = dedup:
    # reprocessar/re-remeter o mesmo evento não gera duas decisões.
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    source: Mapped[str] = mapped_column(String(40), default="system")
    priority: Mapped[str] = mapped_column(String(12), default="normal", index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    # Decisão do engine: ignore | defer | notify | ask | execute | failed.
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Última avaliação para DEFER (reexecuta a política depois do quiet/cooldown).
    next_evaluation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)

    def payload(self) -> dict:
        import json

        if not self.payload_json:
            return {}
        try:
            return dict(json.loads(self.payload_json))
        except (TypeError, ValueError):
            return {}


class ProactiveSchedule(Base):
    """Agendamento persistente do worker proativo (overvive a restart).

    `kind` informa como interpretar `spec`:
    - "oneshot": ISO timestamp (UTC) em `spec`;
    - "interval": segundos (inteiro) entre execuções periódicas;
    - "cron": horário diário "HH:MM" (avaliado no fuso `tz_offset_minutes`).
    """

    __tablename__ = "proactive_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="interval")
    spec: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(80), default="proactive.scheduled")
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(12), default="normal")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    tz_offset_minutes: Mapped[int] = mapped_column(Integer, default=-180)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    run_count: Mapped[int] = mapped_column(Integer, default=0)

    def payload(self) -> dict:
        import json

        if not self.payload_json:
            return {}
        try:
            return dict(json.loads(self.payload_json))
        except (TypeError, ValueError):
            return {}


class ProactiveMessage(Base):
    """Mensagem proativa (separada do chat) — idempotente por `dedup_key`."""

    __tablename__ = "proactive_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Chave de idempotência: event_id de origem (ou sched:{id}) — UNIQUE impede
    # notificar o mesmo item duas vezes ("não duplique notificações").
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    decision: Mapped[str] = mapped_column(String(20), default="notify")
    priority: Mapped[str] = mapped_column(String(12), default="normal")
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_web_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_remote_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)