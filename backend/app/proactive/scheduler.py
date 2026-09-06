"""Proactive Agent (Fase 17) — scheduler persistente (one-shot/interval/cron).

Agendamentos vivem em `proactive_schedules` no banco: o estado em memória é
sempre reconstruído do banco (recovery de restart). `fire_due` dispara apenas
schedules habilitados e vencidos, e recalcula `next_run_at` — nunca há thread
infinita: o worker do engine chama `tick()` no ciclo do FastAPI lifespan.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.models.proactive import ProactiveSchedule
from app.proactive import policy

logger = logging.getLogger("jarvis.proactive.scheduler")

_CRON_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_MAX_CATCHUP = timedelta(days=1)


def validate_spec(kind: str, spec: str) -> tuple[bool, str | None]:
    """Valida `(kind, spec)`; retorna (ok, erro|None)."""
    kind = (kind or "").lower().strip()
    if kind not in ("oneshot", "interval", "cron"):
        return False, "kind deve ser oneshot | interval | cron"
    spec = (spec or "").strip()
    if kind == "oneshot":
        from app.models.proactive import ProactiveSchedule  # noqa: F401

        try:
            datetime.fromisoformat(spec.replace("Z", "+00:00"))
        except ValueError:
            return False, "oneshot exige ISO timestamp UTC (ex.: 2030-01-01T12:00:00Z)"
    elif kind == "interval":
        try:
            if int(spec) <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return False, "interval exige segundos inteiros > 0"
    elif kind == "cron":
        if not _CRON_RE.match(spec):
            return False, "cron exige HH:MM (24h, ex.: 09:30)"
    return True, None


def local_now_utc(offset_minutes: int | None = None) -> datetime:
    return datetime.now(timezone.utc)


def _to_local(dt: datetime, offset_minutes: int) -> datetime:
    return dt + timedelta(minutes=offset_minutes)


def week_day_format(hour: int, minute: int) -> int:
    return hour * 60 + minute


def _parse_cron(spec: str) -> tuple[int, int]:
    match = _CRON_RE.match((spec or "").strip())
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def next_cron_run(
    spec: str,
    *,
    now_utc: datetime | None = None,
    offset_minutes: int | None = None,
) -> datetime:
    """Próximo horário `HH:MM` (local) ESTRITAMENTE no futuro, em UTC."""
    offset = (
        settings.proactive_timezone_offset_minutes
        if offset_minutes is None
        else offset_minutes
    )
    now = now_utc or datetime.now(timezone.utc)
    hour, minute = _parse_cron(spec)
    local_now = _to_local(now, offset)
    candidate = datetime(
        local_now.year, local_now.month, local_now.day, hour, minute,
        tzinfo=timezone.utc,
    )
    candidate = candidate - timedelta(minutes=offset)  # back to UTC
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def next_run_for(
    schedule: ProactiveSchedule,
    *,
    now_utc: datetime | None = None,
) -> datetime | None:
    """`next_run_at` para um schedule (None p/ one-shot já concluído)."""
    kind = (schedule.kind or "").lower().strip()
    spec = (schedule.spec or "").strip()
    ok, _err = validate_spec(kind, spec)
    if not ok:
        return None
    now = now_utc or datetime.now(timezone.utc)
    if kind == "oneshot":
        try:
            return datetime.fromisoformat(spec.replace("Z", "+00:00"))
        except ValueError:
            return None
    if kind == "interval":
        try:
            seconds = int(spec)
        except (ValueError, TypeError):
            return None
        return now + timedelta(seconds=seconds)
    if kind == "cron":
        return next_cron_run(spec, now_utc=now, offset_minutes=schedule.tz_offset_minutes)
    return None


def create_schedule(
    db,
    *,
    name: str,
    kind: str,
    spec: str,
    event_type: str = "proactive.scheduled",
    payload: dict[str, Any] | None = None,
    priority: str = "normal",
    enabled: bool = True,
    tz_offset_minutes: int | None = None,
) -> tuple[ProactiveSchedule | None, str | None]:
    """Cria um schedule persistente validado; retorna (row, erro|None)."""
    import json

    from app.proactive.events import is_valid_event_type, normalize_priority, sanitize_payload

    if not name or not name.strip():
        return None, "name é obrigatório"
    ok, err = validate_spec(kind, spec)
    if not ok:
        return None, err
    if not is_valid_event_type(event_type):
        return None, "event_type inválido (namespace.dotted)"
    offset = int(
        settings.proactive_timezone_offset_minutes
        if tz_offset_minutes is None
        else tz_offset_minutes
    )
    schedule = ProactiveSchedule(
        name=name.strip()[:120],
        kind=kind.lower().strip(),
        spec=spec.strip(),
        event_type=event_type,
        payload_json=json.dumps(
            sanitize_payload(payload or {}), ensure_ascii=False, default=str
        ),
        priority=normalize_priority(priority),
        enabled=bool(enabled),
        tz_offset_minutes=offset,
    )
    schedule.next_run_at = next_run_for(schedule)
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule, None


def update_schedule(
    db, schedule_id: str, *, fields: dict[str, Any]
) -> tuple[ProactiveSchedule | None, str | None]:
    """Atualiza campos editáveis e recalcula `next_run_at`."""
    from sqlalchemy import select

    schedule = db.scalars(
        select(ProactiveSchedule).where(ProactiveSchedule.id == schedule_id)
    ).first()
    if schedule is None:
        return None, "schedule não encontrado"

    kind = fields.get("kind", schedule.kind)
    spec = fields.get("spec", schedule.spec)
    ok, err = validate_spec(kind, spec)
    if not ok:
        return None, err
    if "event_type" in fields and fields["event_type"]:
        from app.proactive.events import is_valid_event_type

        if not is_valid_event_type(fields["event_type"]):
            return None, "event_type inválido (namespace.dotted)"

    for key in ("name", "kind", "spec", "event_type", "priority", "enabled"):
        if key in fields:
            if key == "priority" and fields[key]:
                from app.proactive.events import normalize_priority

                setattr(schedule, key, normalize_priority(fields[key]))
            elif key == "enabled":
                schedule.enabled = bool(fields[key])
            elif fields[key] is not None:
                setattr(schedule, key, fields[key].strip() if isinstance(fields[key], str) else fields[key])
    schedule.next_run_at = next_run_for(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule, None


def delete_schedule(db, schedule_id: str) -> bool:
    from sqlalchemy import select

    schedule = db.scalars(
        select(ProactiveSchedule).where(ProactiveSchedule.id == schedule_id)
    ).first()
    if schedule is None:
        return False
    db.delete(schedule)
    db.commit()
    return True


def list_schedules(db) -> list[ProactiveSchedule]:
    from sqlalchemy import select

    return list(
        db.scalars(
            select(ProactiveSchedule).order_by(ProactiveSchedule.created_at.desc())
        ).all()
    )


def fire_due(
    db,
    *,
    now_utc: datetime | None = None,
    max_fires: int = 20,
) -> int:
    """Dispara schedules habilitados vencidos (one-shot/interval/cron).

    Emite o evento do schedule pelo canal canônico (`emit`), atualiza
    `last_run_at`/`run_count` e recalcula `next_run_at`. One-shot concluído é
    desabilitado (não refira). Limite `max_fires` evita avalanches.
    """
    from sqlalchemy import select

    from app.proactive.events import emit

    if not settings.proactive_scheduler_enabled:
        return 0
    now = now_utc or datetime.now(timezone.utc)
    due = db.scalars(
        select(ProactiveSchedule)
        .where(ProactiveSchedule.enabled.is_(True))
        .where(ProactiveSchedule.next_run_at <= now)
        .limit(max_fires)
    ).all()
    if not due:
        return 0

    # Dois momentos: (1) muta os schedules e encerra a transação (`db.commit`);
    # (2) SO depois, emite os eventos com sessões próprias (sequencial). Isso
    # evita duas SessionLocal abertas na MESMA conexão in-memory (StaticPool).
    fired_payloads: list[tuple[str, str, dict]] = []
    for schedule in due:
        schedule.last_run_at = now
        schedule.run_count = schedule.run_count + 1
        payload = dict(schedule.payload())
        payload.setdefault("schedule_id", schedule.id)
        payload.setdefault("name", schedule.name)
        payload.setdefault("kind", schedule.kind)
        next_run = next_run_for(schedule, now_utc=now)
        if schedule.kind == "oneshot":
            schedule.enabled = False
            schedule.next_run_at = None
        else:
            schedule.next_run_at = next_run
        fired_payloads.append(
            (schedule.event_type or "proactive.scheduled", schedule.priority, payload)
        )
    fired = len(fired_payloads)
    db.commit()

    for event_type, priority, payload in fired_payloads:
        emit(
            event_type,
            source="jarvis",
            priority=priority,
            payload=payload,
        )
    return fired


def deferred_sweep(db, *, now_utc: datetime | None = None, max_rows: int = 50) -> int:
    """Reavalia entradas DEFER cujo `next_evaluation_at` venceu.

    (Quiet hours começando/término, cooldown expirado, teto reaberto.)
    """
    from sqlalchemy import select

    from app.models.proactive import ProactiveInboxEvent
    from app.proactive.engine import process_event

    if not settings.proactive_enabled:
        return 0
    now = now_utc or datetime.now(timezone.utc)
    rows = db.scalars(
        select(ProactiveInboxEvent)
        .where(ProactiveInboxEvent.decision == "defer")
        .where(ProactiveInboxEvent.delivered.is_(False))
        .where(
            (ProactiveInboxEvent.next_evaluation_at.is_(None))
            | (ProactiveInboxEvent.next_evaluation_at <= now)
        )
        .limit(max_rows)
    ).all()
    ids = [row.id for row in rows]
    if not ids:
        return 0
    # Encerra a transação de leitura ANTES de processar (cada `process_event`
    # usa sessão própria e sequencial; nunca duas abertas na mesma conexão).
    db.commit()
    for row_id in ids:
        process_event(row_id)
    return len(ids)