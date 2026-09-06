"""Agentic Core (Fase 13): camada de TAREFAS compostas sobre o Tool Engine.

Fluxo alvo (AGENT CORE→TASK→PLAN→EXECUTE→OBSERVE→VERIFY→
RECOVER/CONTINUE→COMPLETION→RESPONSE):

- `create_task` materializa a intenção do usuário (TASK).
- `plan_for` produz um plano determinístico de passos (PLAN) — sem depender de
  LLM para a espinha; cada passo declara tool + arguments + verificação.
- `run_agent_task` (SSE) executa cada passo pelo caminho já existente
  (Tool Registry → Permission Engine → AuditLog), observa e verifica
  (EXECUTE→OBSERVE→VERIFY), recupera em falhas recuperáveis (RECOVER/CONTINUE)
  e conclui (COMPLETION→RESPONSE).

Reuso (nada é reescrito): execução via `agent._run_tool` (mesmo fluxo do loop
conversacional e do Remote); permissão via `permissions.effective_level`;
auditoria via `audit.log_action`; aprovações via `approvals` (nível ≥ 2 pausa a
tarefa e retoma pelo mesmo ApprovalRequest). Fase 14: a Web Research é uma
CAPACIDADE do Core — passos `{"kind": "research"}` rodam pelo Research Agent
(Search Provider → Fetch SSRF → Síntese local-first) e as tools `web_search`/
`web_fetch` ficam disponíveis ao Modelo via Tool Registry.
"""

import json
import logging
import re
import time
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider
from app.core.config import settings
from app.core.enums import ApprovalStatus, PermissionLevel, StepStatus, TaskStatus
from app.models import AgentTask, utcnow
from app.schemas.ai import ToolCall
from app.services import agent as agent_loop
from app.services import approvals as approval_service
from app.services import audit as audit_service
from app.services import ops as ops_service
from app.services import permissions as permission_service
from app.services.chat import sse_event
from app.tools import ToolResult

logger = logging.getLogger("jarvis.agent_core")

# ---------------------------------------------------------------------------
# PLAN — planejamento determinístico (sem depender de geração de LLM p/ espinha)
# ---------------------------------------------------------------------------

_ANALYZE = re.compile(
    r"\b(?:analis|an[áa]lise|analisar|explor|investig|descrev|como\s+funciona"
    r"|entenda|entender|revise|revisar|resuma|resumir|diagn[óo]stico)\b",
    re.IGNORECASE,
)
_TESTS = re.compile(
    r"\b(?:teste|testes|testar|rodar\s+os\s+testes|valida[çc][ãa]o|verific"
    r"|checar|checagem)\b",
    re.IGNORECASE,
)
_CODE = re.compile(
    r"\b(?:corr[ií][g]|corre[çc][ãa]o|bug|implement|cri[ae]\s+(?:uma|um)"
    r"|adicione|adicionar|refator|feature|funcionalidade)\b",
    re.IGNORECASE,
)
# Fase 14 — Web Research como capacidade do Agentic Core (o passo é {"kind": "research"}).
_RESEARCH = re.compile(
    r"\b(?:pesquisar|pesquisa|buscar\s+na?\s+web|pesquise|investiga\s+na?\s+web|search"
    r"|procure|v[ée]r(?:ificar)?\s+na?\s+web|descobrir|source|tend[eê]ncias"
    r"|quem\s+(?:é|são)|\bwhat\s+is|\bhow\s+to|notícias|not[ií]cias|atualidade)\b",
    re.IGNORECASE,
)
# Fase 15 — Percepção do computador como capacidade (o passo é {"kind": "observe"}).
_OBSERVE = re.compile(
    r"\b(?:observ[ae]|percep[çc][ãa]o|perceba|status\s+do\s+computador|o\s+que\s+está"
    r"\s+aberto?|ver\s+o\s+computador|inspecion[ae]r\s+o\s+computador|janela\s+ativa"
    r"|como\s+est[áa]\s+o\s+(?:pc|computador|sistema))\b",
    re.IGNORECASE,
)


def _research_plan(objective: str) -> list[dict]:
    """Plano de Web Research: um único passo da capacidade research."""
    return [
        {
            "description": "Pesquisar na web e sintetizar com citações",
            "kind": "research",
            "arguments": {"objective": objective[:500]},
            "verify": {"kind": "research"},
            "guard": "skip",
        }
    ]


def _observe_plan(objective: str) -> list[dict]:
    """Plano de Percepção: um único passo da capacidade observe."""
    return [
        {
            "description": "Observar o estado atual do computador",
            "kind": "observe",
            "arguments": {"include_processes": True},
            "verify": {"kind": "observe"},
            "guard": "skip",
        }
    ]


def _analysis_plan() -> list[dict]:
    return [
        {
            "description": "Inspecionar o ambiente do projeto (estrutura inicial)",
            "tool": "list_dir",
            "arguments": {"path": "", "limit": 50},
            "verify": {"kind": "exit_ok"},
            "guard": "skip",
        },
        {
            "description": "Coletar diagnóstico interno do Core",
            "tool": "dev_diagnostics",
            "arguments": {},
            "verify": {"kind": "exit_ok"},
            "guard": "skip",
        },
    ]


def _tests_plan() -> list[dict]:
    return [
        {
            "description": "Coletar diagnóstico interno do Core",
            "tool": "dev_diagnostics",
            "arguments": {},
            "verify": {"kind": "exit_ok"},
            "guard": "skip",
        },
        {
            "description": "Executar comando seguro de inspeção (allowlist)",
            "tool": "run_allowed_command",
            "arguments": {"command": "tasklist"},
            "verify": {"kind": "exit_ok"},
            "guard": "stop",
        },
    ]


def _code_plan() -> list[dict]:
    """Plano de desenvolvimento: inspecionar antes de mexer (somente leitura)."""
    return [
        {
            "description": "Inspecionar a estrutura do projeto (somente leitura)",
            "tool": "list_dir",
            "arguments": {"path": "", "limit": 50},
            "verify": {"kind": "exit_ok"},
            "guard": "skip",
        },
        {
            "description": "Coletar diagnóstico interno do Core",
            "tool": "dev_diagnostics",
            "arguments": {},
            "verify": {"kind": "exit_ok"},
            "guard": "skip",
        },
    ]


def plan_for(objective: str) -> list[dict]:
    """Produz o PLAN (lista de passos) para uma tarefa, de forma determinística.

    A heurística escolhe o template por classe reconhecível; o passo real é
    sempre resolvido pelo Tool Registry (nunca inventado). Respeita o teto
    `agent_core_max_steps`.
    """
    objective = (objective or "").strip()
    if _RESEARCH.search(objective):
        steps = _research_plan(objective)
    elif _OBSERVE.search(objective):
        steps = _observe_plan(objective)
    elif _TESTS.search(objective):
        steps = _tests_plan()
    elif _CODE.search(objective):
        steps = _code_plan()
    else:
        steps = _analysis_plan()  # default conservador: somente leitura L0
    return steps[: settings.agent_core_max_steps]


# ---------------------------------------------------------------------------
# VERIFY — verificação determinística pós-execução
# ---------------------------------------------------------------------------

def verify_step(step: dict, result: ToolResult) -> bool:
    """Verifica objetivamente o resultado de um passo.

    Sinais determinísticos (nunca LLM): o `ok` do ToolResult é a base; quando a
    verificação declara `contains`, exige também um trecho esperado na saída.
    """
    if not result.ok:
        return False
    verify = step.get("verify") or {}
    if verify.get("kind") == "contains":
        expected = verify.get("expect") or ""
        return expected in (result.output or "")
    return True


# ---------------------------------------------------------------------------
# RESEARCH — passo da capacidade de Web Research (Fase 14)
# ---------------------------------------------------------------------------

