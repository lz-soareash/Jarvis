"""Proactive Agent (Fase 17) — API dos schedules/status + stream SSE.

Endpoints mínimos (status, CRUD de schedules, stream de mensagens proativas).
Mesmo padrão da Central: endpoints locais, dados sanitizados, mutações
auditadas e validadas (sem secrets, sem payload fora do esperado).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from app.db.session import get_db
from app.proactive.observer import proactive_stats
from app.proactive.scheduler import (
    create_schedule,
    delete_schedule,
    list_schedules,
    update_schedule,
)
from app.schemas.proactive import ScheduleCreate, ScheduleOut, ScheduleUpdate
from app.services.audit import log_action

router = APIRouter(prefix="/api/proactive", tags=["proactive"])


def _schedule_out(schedule) -> ScheduleOut:
    return ScheduleOut(
        id=schedule.id,
        name=schedule.name,
        kind=schedule.kind,
        spec=schedule.spec,
        event_type=schedule.event_type,
        priority=schedule.priority,
        enabled=schedule.enabled,
        last_run_at=schedule.last_run_at,
        next_run_at=schedule.next_run_at,
        run_count=schedule.run_count,
        created_at=schedule.created_at,
    )


@router.get("/status", response_model=dict)
def status(db: OrmSession = Depends(get_db)) -> dict:
    """Estado sanitizado da camada proativa (flags + contagens)."""
    return proactive_stats(db)


@router.get("/schedules", response_model=list[ScheduleOut])
def schedules(db: OrmSession = Depends(get_db)) -> list[ScheduleOut]:
    return [_schedule_out(s) for s in list_schedules(db)]


@router.post("/schedules", response_model=ScheduleOut, status_code=201)
def create_schedule_endpoint(
    body: ScheduleCreate, db: OrmSession = Depends(get_db)
) -> ScheduleOut:
    schedule, err = create_schedule(
        db,
        name=body.name,
        kind=body.kind,
        spec=body.spec,
        event_type=body.event_type,
        payload=body.payload,
        priority=body.priority,
        enabled=body.enabled,
        tz_offset_minutes=body.tz_offset_minutes,
    )
    if schedule is None:
        raise HTTPException(status_code=422, detail=err)
    log_action(
        db,
        action="proactive.schedule.create",
        allowed=True,
        detail=f"schedule {schedule.id} ({schedule.kind})",
    )
    return _schedule_out(schedule)


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
def update_schedule_endpoint(
    schedule_id: str, body: ScheduleUpdate, db: OrmSession = Depends(get_db)
) -> ScheduleOut:
    fields: dict[str, Any] = body.model_dump(exclude_unset=True)
    schedule, err = update_schedule(db, schedule_id, fields=fields)
    if schedule is None:
        raise HTTPException(status_code=404, detail=err or "schedule não encontrado")
    log_action(
        db,
        action="proactive.schedule.update",
        allowed=True,
        detail=f"schedule {schedule.id}",
    )
    return _schedule_out(schedule)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule_endpoint(schedule_id: str, db: OrmSession = Depends(get_db)) -> None:
    ok = delete_schedule(db, schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="schedule não encontrado")
    log_action(
        db,
        action="proactive.schedule.delete",
        allowed=True,
        detail=f"schedule {schedule_id}",
    )


@router.get("/stream")
async def proactive_stream() -> StreamingResponse:
    """SSE de entradas proativas (replay + ao vivo) — mesmo formato remoto."""
    from app.proactive.events import proactive_event_stream
    from app.services.chat import sse_event

    async def gen():
        async for entry in proactive_event_stream():
            yield sse_event(entry)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )