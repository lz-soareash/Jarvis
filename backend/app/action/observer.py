"""Computer Action Layer (Fase 18) — observador para a Central de Operações.

Agrega estatísticas sanitizadas da camada de ações (estado in-memory +
eventos persistidos + adapter real). Só contagens e metadata — nunca
payloads, textos ou combinhações de teclas completas.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.models import ExecutionEvent
from app.services import ops as ops_service

from .adapters import get_adapter
from .state import get_action_state

EVENT_ACTION_REQUESTED = "computer.action.requested"
EVENT_ACTION_ACCEPTED = "computer.action.accepted"
EVENT_ACTION_EXECUTED = "computer.action.executed"
EVENT_ACTION_FAILED = "computer.action.failed"
EVENT_ACTION_REJECTED = "computer.action.rejected"
EVENT_ACTION_TIMEOUT = "computer.action.timeout"
EVENT_ACTION_CANCELLED = "computer.action.cancelled"
EVENT_ACTION_UNAVAILABLE = "computer.action.unavailable"
EVENT_ACTION_DRY_RUN = "computer.action.dry_run"

ACTION_EVENT_TYPES: tuple[str, ...] = (
    EVENT_ACTION_REQUESTED,
    EVENT_ACTION_ACCEPTED,
    EVENT_ACTION_EXECUTED,
    EVENT_ACTION_FAILED,
    EVENT_ACTION_REJECTED,
    EVENT_ACTION_TIMEOUT,
    EVENT_ACTION_CANCELLED,
    EVENT_ACTION_UNAVAILABLE,
    EVENT_ACTION_DRY_RUN,
)


def action_stats(db: OrmSession) -> dict[str, Any]:
    """Estatísticas da Computer Action Layer para o overview da Central."""
    enabled = bool(settings.computer_actions_enabled)
    snap = get_action_state().snapshot()

    adapter = get_adapter()
    caps = None
    adapter_name = adapter.name
    adapter_available = adapter.available
    try:
        caps = adapter.discover_capabilities()
    except Exception:  # noqa: BLE001
        caps = None

    executed = _count_events(db, EVENT_ACTION_EXECUTED)
    failed = _count_events(db, EVENT_ACTION_FAILED)
    rejected = _count_events(db, EVENT_ACTION_REJECTED)
    timeout = _count_events(db, EVENT_ACTION_TIMEOUT)
    cancelled = _count_events(db, EVENT_ACTION_CANCELLED)
    dry_run = _count_events(db, EVENT_ACTION_DRY_RUN)

    return {
        "enabled": enabled,
        "adapter": {"name": adapter_name, "available": adapter_available},
        "capabilities": caps.to_dict() if caps else None,
        "actions_total": snap.get("history_count", 0),
        "actions_by_type": snap.get("counts_by_type", {}),
        "actions_by_status": snap.get("counts_by_status", {}),
        "actions_by_source": snap.get("counts_by_source", {}),
        "rate_limited_count": snap.get("rate_limited_count", 0),
        "events": {
            "executed": executed,
            "failed": failed,
            "rejected": rejected,
            "timeout": timeout,
            "cancelled": cancelled,
            "dry_run": dry_run,
        },
        "last_actions": snap.get("last_actions", []),
    }


def _count_events(db: OrmSession, event_type: str) -> int:
    return db.scalar(
        select(func.count(ExecutionEvent.id)).where(ExecutionEvent.event_type == event_type)
    ) or 0


def emit_action_event(
    db: OrmSession,
    *,
    session_id: str | None,
    event_type: str,
    action_type: str,
    status: str,
    dry_run: bool = False,
    error_code: str | None = None,
    adapter: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Emite evento observável de ação (meta sanitizada — nunca payloads)."""
    ops_service.record_event(
        db,
        session_id=session_id,
        event_type=event_type,
        status=status,
        latency_ms=duration_ms,
        meta=ops_service.json_safe_meta(
            action_type=action_type,
            dry_run=dry_run,
            error_code=error_code,
            adapter=adapter,
        ),
    )