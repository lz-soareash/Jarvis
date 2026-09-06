"""Proactive Agent (Fase 17) — engine (orquestra evento → decisão → entrega).

Pipeline por evento (determinístico, sem LLM):
    inbox row → intenção (decision.intended_decision) → gates de política
    (policy.policy_gate) → ação (ignorar/adiar/notificar/perguntar/executar).

`EXECUTE` passa obrigatoriamente por `gate_execute` (Tool Registry → Permission
Engine → auditoria) — nunca há execução livre. O `tick()` cobre agendamentos,
reavaliação de adiados e expiração de mensagens; o worker async respeita o
FastAPI lifespan (sem thread infinita).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings

logger = logging.getLogger("jarvis.proactive.engine")

_tick_lock = threading.Lock()
_llm_invocations = 0


def _record_metric(db, kind: str, status: str, meta: dict | None = None) -> None:
    from app.services.ops import record_event

    record_event(
        db,
        event_type=f"proactive.{kind}",
        status=status,
        meta=meta or {},
    )


def note_llm_invocation() -> None:
    """Contador do hook de síntese (Fase 17 = nunca chamado)."""
    global _llm_invocations
    _llm_invocations += 1


def llm_invocations() -> int:
    return _llm_invocations


def _describe(row) -> tuple[str, str]:
    """Título/conteúdo enxuto e sanitizado para a mensagem proativa."""
    payload = dict(row.payload()) if hasattr(row, "payload") else {}
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        title = row.event_type.replace(".", " ").title()
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        bits = [
            f"{k}={v}"
            for k, v in payload.items()
            if isinstance(v, (str, int, float, bool)) and k not in ("title",)
        ]
        message = ", ".join(bits[:8]) or row.event_type
    return title.strip()[:120], message.strip()[:800]


def _decide_and_act(db, row) -> str:
    """Aplica intenção + gates + efeito completo. Retorna a decisão final."""
    from app.proactive import decision as dec
    from app.proactive import policy
    from app.services.audit import log_action

    intent = dec.intended_decision(row)
    gate = policy.policy_gate(row, now_utc=datetime.now(timezone.utc))

    # Evento "low" deliberadamente não notifica (anti-spam).
    if intent == dec.DECISION_IGNORE:
        row.decision = dec.DECISION_IGNORE
        row.delivered = True
        row.decided_at = datetime.now(timezone.utc)
        _record_metric(
            db,
            "events_ignored",
            "ignore",
            {"event_type": row.event_type, "priority": row.priority},
        )
        return dec.DECISION_IGNORE

    if not gate.allowed:
        if gate.non_deferrable:
            row.decision = dec.DECISION_IGNORE
            row.delivered = True
            row.decided_at = datetime.now(timezone.utc)
            _record_metric(
                db,
                "events_ignored",
                "disabled",
                {"event_type": row.event_type, "priority": row.priority},
            )
            return dec.DECISION_IGNORE
        # DEFER: quiet hours / cooldown / rate limit.
        row.decision = dec.DECISION_DEFER
        row.delivered = False
        row.decided_at = datetime.now(timezone.utc)
        row.next_evaluation_at = datetime.now(timezone.utc) + timedelta(
            seconds=max(1, gate.defer_for or int(settings.proactive_tick_seconds))
        )
        _record_metric(
            db,
            "events_deferred",
            gate.reason,
            {"event_type": row.event_type, "priority": row.priority},
        )
        return dec.DECISION_DEFER

    if intent in (dec.DECISION_ASK, dec.DECISION_EXECUTE):
        tool_name = (row.payload() or {}).get("tool")
        result = dec.gate_execute(db, row)
        row.decided_at = datetime.now(timezone.utc)
        if result == dec.DECISION_EXECUTE:
            row.decision = dec.DECISION_EXECUTE
            row.delivered = True
            log_action(
                db,
                action="proactive.execute",
                tool=tool_name,
                allowed=True,
                detail=f"Execução proativa autorizada (LEVEL_1) para {tool_name}",
            )
            _record_metric(
                db,
                "events_executed",
                "execute",
                {"event_type": row.event_type, "tool": tool_name},
            )
            return dec.DECISION_EXECUTE
        if result == dec.DECISION_ASK:
            row.decision = dec.DECISION_ASK
            row.delivered = True
            deliver_row(db, row, decision=dec.DECISION_ASK)
            log_action(
                db,
                action="proactive.ask",
                tool=tool_name,
                allowed=None,
                detail=f"Ação proativa requer confirmação (LEVEL_2/3) para {tool_name}",
            )
            _record_metric(
                db,
                "events_ask",
                "ask",
                {"event_type": row.event_type, "tool": tool_name},
            )
            return dec.DECISION_ASK
        row.decision = dec.DECISION_IGNORE
        row.delivered = True
        log_action(
            db,
            action="proactive.blocked",
            tool=tool_name,
            allowed=False,
            detail=f"Ação proativa bloqueada para {tool_name} (origem/tool desconhecida)",
        )
        _record_metric(
            db,
            "events_blocked",
            "blocked",
            {"event_type": row.event_type, "tool": tool_name},
        )
        return dec.DECISION_IGNORE

    # NOTIFY (padrão para tipos documentados).
    row.decision = dec.DECISION_NOTIFY
    title, content = _describe(row)
    ok = deliver_row(db, row, decision=dec.DECISION_NOTIFY, title=title, content=content)
    if ok.created:
        row.delivered = True
    _record_metric(
        db,
        "events_notified",
        "notify",
        {"event_type": row.event_type, "priority": row.priority, "created": ok.created},
    )
    return dec.DECISION_NOTIFY


def deliver_row(db, row, *, decision: str, title: str | None = None, content: str | None = None) -> Any:
    """Cria/entrega a mensagem proativa (idempotente por dedup_key)."""
    from app.proactive.delivery import deliver

    if title is None or content is None:
        title, content = _describe(row)
    return deliver(
        db,
        event=row,
        decision=decision,
        title=title,
        content=content,
    )


def process_event(event_id: str) -> str | None:
    """Processa um evento do inbox por `id`; retorna a decisão (ou None).

    Reavalia também entradas ainda não entregues cujo veredito foi adiar
    (`defer`) — é assim que o sweep de quiet hours/cooldown/rate promove
    notificações. Eventos já decididos e entregues não reentram.
    """
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.proactive import ProactiveInboxEvent
    from app.proactive import decision as dec

    db = SessionLocal()
    try:
        row = db.scalars(
            select(ProactiveInboxEvent).where(ProactiveInboxEvent.id == event_id)
        ).first()
        if row is None:
            return None
        if row.decision is not None and row.decision != dec.DECISION_DEFER:
            return None
        return _decide_and_act(db, row)
    except Exception:  # noqa: BLE001 — nunca derruba a fonte do evento
        logger.exception("Falha ao processar evento proativo %s", event_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    finally:
        db.close()


def tick() -> dict[str, int]:
    """Ciclo periódico: dispara schedules + reavalia DEFER + expira mensagens.

    Thread-safe (lock) e no-op quando desabilitado. Cada fase roda em sessão
    própria e encerrada (sequencial) — nunca duas `SessionLocal` abertas na
    mesma conexão in-memory (StaticPool).
    """
    from app.db.session import SessionLocal
    from app.proactive import delivery, scheduler

    if _tick_lock.locked():
        return {"fired": 0, "swept": 0, "expired": 0}

    stats = {"fired": 0, "swept": 0, "expired": 0}
    with _tick_lock:
        try:
            if settings.proactive_enabled and settings.proactive_scheduler_enabled:
                db1 = SessionLocal()
                try:
                    stats["fired"] = scheduler.fire_due(db1)
                finally:
                    db1.close()
                db2 = SessionLocal()
                try:
                    stats["swept"] = scheduler.deferred_sweep(db2)
                finally:
                    db2.close()
            db3 = SessionLocal()
            try:
                stats["expired"] = delivery.expire_old(db3)
            finally:
                db3.close()
        except Exception:  # noqa: BLE001 — tick nunca derruba o worker
            logger.exception("Tick proativo falhou")
    return stats


async def worker_loop() -> None:
    """Worker async (lifespan): ciclos de `tick()` sem bloquear o event loop."""
    while True:
        await asyncio.sleep(max(1.0, float(settings.proactive_tick_seconds)))
        try:
            await asyncio.to_thread(tick)
        except Exception:  # noqa: BLE001 — worker resiliente
            logger.exception("Worker proativo falhou (nova tentativa no próximo ciclo)")


def reset_engine() -> None:
    """Zera estado process-local do engine (hermeticidade de testes)."""
    global _llm_invocations
    _llm_invocations = 0
    from app.proactive import policy

    policy.reset_policy_state()
    from app.proactive.events import reset_broker

    reset_broker()