async def _execute_research_step(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    step: dict,
) -> tuple[list[str], ToolResult]:
    """Executa um passo `{"kind": "research"}` reutilizando o Research Agent.

    Sem chamadas à rede não protegia: provê search provider + fetcher + router
    à síntese (local-first, fallback determinístico). Devolve (eventos SSE,
    ToolResult) para os loops do Agentic Core.
    """
    from app.services.research_service import run_web_research

    objective = (step.get("arguments") or {}).get("objective") or ""
    events: list[str] = []

    async def sink(payload: dict) -> None:
        events.append(sse_event(payload))

    outcome = await run_web_research(
        db,
        session_id,
        provider,
        objective or "Pesquisa web",
        sink=sink,
    )
    if outcome.status != "failed" and outcome.answer:
        answer = f"{outcome.answer}\n\nFontes da pesquisa:"
        for c in outcome.citations[:8]:
            answer += f"\n- {c.get('title') or ''} ({c.get('source_url')})"
        return events, ToolResult.success(answer[:8000])
    error = outcome.error or "Pesquisa sem evidências (nenhuma resposta)."
    return events, ToolResult.failure(error)


async def _execute_observe_step(
    db: OrmSession,
    session_id: str,
    step: dict,
) -> tuple[list[str], ToolResult]:
    """Executa um passo `{"kind": "observe"}` reutilizando a Perception Layer.

    PERCEBER é independente do LLM: o modelo recebe dados estruturados, nunca
    adivinha o estado. Sem screenshot automático (nunca contínuo). Devolve
    (eventos SSE, ToolResult) para os loops do Agentic Core.
    """
    from app.perception.service import run_perception

    args = step.get("arguments") or {}
    events: list[str] = []

    async def sink(payload: dict) -> None:
        events.append(sse_event(payload))

    result = await run_perception(
        db,
        session_id,
        sink=sink,
        include_processes=bool(args.get("include_processes", False)),
        max_processes=int(args.get("max_processes") or 30),
        capture_screenshot=bool(args.get("capture_screenshot", False)),
    )
    if not result.available:
        return events, ToolResult.failure(result.error or "Observação indisponível.")

    obs = result.observation
    lines = [f"Capacidades: {obs.capabilities.summary}"]
    active = obs.active_window
    if active and (active.title or active.process_name):
        lines.append(f"Janela ativa: {active.title or '?'}" + (f" ({active.process_name})" if active.process_name else ""))
    if obs.system_stats:
        cpu = obs.system_stats.get("cpu_percent")
        lines.append(f"CPU: {cpu}%" if cpu is not None else "CPU: indisponível")
    if obs.processes:
        lines.append(f"Processos observados: {len(obs.processes)}")
    if obs.errors:
        lines.append("Avisos: " + "; ".join(obs.errors))
    return events, ToolResult.success("\n".join(lines)[:4000])


# ---------------------------------------------------------------------------
# TASK — persistência e consultas
# ---------------------------------------------------------------------------

def create_task(db: OrmSession, session_id: str, objective: str) -> AgentTask:
    """Cria a tarefa (TASK) e registra observabilidade."""
    task = AgentTask(
        session_id=session_id,
        objective=objective.strip(),
        status=TaskStatus.PLANNED.value,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    ops_service.record_event(
        db,
        session_id=session_id,
        event_type=ops_service.EVENT_TASK_STARTED,
        status=TaskStatus.PLANNED.value,
        meta=ops_service.json_safe_meta(task_id=task.id),
    )
    logger.info("Tarefa criada: %s (sessão %s)", task.id, session_id)
    return task


def get_task(db: OrmSession, task_id: str) -> AgentTask | None:
    return db.get(AgentTask, task_id)


def list_tasks(db: OrmSession, session_id: str | None = None) -> list[AgentTask]:
    stmt = select(AgentTask).order_by(AgentTask.created_at.desc())
    if session_id is not None:
        stmt = stmt.where(AgentTask.session_id == session_id)
    return list(db.scalars(stmt).all())


def get_task_by_approval(db: OrmSession, approval_id: str) -> AgentTask | None:
    return db.scalars(
        select(AgentTask).where(AgentTask.approval_id == approval_id)
    ).first()


def delete_task(db: OrmSession, task: AgentTask) -> None:
    db.delete(task)
    db.commit()


def cancel_task(db: OrmSession, task: AgentTask) -> None:
    if task.status in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value, TaskStatus.FAILED.value):
        return
    task.status = TaskStatus.CANCELLED.value
    db.commit()
    db.refresh(task)


