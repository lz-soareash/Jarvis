"""Proactive Agent (Fase 17) — estatísticas sanitizadas p/ Central de Operações.

Agrega contagens das tabelas proativas + flags de configuração. Nada sensível
(payloads/mensagens não são expostos; só contagens e metadados).
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select

from app.core.config import settings
from app.models.proactive import (
    ProactiveInboxEvent,
    ProactiveMessage,
    ProactiveSchedule,
)


def proactive_stats(db) -> dict:
    """Bloco "Proactive" do `/api/ops/overview` (fonte única de contagem)."""
    inbox_total = db.scalar(select(func.count()).select_from(ProactiveInboxEvent)) or 0
    inbox_by_decision = dict(
        db.execute(
            select(ProactiveInboxEvent.decision, func.count())
            .group_by(ProactiveInboxEvent.decision)
        ).all()
    )
    deferred_pending = (
        db.scalar(
            select(func.count())
            .select_from(ProactiveInboxEvent)
            .where(
                ProactiveInboxEvent.decision == "defer",
                ProactiveInboxEvent.delivered.is_(False),
            )
        )
        or 0
    )

    msg_total = db.scalar(select(func.count()).select_from(ProactiveMessage)) or 0
    msg_status = Counter(
        status or "unknown"
        for (status,) in db.execute(
            select(ProactiveMessage.status).where(ProactiveMessage.status.is_not(None))
        ).all()
    )

    sched_total = db.scalar(select(func.count()).select_from(ProactiveSchedule)) or 0
    sched_enabled = (
        db.scalar(
            select(func.count())
            .select_from(ProactiveSchedule)
            .where(ProactiveSchedule.enabled.is_(True))
        )
        or 0
    )

    from app.proactive.engine import llm_invocations

    decisions = {k: int(v) for k, v in inbox_by_decision.items()}
    return {
        "enabled": bool(settings.proactive_enabled),
        "scheduler_enabled": bool(settings.proactive_scheduler_enabled),
        "quiet_hours": settings.proactive_quiet_hours,
        "interrupt_on_critical": bool(settings.proactive_interrupt_on_critical),
        "max_per_hour": int(settings.proactive_max_per_hour),
        "max_per_day": int(settings.proactive_max_per_day),
        "default_cooldown_seconds": int(settings.proactive_default_cooldown_seconds),
        "events_received": int(inbox_total),
        "events_by_decision": decisions,
        "deferred_pending": int(deferred_pending),
        "messages_total": int(msg_total),
        "messages_pending": int(msg_status.get("pending", 0)),
        "messages_delivered_web": int(msg_status.get("delivered_web", 0))
        + int(msg_status.get("delivered", 0)),
        "messages_delivered_remote": int(msg_status.get("delivered", 0)),
        "messages_expired": int(msg_status.get("expired", 0)),
        "schedules_total": int(sched_total),
        "schedules_enabled": int(sched_enabled),
        "llm_invocations": int(llm_invocations()),  # Fase 17 fixado em 0
    }