"""Proactive Agent (Fase 17) — camada de entrega (Web SSE + Remote).

`ProactiveMessage` é persistida (rastreabilidade) com `dedup_key` UNIQUE —
mesmo evento nunca notifica duas vezes (idempotência). Entrega ao vivo é
best-effort via brokers pub/sub: Web usa o broker proativo (SSE própria,
`/api/proactive/stream`) e, se a camada remota estiver habilitada, Remote vê o
MESMO evento pelo broker remoto (padrão Fase 12.5). Quando o assinante está
offline, o replay limitado do broker cobre reconexões; a persistência garante
durabilidade e auditoria sem re-notificar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import settings

logger = logging.getLogger("jarvis.proactive.delivery")

_MAX_TITLE = 200
_MAX_CONTENT = 8000


@dataclass
class DeliveryResult:
    created: bool
    message_id: str | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def deliver(
    db,
    *,
    event,
    decision: str,
    title: str,
    content: str,
    meta: dict | None = None,
) -> DeliveryResult:
    """Cria (idempotentemente) e entrega uma mensagem proativa.

    Retorna `created=False` se já houver mensagem pelo mesmo evento (dedup).
    """
    from app.models.proactive import ProactiveMessage
    from app.proactive.events import publish
    from app.proactive.policy import note_delivered

    event_id = getattr(event, "event_id", None)
    event_type = getattr(event, "event_type", "proactive.scheduled")
    dedup_key = event_id or f"e:{event_type}"
    now = datetime.now(timezone.utc)

    msg = ProactiveMessage(
        dedup_key=dedup_key,
        event_id=event_id,
        event_type=event_type,
        decision=decision,
        priority=getattr(event, "priority", "normal"),
        title=(title or "JARVIS")[:_MAX_TITLE],
        content=(content or "")[:_MAX_CONTENT],
        created_at=now,
        expires_at=now + timedelta(seconds=int(settings.proactive_message_ttl_seconds)),
    )
    db.add(msg)
    try:
        db.commit()
        db.refresh(msg)
    except Exception:  # noqa: BLE001 — IntegrityError de dedup_key
        db.rollback()
        return DeliveryResult(created=False)

    entry: dict = {
        "message_id": msg.id,
        "event_type": event_type,
        "decision": decision,
        "priority": msg.priority,
        "title": msg.title,
        "created_at": msg.created_at.isoformat(),
        "content": msg.content[:1000],
    }
    # 1) Web (SSE própria): best-effort em processo.
    msg.delivered_web_at = now
    publish("proactive.message", entry)
    # 2) Remote (reuso Fase 12.5): mesmo payload nos eventos remotos.
    if settings.remote_enabled:
        from app.remote.events import publish_event

        publish_event("proactive.message", entry)
        msg.delivered_remote_at = now
        msg.status = "delivered"
    else:
        msg.status = "delivered_web"
    db.commit()
    db.refresh(msg)

    note_delivered(event_type)
    logger.info("Entrega proativa %s (%s): %s", msg.id, decision, event_type)
    return DeliveryResult(created=True, message_id=msg.id)


def expire_old(db, *, now: datetime | None = None) -> int:
    """Expira mensagens proativas além do TTL (sweep do worker)."""
    from sqlalchemy import select

    from app.models.proactive import ProactiveMessage

    now = now or datetime.now(timezone.utc)
    rows = db.scalars(
        select(ProactiveMessage).where(
            ProactiveMessage.expires_at <= now,
            ProactiveMessage.status != "expired",
        )
    ).all()
    for row in rows:
        row.status = "expired"
    if rows:
        db.commit()
    return len(rows)