def _progress_snapshot(task: AgentTask) -> list[dict]:
    if not task.progress_json:
        return [{"status": StepStatus.PENDING.value} for _ in task.plan]
    try:
        data = json.loads(task.progress_json)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _set_progress(db: OrmSession, task: AgentTask, index: int, **fields) -> None:
    progress = _progress_snapshot(task)
    while len(progress) <= index:
        progress.append({"status": StepStatus.PENDING.value})
    entry = dict(progress[index])
    entry.update({k: v for k, v in fields.items() if v is not None})
    progress[index] = entry
    task.progress_json = json.dumps(progress, ensure_ascii=False)
    db.commit()
    db.refresh(task)


def _steps_done_count(task: AgentTask) -> int:
    return sum(
        1
        for p in _progress_snapshot(task)
        if p.get("status") in (StepStatus.DONE.value, StepStatus.SKIPPED.value)
    )


# ---------------------------------------------------------------------------
# EXECUTE → OBSERVE → VERIFY → RECOVER/CONTINUE (via SSE)
# ---------------------------------------------------------------------------

async def run_agent_task(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    objective: str,
    *,
    plan: list[dict] | None = None,
) -> AsyncIterator[str]:
    """Executa a tarefa emitindo SSE (mesmo contrato do loop conversacional).

    `plan` opcional permite injetar um plano pronto (testes) ou futuro
    planejamento assistido por LLM; sem ele, usa o planner determinístico.
    """
    if not settings.agent_core_enabled:
        yield sse_event({"type": "error", "detail": "Agentic Core desabilitado."})
        return

    task = create_task(db, session_id, objective)
    start = time.perf_counter()
    steps = plan if plan is not None else plan_for(objective)
    task.plan_json = json.dumps(steps, ensure_ascii=False)
    task.steps_total = len(steps)
    task.status = TaskStatus.RUNNING.value
    db.commit()
    db.refresh(task)

    ops_service.record_event(
        db,
        session_id=session_id,
        event_type=ops_service.EVENT_TASK_PLANNED,
        status=TaskStatus.RUNNING.value,
        meta=ops_service.json_safe_meta(task_id=task.id, steps=len(steps), planner="deterministic"),
    )
    yield sse_event({"type": "agent_task_started", "task": _task_out(task)})
    yield sse_event(
        {
            "type": "agent_task_planned",
            "task_id": task.id,
            "steps": [s["description"] for s in steps],
        }
    )
    logger.info("Plano da tarefa %s: %d passos", task.id, len(steps))

    index = 0
    while index < len(steps) and settings.agent_core_max_steps > 0:
        step = steps[index]
        task = db.get(AgentTask, task.id)
        if task is None or _is_terminal(task):
            break

        progress = _progress_snapshot(task)
        current = progress[index] if index < len(progress) else {}
        if current.get("status") in (StepStatus.DONE.value, StepStatus.SKIPPED.value):
            index += 1
            continue

        attempts = int(current.get("attempts") or 0)
        tool_name = step.get("tool", "")
        args = step.get("arguments") or {}

        # Fase 14 — passo da capacidade pesquisa (kind="research"), nível L0.
        if step.get("kind") == "research":
            yield sse_event({"type": "tool_start", "round": index + 1, "names": ["web_research"]})
            events, result = await _execute_research_step(db, session_id, provider, step)
            for ev in events:
                yield ev
            ok = verify_step(step, result)
            _set_progress(
                db,
                task,
                index,
                status=StepStatus.DONE.value if ok else StepStatus.SKIPPED.value,
                ok=ok,
                summary=(result.output or "")[:500],
                attempts=attempts + 1,
            )
            yield sse_event({"type": "tool_done", "name": "web_research", "ok": result.ok})
            yield sse_event(
                {"type": "agent_task_step", "task_id": task.id, "index": index, "tool": "web_research", "ok": ok}
            )
            task = db.get(AgentTask, task.id)
            task.steps_done = _steps_done_count(task)
            db.commit()
            index += 1
            continue

        # Fase 15 — passo da capacidade percepção (kind="observe"), nível L0.
        if step.get("kind") == "observe":
            yield sse_event({"type": "tool_start", "round": index + 1, "names": ["observe_computer"]})
            events, result = await _execute_observe_step(db, session_id, step)
            for ev in events:
                yield ev
            ok = verify_step(step, result)
            _set_progress(
                db,
                task,
                index,
                status=StepStatus.DONE.value if ok else StepStatus.SKIPPED.value,
                ok=ok,
                summary=(result.output or "")[:500],
                attempts=attempts + 1,
            )
            yield sse_event({"type": "tool_done", "name": "observe_computer", "ok": result.ok})
            yield sse_event(
                {"type": "agent_task_step", "task_id": task.id, "index": index, "tool": "observe_computer", "ok": ok}
            )
            task = db.get(AgentTask, task.id)
            task.steps_done = _steps_done_count(task)
            db.commit()
            index += 1
            continue

        level = permission_service.effective_level(db, tool_name)

        # Pausa por aprovação (nível ≥ 2): mesma mecânica do loop conversacional.
        if level > PermissionLevel.LEVEL_1:
            approval = approval_service.create_approval(
                db,
                session_id=session_id,
                tool_name=tool_name,
                arguments=args,
                permission_level=level.value,
                risk="medium",
            )
            task = db.get(AgentTask, task.id)
            task.approval_id = approval.id
            task.status = TaskStatus.RUNNING.value
            _set_progress(db, task, index, status="pending_approval")
            audit_service.log_action(
                db,
                action="agent_task.paused",
                session_id=session_id,
                tool=tool_name,
                allowed=None,
                detail=f"tarefa {task.id} aguardando decisão (passo {index})",
            )
            yield sse_event(
                {
                    "type": "approval_request",
                    "approval": approval_service.to_out(approval).model_dump(mode="json"),
                }
            )
            yield sse_event(
                {
                    "type": "approval_pending",
                    "count": 1,
                    "approvals": [approval_service.to_out(approval).model_dump(mode="json")],
                }
            )
            return

        yield sse_event({"type": "tool_start", "round": index + 1, "names": [tool_name]})
        _set_progress(db, task, index, status=StepStatus.RUNNING.value, attempts=attempts)

        call = ToolCall(name=tool_name, arguments=args, call_id=f"task_{task.id}_{index}")
        result = await agent_loop._run_tool(db, session_id, provider, call)

        # OBSERVE — resultado normalizado do passo.
        ok = verify_step(step, result)
        _set_progress(
            db,
            task,
            index,
            status=StepStatus.DONE.value if ok else StepStatus.FAILED.value,
            ok=ok,
            summary=(result.output or "")[:500],
            attempts=attempts + 1,
        )

        event: dict = {"type": "tool_done", "name": tool_name, "ok": result.ok}
        if result.ok:
            event["output"] = result.output
        else:
            event["detail"] = result.output
        yield sse_event(event)
        yield sse_event(
            {"type": "agent_task_step", "task_id": task.id, "index": index, "tool": tool_name, "ok": ok}
        )

        if not ok:
            # RECOVER — nova tentativa no limite configurado (RE-EXECUTA o passo).
            guard = step.get("guard", "retry")
            if guard == "retry" and attempts < settings.agent_core_max_retries:
                _set_progress(db, task, index, status="pending_retry")
                audit_service.log_action(
                    db,
                    action="agent_task.retry",
                    session_id=session_id,
                    tool=tool_name,
                    allowed=False,
                    detail=f"tarefa {task.id} passo {index} recuperado (tentativa {attempts + 1}/{settings.agent_core_max_retries})",
                )
                yield sse_event(
                    {
                        "type": "agent_task_step",
                        "task_id": task.id,
                        "index": index,
                        "tool": tool_name,
                        "ok": False,
                        "recovery": "retry",
                    }
                )
                continue  # re-executa o MESMO passo (a próxima iteração).

            if guard == "stop":
                task = db.get(AgentTask, task.id)
                if task is None:
                    return
                task.status = TaskStatus.FAILED.value
                task.error = (result.output or "")[:1000]
                task.completed_at = utcnow()
                db.commit()
                ops_service.record_event(
                    db,
                    session_id=session_id,
                    event_type=ops_service.EVENT_TASK_FAILED,
                    status=TaskStatus.FAILED.value,
                    latency_ms=_latency(start),
                    meta=ops_service.json_safe_meta(task_id=task.id),
                )
                yield sse_event(
                    {
                        "type": "agent_task_done",
                        "task_id": task.id,
                        "status": TaskStatus.FAILED.value,
                        "error": task.error,
                    }
                )
                return

            # "retry" esgotado (ou "skip"): ignora o passo sem barrar a tarefa.
            _set_progress(db, task, index, status=StepStatus.SKIPPED.value, ok=False)

        task = db.get(AgentTask, task.id)
        if task is None or _is_terminal(task):
            break
        task.steps_done = _steps_done_count(task)
        db.commit()
        index += 1

    task = db.get(AgentTask, task.id)
    if task is None:
        return
    # CONTINUE → COMPLETION (somente se ainda RUNNING; p. ex. cancelado fica parado).
    if task.status != TaskStatus.RUNNING.value:
        yield sse_event(
            {
                "type": "agent_task_done",
                "task_id": task.id,
                "status": task.status,
            }
        )
        yield sse_event({"type": "done"})
        return
    task.status = TaskStatus.COMPLETED.value
    task.completed_at = utcnow()
    task.steps_done = _steps_done_count(task)
    db.commit()
    db.refresh(task)

    ops_service.record_event(
        db,
        session_id=session_id,
        event_type=ops_service.EVENT_TASK_COMPLETED,
        status=TaskStatus.COMPLETED.value,
        latency_ms=_latency(start),
        meta=ops_service.json_safe_meta(task_id=task.id),
    )
    summary = _completion_summary(task)
    yield sse_event(
        {
            "type": "agent_task_done",
            "task_id": task.id,
            "status": TaskStatus.COMPLETED.value,
            "summary": summary,
        }
    )
    yield sse_event({"type": "done"})


