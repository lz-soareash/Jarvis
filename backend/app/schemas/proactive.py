"""Schemas da camada Proactive (Fase 17) — contrato sanitizado da API."""

from datetime import datetime

from app.schemas.base import APIModel


class ScheduleCreate(APIModel):
    name: str
    kind: str  # oneshot | interval | cron
    spec: str
    event_type: str = "proactive.scheduled"
    payload: dict | None = None
    priority: str = "normal"
    enabled: bool = True
    tz_offset_minutes: int | None = None


class ScheduleUpdate(APIModel):
    name: str | None = None
    kind: str | None = None
    spec: str | None = None
    event_type: str | None = None
    payload: dict | None = None
    priority: str | None = None
    enabled: bool | None = None


class ScheduleOut(APIModel):
    id: str
    name: str
    kind: str
    spec: str
    event_type: str
    priority: str
    enabled: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    run_count: int = 0
    created_at: datetime