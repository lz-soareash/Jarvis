"""Observabilidade e Central de Operações (Fase 11).

Responsável por persistir/compartilhar `ExecutionEvent` (sanitizado — sem
conteúdo de mensagens, tokens ou secrets) e por agregar o *overview* que a
Central de Operações exibe: AI Core, provedores, memória, tarefas, tools, Atlas
e sistema. Eventos são a fonte estruturada; logs em arquivo ficam só p/ debug.
"""

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.models import ApprovalRequest, AuditLog, ExecutionEvent, Memory
from app.schemas.ops import (
    ExecutionEventOut,
    OpsAICore,
    OpsOverview,
    OpsProvider,
)

logger = logging.getLogger("jarvis.ops")

# Vocabulário estável de eventos do ciclo de IA.
EVENT_STARTED = "chat.started"
EVENT_PROVIDER = "provider.selected"
EVENT_PATH = "path.selected"
EVENT_COMPLETED = "chat.completed"
EVENT_FAILED = "chat.failed"


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def record_event(
    db: OrmSession,
    *,
    session_id: str | None = None,
    event_type: str,
    provider: str | None = None,
    model: str | None = None,
    status: str | None = None,
    latency_ms: int | None = None,
    meta: dict | None = None,
) -> ExecutionEvent:
    """Grava um evento observável (meta já deve vir sanitizada)."""
    event = ExecutionEvent(
        session_id=session_id,
        event_type=event_type,
        provider=provider,
        model=model,
        status=status,
        latency_ms=latency_ms,
        meta_json=json.dumps(meta, ensure_ascii=False, default=str) if meta else None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def list_events(
    db: OrmSession,
    *,
    session_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[ExecutionEvent]:
    stmt = select(ExecutionEvent).order_by(ExecutionEvent.id.desc()).limit(limit)
    if session_id is not None:
        stmt = select(ExecutionEvent).where(
            ExecutionEvent.session_id == session_id
        ).order_by(ExecutionEvent.id.desc()).limit(limit)
    if event_type is not None:
        stmt = stmt.where(ExecutionEvent.event_type == event_type)
    return list(db.scalars(stmt).all())


def to_out(event: ExecutionEvent) -> ExecutionEventOut:
    meta = None
    if event.meta_json:
        try:
            meta = json.loads(event.meta_json)
        except (ValueError, TypeError):
            meta = None
    return ExecutionEventOut(
        id=event.id,
        session_id=event.session_id,
        event_type=event.event_type,
        provider=event.provider,
        model=event.model,
        status=event.status,
        latency_ms=event.latency_ms,
        meta=meta,
        created_at=event.created_at,
    )


# ---------------------------------------------------------------------------
# Agregação (Central de Operações)
# ---------------------------------------------------------------------------

def _memory_stats(db: OrmSession) -> dict:
    total = db.scalar(select(func.count(Memory.id))) or 0
    kinds = {}
    for row in db.execute(select(Memory.kind, func.count(Memory.id)).group_by(Memory.kind)):
        kinds[row[0]] = row[1]
    recent = [
        {"content": m.content, "kind": m.kind, "created_at": m.created_at.isoformat()}
        for m in db.scalars(
            select(Memory).order_by(Memory.created_at.desc()).limit(5)
        ).all()
    ]
    return {"total": total, "by_kind": kinds, "recent": recent}


def _tasks_stats(db: OrmSession) -> dict:
    pending = db.scalar(
        select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == "pending")
    ) or 0
    approved = db.scalar(
        select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == "approved")
    ) or 0
    denied = db.scalar(
        select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == "denied")
    ) or 0
    executed = [
        {"action": a.action, "tool": a.tool, "allowed": a.allowed}
        for a in db.scalars(
            select(AuditLog).order_by(AuditLog.id.desc()).limit(6)
        ).all()
    ]
    return {
        "pending_approvals": pending,
        "approved": approved,
        "denied": denied,
        "recent_executions": executed,
    }


def _tools_stats() -> dict:
    from app.tools.registry import get_tool_registry

    tools = get_tool_registry().all()
    by_level: dict[str, int] = {}
    for tool in tools:
        level = str(tool.permission_level.value)
        by_level[level] = by_level.get(level, 0) + 1
    return {
        "total": len(tools),
        "by_permission": by_level,
        "tools": [t.name for t in tools],
    }


def _atlas_stats() -> dict:
    from app.services.atlas_router import should_route_to_atlas

    enabled = bool(settings.atlas_enabled)
    configured = should_route_to_atlas()
    return {
        "enabled": enabled,
        "configured": configured,
        "base_url": settings.atlas_base_url if enabled else None,
        "detail": (
            "pronto para roteamento"
            if configured
            else (
                "habilitado mas sem credenciais (ATLAS_EMAIL/ATLAS_PASSWORD)"
                if enabled
                else "desabilitado (ATLAS_ENABLED=false)"
            )
        ),
    }


def _system_stats() -> dict:
    from app.db.session import check_database

    return {"db": check_database(), "version": settings.version}


def build_overview(db: OrmSession) -> OpsOverview:
    """Agrega o quadro atual do JARVIS para a Central de Operações (sem rede)."""
    from app.ai.registry import get_ai_router

    router = get_ai_router()
    primary = router.primary(task="generate")

    # Fallback ativo conforme o último evento provider.selected.
    fallback_active = False
    last = list_events(db, event_type=EVENT_PROVIDER, limit=1)
    if last:
        fallback_active = last[0].status == "fallback"

    return OpsOverview(
        ai_core=OpsAICore(
            active_provider=primary.name if primary else None,
            active_model=getattr(primary, "model", None) if primary else None,
            fallback_active=fallback_active,
            router_ready=bool(router.configured()),
        ),
        providers=[_to_provider_out(p) for p in router.statuses()],
        memory=_memory_stats(db),
        tasks=_tasks_stats(db),
        tools=_tools_stats(),
        atlas=_atlas_stats(),
        system=_system_stats(),
        recent_events=[to_out(e) for e in list_events(db, limit=20)],
    )


def _to_provider_out(status) -> OpsProvider:
    return OpsProvider(
        name=status.provider,
        model=status.model,
        configured=status.status == "ok",
        status=status.status,
    )


def json_safe_meta(**kwargs: Any) -> dict:
    """Ajuda a montar `meta` sanitizado: remove `None` e chaves vazias."""
    return {k: v for k, v in kwargs.items() if v is not None and v != ""}


def sanitized_timestamp(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None