# ---------------------------------------------------------------------------
# RESUME — retomada pela aprovação do usuário (pós-decisão)
# ---------------------------------------------------------------------------

async def resume_agent_task(
    db: OrmSession,
    task: AgentTask,
    provider: AIProvider,
    approval,
) -> AsyncIterator[str]:
    """Retoma a tarefa pausada por aprovação, aplicando a decisão do usuário.

    Aplicado o approval (`approved`/`denied`), executa/pula o passo pendente e
    segue o plano restante — mesmo raciocínio do `_collect_decisions`.
    """
    approval_service.mark_applied(db, approval)
    task = db.get(AgentTask, task.id)
    if task is None:
        yield sse_event({"type": "error", "detail": "Tarefa não encontrada."})
        return
    task.approval_id = None
    db.commit()

    if _is_terminal(task):
        # Tarefa cancelada/falha/concluída: não re-executa a decisão.
        yield sse_event({"type": "start"})
        yield sse_event(
            {
                "type": "agent_task_done",
                "task_id": task.id,
                "status": task.status,
            }
        )
        yield sse_event({"type": "done"})
        return

    progress = _progress_snapshot(task)
    index = next(
        (
            i
            for i, p in enumerate(progress)
            if p.get("status") in ("pending_approval", "pending_retry", StepStatus.PENDING.value)
        ),
        None,
    )
    if index is None:
        index = next(
            (
                i
                for i, p in enumerate(progress)
                if p.get("status") not in (StepStatus.DONE.value, StepStatus.SKIPPED.value)
            ),
            None,
        )

    yield sse_event({"type": "start"})
    yield sse_event({"type": "agent_task_resumed", "task_id": task.id, "index": index})

    if index is not None and index < len(task.plan):
        step = task.plan[index]
        tool_name = step.get("tool", "")
        args = step.get("arguments") or {}
        if step.get("kind") == "research":
            yield sse_event({"type": "tool_start", "round": index + 1, "names": ["web_research"]})
            events, result = await _execute_research_step(db, task.session_id, provider, step)
            for ev in events:
                yield ev
            ok = verify_step(step, result)
            _set_progress(db, task, index, status=StepStatus.DONE.value if ok else StepStatus.SKIPPED.value, ok=ok, summary=(result.output or "")[:500])
            yield sse_event({"type": "tool_done", "name": "web_research", "ok": result.ok})
            yield sse_event(
                {"type": "agent_task_step", "task_id": task.id, "index": index, "tool": "web_research", "ok": ok}
            )
        elif step.get("kind") == "observe":
            yield sse_event({"type": "tool_start", "round": index + 1, "names": ["observe_computer"]})
            events, result = await _execute_observe_step(db, task.session_id, step)
            for ev in events:
                yield ev
            ok = verify_step(step, result)
            _set_progress(db, task, index, status=StepStatus.DONE.value if ok else StepStatus.SKIPPED.value, ok=ok, summary=(result.output or "")[:500])
            yield sse_event({"type": "tool_done", "name": "observe_computer", "ok": result.ok})
            yield sse_event(
                {"type": "agent_task_step", "task_id": task.id, "index": index, "tool": "observe_computer", "ok": ok}
            )
        elif approval.status == ApprovalStatus.DENIED.value:
            audit_service.log_action(
                db,
                action="agent_task.decided",
                session_id=task.session_id,
                tool=tool_name,
                allowed=False,
                detail=f"negação {approval.id} — passo {index} ignorado",
            )
            _set_progress(db, task, index, status=StepStatus.SKIPPED.value, ok=False, summary="Usuário negou a execução.")
            yield sse_event({"type": "tool_done", "name": tool_name, "ok": False, "detail": "Execução negada pelo usuário."})
        else:
            yield sse_event({"type": "tool_start", "round": index + 1, "names": [tool_name]})
            call = ToolCall(name=tool_name, arguments=args, call_id=f"task_{task.id}_{index}")
            result = await agent_loop._run_tool(db, task.session_id, provider, call)
            ok = verify_step(step, result)
            _set_progress(db, task, index, status=StepStatus.DONE.value if ok else StepStatus.SKIPPED.value, ok=ok, summary=(result.output or "")[:500])
            event: dict = {"type": "tool_done", "name": tool_name, "ok": result.ok}
            if result.ok:
                event["output"] = result.output
            else:
                event["detail"] = result.output
            yield sse_event(event)

        yield sse_event(
            {"type": "agent_task_step", "task_id": task.id, "index": index, "tool": tool_name, "ok": True}
        )

    async for item in _continue_plan(db, task, provider):
        yield item


