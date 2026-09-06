"""Proactive Agent (Fase 17) — policy engine (gates determinísticos).

Prioridades LOW/NORMAL/HIGH/CRITICAL, janela de silêncio configurável
(default 22:00-07:00 no fuso local), cooldown por tipo de evento e tetos
horário/diário — tudo conservador e reutilizando `RateLimiter` (camada remota).

Nada aqui autoriza ação automatizada além dos limites do Permission Engine:
a política apenas decide *quando* um evento pode virar notificação/ão, e o
`decision` module é quem decide *o quê* (nunca escape de permissão).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.remote.ratelimit import RateLimiter

logger = logging.getLogger("jarvis.proactive.policy")

PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
PRIORITY_CRITICAL = "critical"


@dataclass
class GateResult:
    """Resultado dos gates de política: permissão de seguir + motivo/código."""

    allowed: bool
    reason: str | None = None  # disabled | quiet_hours | cooldown | rate_limit
    defer_for: int = 0  # segundos sugeridos até reavaliar (DEFER)
    non_deferrable: bool = False  # IGNORE (não reavaliar): disabled apenas


def parse_quiet_hours(quiet: str, *, default: str = "22:00-07:00") -> tuple[int, int]:
    """Lê "HH:MM-HH:MM" → (início, fim) em minutos desde 00:00 local."""
    raw = (quiet or "").strip()
    if "-" not in raw:
        raw = default
    try:
        start_txt, end_txt = raw.split("-", 1)
        start_h, start_m = start_txt.split(":", 1)
        end_h, end_m = end_txt.split(":", 1)
        return int(start_h) * 60 + int(start_m), int(end_h) * 60 + int(end_m)
    except (ValueError, AttributeError):
        start_txt, end_txt = default.split("-", 1)
        start_h, start_m = start_txt.split(":", 1)
        end_h, end_m = end_txt.split(":", 1)
        return int(start_h) * 60 + int(start_m), int(end_h) * 60 + int(end_m)


def local_minutes(
    now_utc: datetime, *, offset_minutes: int | None = None
) -> int:
    """Minutos do dia no fuso local (`offset_minutes`; default do settings)."""
    offset = (
        settings.proactive_timezone_offset_minutes
        if offset_minutes is None
        else offset_minutes
    )
    base = now_utc.hour * 60 + now_utc.minute + offset
    return base % 1440


def in_quiet_hours(
    now_utc: datetime,
    *,
    quiet_hours: str | None = None,
    offset_minutes: int | None = None,
) -> bool:
    """Verdadeiro se `now_utc`, no fuso local, estiver dentro da janela de
    silêncio (configurada em `settings.proactive_quiet_hours`)."""
    qh = quiet_hours if quiet_hours is not None else settings.proactive_quiet_hours
    start, end = parse_quiet_hours(qh)
    minute = local_minutes(now_utc, offset_minutes=offset_minutes)
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end  # cruzou meia-noite


def quiet_end_seconds(now_utc: datetime, *, offset_minutes: int | None = None) -> int:
    """Segundos até o fim da janela de silêncio (teto ~24h)."""
    qh = settings.proactive_quiet_hours
    start, end = parse_quiet_hours(qh)
    minute = local_minutes(now_utc, offset_minutes=offset_minutes)
    if start < end:
        remaining = (end - minute) % 1440
    else:
        # cruzou meia-noite: se ainda antes da meia-noite, falta até 1440,
        # senão até `end`.
        remaining = (end - minute) % 1440
        if remaining == 0 and minute >= start:
            remaining = 0
    if remaining == 0:
        remaining = 1440
    return remaining * 60


# -- cooldown por tipo (estado em memória, resetável em testes) ---------------
_lock = threading.Lock()
_last_delivered: dict[str, float] = {}
_hourly: RateLimiter | None = None
_daily: RateLimiter | None = None


def _build_limiters() -> None:
    global _hourly, _daily
    per_hour = max(1, int(settings.proactive_max_per_hour))
    per_day = max(1, int(settings.proactive_max_per_day))
    _hourly = RateLimiter(per_hour, per_hour / 3600.0)
    _daily = RateLimiter(per_day, per_day / 86400.0)


def _ensure_limiters() -> None:
    global _hourly, _daily
    if _hourly is None or _daily is None:
        _build_limiters()


def reset_policy_state() -> None:
    """Zera cooldowns e rate limiters (hermeticidade de testes/restart)."""
    global _last_delivered
    with _lock:
        _last_delivered = {}
    _build_limiters()


def note_delivered(event_type: str) -> None:
    """Registra que um tipo de evento notificou agora (base p/ cooldown)."""
    with _lock:
        _last_delivered[event_type] = time.monotonic()


def seconds_since_last(event_type: str) -> float | None:
    with _lock:
        last = _last_delivered.get(event_type)
    return None if last is None else time.monotonic() - last


def _critical_interrupts(priority: str) -> bool:
    return priority == PRIORITY_CRITICAL and bool(settings.proactive_interrupt_on_critical)


def policy_gate(
    event,
    *,
    now_utc: datetime | None = None,
    offset_minutes: int | None = None,
) -> GateResult:
    """Aplica os gates de política a um `ProactiveEvent` (ou inbox row).

    Ordem fixa, tudo determinístico: enabled → quiet hours → cooldown → rate.
    Reason distinctions permitem metrics (`cooldown_hits`, `rate_limit_hits`).
    """
    _ensure_limiters()
    now = now_utc or datetime.now(timezone.utc)
    priority = getattr(event, "priority", PRIORITY_NORMAL) or PRIORITY_NORMAL

    if not settings.proactive_enabled:
        return GateResult(False, reason="disabled", non_deferrable=True)

    if in_quiet_hours(now, offset_minutes=offset_minutes) and not _critical_interrupts(
        priority
    ):
        return GateResult(False, reason="quiet_hours", defer_for=quiet_end_seconds(now))

    cooldown = max(0, int(settings.proactive_default_cooldown_seconds))
    last = seconds_since_last(getattr(event, "event_type", ""))
    if cooldown > 0 and last is not None and last < cooldown and not _critical_interrupts(priority):
        remaining = int(cooldown - last)
        return GateResult(False, reason="cooldown", defer_for=remaining)

    if not (_hourly.allow() and _daily.allow()):
        return GateResult(False, reason="rate_limit", defer_for=settings.proactive_tick_seconds)

    return GateResult(True)