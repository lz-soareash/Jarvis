"""Observabilidade do Computer Agent (Fase 19) — eventos `computer.task.*`.

Registra eventos estruturados via `ops.record_event` (persistente, Central de
Operações), auditoria via `audit.log_action` e, quando o remoto está habilitado,
publica no RemoteEventBroker (SSE móvel/web) reutilizando `publish_event`.

NUNCA registra: screenshots brutos, texto digitado, tokens, credenciais.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.services import audit as audit_service
from app.services import ops as ops_service

logger = logging.getLogger("jarvis.computer_agent")

EVENT_TASK_CREATED = "computer.task.created"
EVENT_TASK_STARTED = "computer.task.started"
EVENT_TASK_PLANNED = "computer.task.planned"
EVENT_OBSERVATION_RECEIVED = "computer.observation.received"
EVENT_ACTION_REQUESTED = "computer.action.requested"
EVENT_ACTION_EXECUTED = "computer.action.executed"
EVENT_ACTION_FAILED = "computer.action.failed"
EVENT_VERIFICATION_STARTED = "computer.verification.started"
EVENT_VERIFICATION_SUCCESS = "computer.verification.success"
EVENT_VERIFICATION_FAILED = "computer.verification.failed"
EVENT_RECOVERY_STARTED = "computer.recovery.started"
EVENT_RECOVERY_COMPLETED = "computer.recovery.completed"
EVENT_TASK_COMPLETED = "computer.task.completed"
EVENT_TASK_FAILED = "computer.task.failed"
EVENT_TASK_CANCELLED = "computer.task.cancelled"
EVENT_WAITING_CONFIRMATION = "computer.task.waiting_confirmation"
EVENT_LOOP_PREVENTED = "computer.loop.prevented"
EVENT_INJECTION_BLOCKED = "computer.security.prompt_injection"
EVENT_AUTONOMY_BLOCKED = "computer.security.autonomy_blocked"


def emit(db, *, event_type: str, session_id: str | None = None,
         status: str | None = None, meta: dict | None = None) -> None:
    """Registra evento de observabilidade + audit + broker (se remoto)."""
    try:
        ops_service.record_event(
            db,
            session_id=session_id,
            event_type=event_type,
            status=status,
            meta=ops_service.json_safe_meta(**(meta or {})),
        )
    except Exception:  # noqa: BLE001
        logger.debug("Falha ao registrar evento %s", event_type, exc_info=True)

    try:
        if settings.remote_enabled:
            from app.remote.events import publish_event
            data = dict(meta or {})
            data["event"] = event_type
            publish_event(event_type, data)
    except Exception:  # noqa: BLE001
        logger.debug("Falha ao publicar evento remoto %s", event_type, exc_info=True)


def audit(db, *, task_id: str, action: str, allowed: bool | None = None,
          detail: str = "") -> None:
    """Auditoria sanitizada (nunca texto sensível)."""
    try:
        audit_service.log_action(
            db,
            action=action,
            tool="computer_agent",
            allowed=allowed,
            detail=f"{task_id} {detail}"[:1200],
        )
    except Exception:  # noqa: BLE001
        logger.debug("Falha ao auditar %s", action, exc_info=True)