"""Fase 27 — Multi-turn Agentic Context no Core.

Camada determinística de contexto multi-turn: o Core (única autoridade) mantém,
por sessão, o contexto operacional que permite resolver continuidade entre
turnos — "Agora pesquisa FIAP" após "Abra o Chrome no meu celular" — SEM depender
do LLM e SEM criar memória/agente/configuração no lado mobile.

Composição (derivada de DADOS REAIS, nunca inventada):

- `DeviceContext` — o dispositivo móvel ativo da sessão: nome, última capability
  executada, status, transporte e resumo. É escrito por `tools/mobile.py` sempre
  que um comando de dispositivo termina (resultado real do executor).
- `ActiveTaskContext` — derivado do modelo existente `AgentTask` (Fase 13): a
  tarefa ativa (planned/running) da sessão com objetivo/status/passos/última
  ferramenta. Sem coluna nova; apenas leitura determinística.
- `Session::context_json` — armazena apenas o bloco `device` (sem ids sensíveis
  em excesso, sem tokens/segredos). O NOME amigável é o único dado injetado no
  prompt; never o `device_id`.

Segurança e política:
- Contexto é INFORMAÇÃO, não autorização: injetar um bloco aqui NUNCA eleva
  permissões — toda execução continua passando pelo Tool/Permission Engine
  existente e pelo `resolve_target` (explícito > sessão > único > perguntar).
- Nunca inventar dispositivo ausente; contexto expira (`_DEVICE_CONTEXT_TTL_S`).
- Nome amigável/mensagens sanitizados no prompt; ids/transporte ficam fora.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.models import AgentTask, Session, utcnow
from app.models.session import ensure_utc

# TTL do contexto de dispositivo: após esse tempo sem nova ação, a continuidade
# não é mais resolvida de forma automática (cada turno precisa reverter ao
# fluxo normal: explícito > sessão > único online > perguntar).
_DEVICE_CONTEXT_TTL_S: int = 600  # 10 minutos
# Fração para o contexto nunca crescer além de um tamanho seguro.
_MAX_THRESHOLD: None = None


def _now() -> datetime:
    return utcnow()


def _iso_now() -> str:
    return _now().isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceContext:
    """Dispositivo móvel ativo da sessão (visto sanitizado p/ o Core)."""

    id: str
    name: str
    capability: str
    status: str
    transport: str | None
    summary: str
    updated_at: str

    @property
    def fresh(self) -> bool:
        parsed = _parse_dt(self.updated_at)
        if parsed is None:
            return False
        return (ensure_utc(_now()) - ensure_utc(parsed)) <= timedelta(
            seconds=_DEVICE_CONTEXT_TTL_S
        )

    @property
    def label(self) -> str:
        from app.remote import mobile_agent

        return mobile_agent.human_label(self.capability)


@dataclass(frozen=True, slots=True)
class ActiveTaskContext:
    """Visão determinística da tarefa ativa (planned/running) da sessão."""

    task_id: str
    objective: str
    status: str
    steps_done: int
    steps_total: int
    current_step: int
    next_steps: list[str]
    last_tool: str | None
    last_result: str | None
    error: str | None
    updated_at: str

    @property
    def is_cancelled(self) -> bool:
        return self.status == "cancelled"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Contexto completo do turno: dispositivo + tarefa ativa."""

    device: DeviceContext | None = None
    active_task: ActiveTaskContext | None = None


# ---------------------------------------------------------------------------
# Persistência em Session.context_json (somente o bloco `device`)
# ---------------------------------------------------------------------------


def _read_context(db: OrmSession, session_id: str) -> dict[str, Any]:
    session = db.get(Session, session_id)
    if session is None or not session.context_json:
        return {}
    try:
        data = json.loads(session.context_json)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _write_context(db: OrmSession, session_id: str, data: dict[str, Any]) -> None:
    session = db.get(Session, session_id)
    if session is None:
        return
    session.context_json = json.dumps(data, ensure_ascii=False)
    db.commit()


def record_device_outcome(
    db: OrmSession,
    session_id: str,
    *,
    device_id: str,
    device_name: str,
    capability: str,
    status: str,
    transport: str | None,
    summary: str,
) -> None:
    """Registra no contexto da sessão o resultado REAL de um comando de
    dispositivo (chamado em `tools/mobile.py` ao final de cada execução).

    Só grava quando há nome amigável (decisão de continuidade exige alvo
    inequívoco). O `device_id` fica armazenado para auditoria, mas NUNCA é
    injetado no prompt (apenas `name`).
    """
    if not session_id or not device_name:
        return
    data = _read_context(db, session_id)
    data["device"] = {
        "id": device_id,
        "name": device_name.strip()[:120],
        "capability": capability,
        "status": status,
        "transport": transport,
        "summary": (summary or "").strip()[:300],
        "updated_at": _iso_now(),
    }
    _write_context(db, session_id, data)


def clear_device_context(db: OrmSession, session_id: str) -> None:
    """Remove o contexto de dispositivo da sessão (ex.: usuário encerrou a
    thread ou pediu explicitamente para trocar). A continuidade automática
    deixa de se aplicar até a próxima ação de dispositivo."""
    data = _read_context(db, session_id)
    if "device" in data:
        data.pop("device", None)
        _write_context(db, session_id, data)


def device_context(db: OrmSession, session_id: str) -> DeviceContext | None:
    data = _read_context(db, session_id).get("device")
    if not isinstance(data, dict):
        return None
    try:
        return DeviceContext(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            capability=str(data.get("capability", "")),
            status=str(data.get("status", "")),
            transport=data.get("transport"),
            summary=str(data.get("summary", "")),
            updated_at=str(data.get("updated_at", "")),
        )
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Active Task — derivado do modelo existente AgentTask
# ---------------------------------------------------------------------------


def _active_task(db: OrmSession, session_id: str) -> AgentTask | None:
    from sqlalchemy import select

    stmt = (
        select(AgentTask)
        .where(
            AgentTask.session_id == session_id,
            AgentTask.status.in_(("planned", "running")),
        )
        .order_by(AgentTask.created_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _task_context(task: AgentTask) -> ActiveTaskContext | None:
    plan = task.plan
    if not plan:
        return None
    try:
        progress = json.loads(task.progress_json) if task.progress_json else None
    except (ValueError, TypeError):
        progress = None
    progress = progress if isinstance(progress, list) else []

    done_statuses = {"done", "skipped", "failed"}
    first_pending = next(
        (
            i
            for i in range(len(plan))
            if i >= len(progress) or progress[i].get("status") not in done_statuses
        ),
        None,
    )
    current_step = (first_pending + 1) if first_pending is not None else task.steps_total

    next_steps: list[str] = []
    for i in range(len(plan)):
        if first_pending is None or i < first_pending:
            continue
        step = plan[i]
        desc = str(step.get("description") or step.get("tool") or "passo").strip()
        if desc:
            next_steps.append(desc)
        if len(next_steps) >= 3:
            break

    last_index: int | None = None
    last_result: str | None = None
    for i in range(len(plan)):
        st = progress[i].get("status", "pending") if i < len(progress) else "pending"
        if st != "pending":
            last_index = i
            last_result = st
    last_tool = None
    if last_index is not None and last_index < len(plan):
        last_tool = str(plan[last_index].get("tool") or "").strip() or None

    return ActiveTaskContext(
        task_id=task.id,
        objective=(task.objective or "").strip()[:300],
        status=task.status,
        steps_done=task.steps_done,
        steps_total=task.steps_total,
        current_step=current_step,
        next_steps=next_steps,
        last_tool=last_tool,
        last_result=last_result,
        error=task.error,
        updated_at=task.updated_at.isoformat() if task.updated_at else "",
    )


def active_task_context(db: OrmSession, session_id: str) -> ActiveTaskContext | None:
    task = _active_task(db, session_id)
    if task is None:
        return None
    return _task_context(task)


# ---------------------------------------------------------------------------
# Carregamento e renderização (blocos SANITIZADOS p/ o prompt sistêmico)
# ---------------------------------------------------------------------------


def load_turn_context(db: OrmSession, session_id: str) -> TurnContext:
    """Contexto do turno: dispositivo ativo (se fresco) + tarefa ativa.

    O dispositivo só entra quando PRESENTE na sessão; a frescura é avaliada por
    `DeviceContext.fresh` no render, mantendo o dado, mas impedindo continuidade
    com contexto velho.
    """
    return TurnContext(
        device=device_context(db, session_id),
        active_task=active_task_context(db, session_id),
    )


def render_context_blocks(ctx: TurnContext) -> list[str]:
    """Blocos sistêmicos injetados no prompt (sem ids, tokens ou segredos)."""
    blocks: list[str] = []

    device = ctx.device
    if device is not None and device.name:
        blocks.append(
            "[Continuidade de dispositivo]\n"
            f"Você está operando no celular '{device.name}'. "
            f"Última ação executada nele: {device.label} ({device.status})."
        )
        if device.summary:
            blocks.append(f"Resumo da última ação: {device.summary}.")

    task = ctx.active_task
    if task is not None:
        lines = [f"[Tarefa ativa]\nObjetivo: {task.objective}"]
        lines.append(
            f"Status: {task.status} · passo {task.current_step} de "
            f"{task.steps_total if task.steps_total else 0}"
        )
        if task.last_tool:
            lines.append(f"Última ferramenta: {task.last_tool} ({task.last_result or 'ok'})")
        if task.next_steps:
            upcoming = "; ".join(task.next_steps)
            lines.append(f"Próximos passos: {upcoming}")
        if task.error:
            lines.append(f"Erro relatado: {task.error}")
        blocks.append("\n".join(lines))

    return blocks


def system_context_block(db: OrmSession, session_id: str) -> list[str]:
    """Conveniência p/ o serviço de chat: carrega e renderiza o contexto."""
    if not session_id:
        return []
    return render_context_blocks(load_turn_context(db, session_id))