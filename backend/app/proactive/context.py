"""Proactive Agent (Fase 17) — context hooks (bounded/sanitized/time-aware).

Contexto montado ANTES de qualquer decisão pesada, com teto rígido de itens e
tamanho — só contagens/estado (nunca conteúdo ou secrets). Este pacote é
determinístico; síntese via LLM é um HOOK futuro opcional e NUNCA é chamado por
evento (ver `needs_llm` sempre False na Fase 17).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

logger = logging.getLogger("jarvis.proactive.context")

_MAX_EVENTS = 10
_MAX_CHARS = 2048


def build_context(db, event) -> dict[str, Any]:
    """Contexto enxuto p/ decisão: relógio local, filtros e eventos recentes."""
    from app.services.ops import list_events

    now = datetime.now(timezone.utc)
    recent = list_events(db, limit=_MAX_EVENTS)
    recent_meta: list[dict[str, Any]] = []
    for item in recent:
        meta = dict(item.meta_json) if getattr(item, "meta_json", None) else {}
        sanitized = {
            k: v
            for k, v in meta.items()
            if isinstance(v, (str, int, float, bool)) or v is None
        }
        recent_meta.append(
            {
                "event_type": item.event_type,
                "status": item.status,
                "meta": sanitized,
            }
        )

    ctx = {
        "now_utc": now.isoformat(),
        "quiet_hours": settings.proactive_quiet_hours,
        "offset_minutes": settings.proactive_timezone_offset_minutes,
        "proactive_enabled": bool(settings.proactive_enabled),
        "scheduler_enabled": bool(settings.proactive_scheduler_enabled),
        "recent_events": recent_meta,
        "event_type": getattr(event, "event_type", None),
        "priority": getattr(event, "priority", None),
    }
    return ctx


def needs_llm(event_type: str) -> bool:
    """A síntese via LLM é opt-in futuro; deterministic-first (Fase 17 = False).

    Nunca chamar um LLM por evento recebido sem sinalização explícita na
    manutenção futura deste retorno.
    """
    return False