async def _continue_plan(
    db: OrmSession, task: AgentTask, provider: AIProvider
) -> AsyncIterator[str]:
    """Percorre os passos restantes após retomada (sem novo plano)."""
    for index, step in enumerate(task.plan):
        task = db.get(AgentTask, task.id)
        if task is None or _is_terminal(task):
            break
        progress = _progress_snapshot(task)
        current = progress[index] if index < len(progress) else {}
        if current.get("status") in (StepStatus.DONE.value, StepStatus.SKIPPED.value):
            continue

        if step.get("kind") == "research":
            yield sse_event({"type": "tool_start", "round": index + 1, "names": ["web_research"]})
            events, result = await _execute_research_step(db, task.session_id, provider, step)
            for ev in events:
                yield ev
            ok = verify_step(step, result)
            _set_progress(db, task, index, status=StepStatus.DONE.value if ok else StepStatus.SKIPPED.value, ok=ok, summary=(result.output or "")[:500])
            yield sse_event({"type": "tool_done", "name": "web_research", "ok": result.ok})
            yield sse_event(
                {"type": "agent_task_step", "task_id": task.id, "index": index, "tool": "web_research", "ok": ok}
            )
            continue

        if step.get("kind") == "observe":
            yield sse_event({"type": "tool_start", "round": index + 1, "names": ["observe_computer"]})
            events, result = await _execute_observe_step(db, task.session_id, step)
            for ev in events:
                yield ev
            ok = verify_step(step, result)
            _set_progress(db, task, index, status=StepStatus.DONE.value if ok else StepStatus.SKIPPED.value, ok=ok, summary=(result.output or "")[:500])
            yield sse_event({"type": "tool_done", "name": "observe_computer", "ok": result.ok})
            yield sse_event(
                {"type": "agent_task_step", "task_id": task.id, "index": index, "tool": "observe_computer", "ok": ok}
            )
            continue

        tool_name = step.get("tool", "")
        level = permission_service.effective_level(db, tool_name)
        if level > PermissionLevel.LEVEL_1:
            approval = approval_service.create_approval(
                db,
                session_id=task.session_id,
                tool_name=tool_name,
                arguments=step.get("arguments") or {},
                permission_level=level.value,
                risk="medium",
            )
            task = db.get(AgentTask, task.id)
            task.approval_id = approval.id
            _set_progress(db, task, index, status="pending_approval")
            db.commit()
            yield sse_event(
                {
                    "type": "approval_request",
                    "approval": approval_service.to_out(approval).model_dump(mode="json"),
                }
            )
            yield sse_event(
                {
                    "type": "approval_pending",
                    "count": 1,
                    "approvals": [approval_service.to_out(approval).model_dump(mode="json")],
                }
            )
            return

        yield sse_event({"type": "tool_start", "round": index + 1, "names": [tool_name]})
        call = ToolCall(name=tool_name, arguments=step.get("arguments") or {}, call_id=f"task_{task.id}_{index}")
        result = await agent_loop._run_tool(db, task.session_id, provider, call)
        ok = verify_step(step, result)
        _set_progress(db, task, index, status=StepStatus.DONE.value if ok else StepStatus.SKIPPED.value, ok=ok, summary=(result.output or "")[:500])
        event: dict = {"type": "tool_done", "name": tool_name, "ok": result.ok}
        if result.ok:
            event["output"] = result.output
        else:
            event["detail"] = result.output
        yield sse_event(event)
        yield sse_event(
            {"type": "agent_task_step", "task_id": task.id, "index": index, "tool": tool_name, "ok": ok}
        )

    task = db.get(AgentTask, task.id)
    if task is None:
        return
    if task.status != TaskStatus.RUNNING.value:
        yield sse_event(
            {
                "type": "agent_task_done",
                "task_id": task.id,
                "status": task.status,
            }
        )
        yield sse_event({"type": "done"})
        return
    task.status = TaskStatus.COMPLETED.value
    task.completed_at = utcnow()
    task.steps_done = _steps_done_count(task)
    db.commit()
    summary = _completion_summary(task)
    yield sse_event(
        {
            "type": "agent_task_done",
            "task_id": task.id,
            "status": TaskStatus.COMPLETED.value,
            "summary": summary,
        }
    )
    yield sse_event({"type": "done"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _completion_summary(task: AgentTask) -> str:
    done = sum(
        1 for p in _progress_snapshot(task) if p.get("status") == StepStatus.DONE.value
    )
    skipped = sum(
        1
        for p in _progress_snapshot(task)
        if p.get("status") == StepStatus.SKIPPED.value
    )
    return (
        f"Tarefa concluída ({task.id[:8]}). Passos: {done} concluídos, "
        f"{skipped} ignorados, {task.steps_total} previstos."
    )


def _latency(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _is_terminal(task: AgentTask) -> bool:
    return task.status in (
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    )


def _task_out(task: AgentTask) -> dict:
    return {
        "id": task.id,
        "session_id": task.session_id,
        "objective": task.objective,
        "status": task.status,
        "steps_done": task.steps_done,
        "steps_total": task.steps_total,
        "error": task.error,
        "plan": task.plan,
        "progress": _progress_snapshot(task),
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }