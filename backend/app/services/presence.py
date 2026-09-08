"""Presença da VEGA (Fase 19.5) — estados REAIS, nunca fingidos.

O estado de presença é DERIVADO de sinais reais persistidos no banco/processo
(eventos de IA, tarefas agênticas, aprovações pendentes, Computer Agent) — nenhum
estado é decorativo ou inventado. Prioridade de resolução:

    offline > error > waiting_confirmation > working > thinking > idle

Quando o componente opcional que alimenta um estado não existe (ex.: voz ativa,
observação em andamento), o estado correspondente NÃO é emitido. `vega.state.changed`
é registrado apenas em TRANSIÇÃO de estado (rate-limited, sem poluir eventos).
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings

logger = logging.getLogger("jarvis.presence")

# Janelas de observação de sinais reais.
_ERROR_WINDOW_SECONDS = 90  # um erro recente conta como estado "error"
_ACTIVITY_WINDOW_SECONDS = 60  # tarefa/evento ativo conta como "working/thinking"
_WAIT_WINDOW_SECONDS = 600  # aprovação pendente recente conta como "waiting"

_STATE_TRANSITION_EVENT = "vega.state.changed"

# Vocabulário estável de presença (apenas os com fonte real de sinal são emitidos).
valid_states = (
    "offline",
    "error",
    "waiting_confirmation",
    "working",
    "thinking",
    "idle",
)

_LABELS = {
    "offline": "off-line",
    "error": "falha recente",
    "waiting_confirmation": "aguardando confirmação",
    "working": "trabalhando",
    "thinking": "processando",
    "idle": "ociosa",
}

_transitions_cached: dict = {"state": None, "recorded_at": 0.0}


def valid_labels() -> dict[str, str]:
    return dict(_LABELS)


def compute_presence(db: OrmSession) -> dict:
    """Deriva o estado de presença corrente a partir de sinais reais.

    Sempre retorna campos estáveis: `name`, `state`, `label`, `online`,
    `detail`, `since`. NUNCA retorna estado que não tem subsídio real.
    """
    now = datetime.now(timezone.utc)

    try:
        db.connection()
        online = True
    except Exception:  # noqa: BLE001 — banco indisponível => offline
        online = False
        return _presence("offline", "banco de dados indisponível", online=online, now=now)

    state, detail, since = _derive(db, now)

    _maybe_record_transition(db, state)

    return _presence(state, detail, online=True, now=now, since=since)


# ---------------------------------------------------------------------------
# Derivação (sinais reais)
# ---------------------------------------------------------------------------

def _derive(db: OrmSession, now: datetime) -> tuple[str, str, str | None]:
    """Decide o estado por sinais reais, na ordem de prioridade."""
    from app.models import AgentTask, ApprovalRequest, ExecutionEvent, Memory

    now_utc = now

    # 1) Erro recente (chat/task falhou nos últimos 90s).
    recent_error = _first_event_after(
        db,
        event_types=("chat.failed", "agent.task.failed", "computer.task.failed"),
        window=_ERROR_WINDOW_SECONDS,
        now=now_utc,
    )
    if recent_error is not None:
        return "error", "falha recente", _iso(recent_error.created_at)

    # 2) Aprovação pendente recente == aguardando decisão do usuário.
    if _has_recent_pending_approval(db, now_utc):
        return "waiting_confirmation", "requer confirmação do usuário", None

    # 3) Tarefa agêntica ativa (plano em andamento).
    if _has_active_agent_task(db):
        return "working", "tarefa em execução", None

    # 4) Computer Agent com tarefa ativa (estado real da fase 19).
    active = _computer_agent_active_state()
    if active is not None:
        label = "computador em observação" if active == "observing" else "agindo no computador"
        return "working", label, None

    # 5) Turno de chat em processamento (started recente sem completion).
    if _has_open_chat_turn(db, now_utc):
        return "thinking", "processando mensagem", None

    return "idle", "—", None


def _first_event_after(
    db: OrmSession, *, event_types: tuple[str, ...], window: int, now: datetime
) -> "ExecutionEvent | None":
    from app.models import ExecutionEvent

    oldest = now - timedelta(seconds=window)
    stmt = (
        select(ExecutionEvent)
        .where(ExecutionEvent.event_type.in_(event_types))
        .order_by(ExecutionEvent.created_at.desc())
        .limit(1)
    )
    for row in db.scalars(stmt).all():
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= oldest:
            return row
    return None


def _has_recent_pending_approval(db: OrmSession, now: datetime) -> bool:
    from app.models import ApprovalRequest

    oldest = now - timedelta(seconds=_WAIT_WINDOW_SECONDS)
    stmt = select(ApprovalRequest).where(ApprovalRequest.status == "pending")
    for row in db.scalars(stmt).all():
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= oldest:
            return True
    return False


def _has_active_agent_task(db: OrmSession) -> bool:
    from app.models import AgentTask

    stmt = select(AgentTask.id).where(AgentTask.status.in_(("planned", "running"))).limit(1)
    return db.scalar(stmt) is not None


def _computer_agent_active_state() -> str | None:
    """Estado real de uma tarefa ativa do Computer Agent (process-local)."""
    try:
        from app.computer_agent import models
        from app.computer_agent.store import get_store

        active = {
            models.ComputerStatus.PLANNING,
            models.ComputerStatus.PERCEIVING,
            models.ComputerStatus.EXECUTING,
            models.ComputerStatus.OBSERVING,
            models.ComputerStatus.VERIFYING,
            models.ComputerStatus.RECOVERING,
        }
        for task in get_store().list(100):
            if task.status in active:
                return "observing" if task.status == models.ComputerStatus.OBSERVING else task.status.value
    except Exception:  # noqa: BLE001 — ausência do agente não quebra a presença
        return None
    return None


def _has_open_chat_turn(db: OrmSession, now: datetime) -> bool:
    """`chat.started` recente que ainda não foi fechado por `chat.completed`."""
    from app.models import ExecutionEvent

    oldest = now - timedelta(seconds=_ACTIVITY_WINDOW_SECONDS)
    stmt = (
        select(ExecutionEvent)
        .where(ExecutionEvent.event_type == "chat.started")
        .order_by(ExecutionEvent.created_at.desc())
        .limit(1)
    )
    started = db.scalars(stmt).first()
    if started is None:
        return False
    created = started.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created < oldest:
        return False
    completed_stmt = (
        select(ExecutionEvent.id)
        .where(ExecutionEvent.event_type == "chat.completed")
        .order_by(ExecutionEvent.created_at.desc())
        .limit(1)
    )
    completed = db.scalars(completed_stmt).first()
    return completed is None


# ---------------------------------------------------------------------------
# Emissão
# ---------------------------------------------------------------------------

def _presence(
    state: str,
    detail: str,
    *,
    online: bool,
    now: datetime,
    since: str | None = None,
) -> dict:
    return {
        "name": (settings.assistant_name or "VEGA").strip() or "VEGA",
        "state": state if state in valid_states else "idle",
        "label": _LABELS.get(state, state),
        "online": online,
        "detail": detail,
        "since": since,
        "checked_at": _iso(now),
    }


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat()


def _maybe_record_transition(db: OrmSession, state: str) -> None:
    """Registra `vega.state.changed` apenas em transição (rate-limited p/ processo)."""
    from app.services import ops

    if not bool(settings.vega_presence_enabled):
        return
    now_s = time.monotonic()
    if state == _transitions_cached["state"]:
        return
    if _transitions_cached["recorded_at"] and now_s - _transitions_cached["recorded_at"] < 2.0:
        return
    _transitions_cached["state"] = state
    _transitions_cached["recorded_at"] = now_s
    try:
        ops.record_event(
            db,
            session_id=None,
            event_type=_STATE_TRANSITION_EVENT,
            status=state,
            meta=ops.json_safe_meta(component="vega.presence"),
        )
    except Exception:  # noqa: BLE001 — observabilidade é best-effort
        logger.debug("Não foi possível registrar transição de presença")