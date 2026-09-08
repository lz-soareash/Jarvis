"""Computer Agent (Fase 19) — loop GOAL→PERCEIVE→INTERPRET→PLAN→ACT→OBSERVE→
VERIFY→RECOVER→CONTINUE→COMPLETE sobre Perception + Action existentes.

Reuso obrigatório (nenhuma duplicação):
- Perception: `run_perception` (Fase 15).
- Ação: `ActionExecutor` do Computer Action Layer (Fase 18) — pipeline completo
  validação→safety→permission→rate limit→adapter→audit→event.
- Permissão: `permissions.effective_level` + approvals existentes.
- Observabilidade: `ops.record_event` + `audit.log_action` + broker remoto.
- Planejamento: AI Router (local→gemini→deterministic).

O agente é uma MÁQUINA DE ESTADOS resumível: `advance()` percorre o plano até
COMPLETAR, FALHAR, CANCELAR ou pausar para CONFIRMAÇÃO (approvals existentes).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.config import settings
from app.services import permissions as permission_service
from app.services import approvals as approval_service

from . import events as ev
from .models import ComputerStatus, ComputerTaskState, IntendedAction
from .planner import ComputerPlanner, DEFAULT_PLANNER
from .recovery import ComputerRecovery, KIND_RETRY, KIND_REPLAN, KIND_GIVE_UP
from .security import guard_intent
from .store import get_store
from .verifier import ComputerVerifier

logger = logging.getLogger("jarvis.computer_agent")

_POST_OBSERVATIONS_MAX = 15


async def _default_perceive(db, session_id: str | None) -> tuple[dict | None, str | None]:
    """Percepção padrão (Perception Layer da Fase 15). Retorna (obs, erro)."""
    from app.perception.service import run_perception

    result = await run_perception(db, session_id, include_processes=True, max_processes=30)
    if not result.available:
        return None, result.error or "Observação indisponível"
    return _sanitize_observation(result.observation.to_dict()), None


def _sanitize_observation(obs: dict) -> dict:
    """Resumo observável sanitizado — nunca binários/screenshot/segredos."""
    out: dict[str, Any] = {"capabilities": dict(obs.get("capabilities") or {})}
    active = obs.get("active_window") or {}
    out["active_window"] = {
        "title": str(active.get("title") or ""),
        "process_name": str(active.get("process_name") or ""),
        "pid": active.get("pid"),
    }
    procs = obs.get("processes") or []
    out["processes"] = [
        {"name": str(p.get("name") or ""), "pid": p.get("pid")} for p in procs[:30]
    ]
    stats = obs.get("system_stats") or {}
    out["system_stats"] = {
        k: stats.get(k) for k in ("cpu_percent", "memory_percent", "memory_used_gb")
        if stats.get(k) is not None
    }
    return out


class ComputerAgent:
    def __init__(
        self,
        *,
        planner: ComputerPlanner | None = None,
        verifier: ComputerVerifier | None = None,
        recovery: ComputerRecovery | None = None,
        executor: Any | None = None,
        perceive: Callable[..., Any] | None = None,
        store: Any | None = None,
    ) -> None:
        self.planner = planner or DEFAULT_PLANNER
        self.verifier = verifier or ComputerVerifier()
        self.recovery = recovery or ComputerRecovery()
        self._executor = executor
        self._perceive = perceive or _default_perceive
        self.store = store or get_store()

    def is_enabled(self) -> bool:
        return bool(settings.computer_agent_enabled)

    # ------------------------------------------------------------------ API

    async def advance(
        self,
        db: Any,
        provider: Any,
        task: ComputerTaskState,
        *,
        sink: Callable[[dict], None] | None = None,
    ) -> ComputerTaskState:
        """Percorre um segmento do loop. Retorna o estado atual (salvo no store).

        Pode concluir (COMPLETED/FAILED/CANCELLED) ou PAUSAR em
        WAITING_CONFIRMATION (approval pendente — retomado por `advance` após
        a decisão do usuário nos approvals existentes).
        """
        # CONFIRMAÇÃO decidida (approvals respondidos) — recolhe antes do loop.
        task = self._consume_pending_decisions(db, task)

        if task.cancellation_requested:
            return self._finish(db, task, ComputerStatus.CANCELLED,
                                "cancelamento solicitado", ev.EVENT_TASK_CANCELLED, sink)

        if not self.is_enabled():
            return self._finish(db, task, ComputerStatus.FAILED,
                                "computer_agent_enabled=false", ev.EVENT_TASK_FAILED, sink)

        if not task.plan:
            task = await self._perceive_and_plan(db, provider, task, sink)
            if task.status in (ComputerStatus.FAILED, ComputerStatus.CANCELLED):
                return task

        # C0 — observe only: nenhuma AÇÃO é executada (apenas contexto).
        if task.autonomy.value == "C0":
            task = await self._verify_observe_only(db, task, sink)

        while task.plan and task.status == ComputerStatus.EXECUTING \
                and task.step_index < len(task.plan):
            task = await self._run_iteration(db, provider, task, sink)
            if task.status != ComputerStatus.EXECUTING:
                break

        if task.status == ComputerStatus.EXECUTING:
            task = self._finish(db, task, ComputerStatus.COMPLETED,
                                "goal", ev.EVENT_TASK_COMPLETED, sink)
        self.store.save(task)
        return task

    # ----------------------------------------------------------- private-api

    async def _perceive_and_plan(self, db, provider, task, sink) -> ComputerTaskState:
        task = task.with_(status=ComputerStatus.PERCEIVING,
                          provider_used=None, provider_reason=None)
        ev.emit(db, event_type=ev.EVENT_TASK_CREATED, session_id=task.session_id,
                status=task.status.value,
                meta={"task_id": task.task_id, "goal_hash": _goal_hash(task.goal)})
        ev.emit(db, event_type=ev.EVENT_TASK_STARTED, session_id=task.session_id,
                status=task.status.value, meta={"task_id": task.task_id})
        ev.audit(db, task_id=task.task_id, action="computer.task.started", allowed=True,
                 detail=f"goal={_clip(task.goal)} autonomy={task.autonomy.value}")
        if sink:
            sink({"type": "computer.task.started", "task_id": task.task_id,
                  "goal": task.goal[:200]})

        obs, error = await self._perceive(db, task.session_id)
        if obs is None:
            return self._finish(db, task, ComputerStatus.FAILED,
                                error or "observação indisponível", ev.EVENT_TASK_FAILED, sink)
        task = task.with_(observations=task.observations + [obs],
                          last_observation=obs, status=ComputerStatus.PLANNING)
        ev.emit(db, event_type=ev.EVENT_OBSERVATION_RECEIVED, session_id=task.session_id,
                status="ok", meta={"task_id": task.task_id})
        if sink:
            sink({"type": "computer.observation.received", "task_id": task.task_id})

        outcome = await self.planner.plan(db, provider, task.goal, obs)
        task = task.with_(
            plan=list(outcome.steps),
            provider_used=outcome.provider,
            provider_reason=outcome.reason,
            status=ComputerStatus.EXECUTING,
        )
        ev.emit(db, event_type=ev.EVENT_TASK_PLANNED, session_id=task.session_id,
                status="planned", meta={"task_id": task.task_id, "steps": len(outcome.steps),
                                        "provider": outcome.provider,
                                        "fallback": outcome.fallback,
                                        "latency_ms": outcome.latency_ms})
        ev.audit(db, task_id=task.task_id, action="computer.task.planned", allowed=True,
                 detail=f"steps={len(outcome.steps)} provider={outcome.provider}")
        self.store.save(task)
        return task

    async def _run_iteration(self, db, provider, task, sink) -> ComputerTaskState:
        if task.cancellation_requested:
            return self._finish(db, task, ComputerStatus.CANCELLED,
                                "cancelamento solicitado", ev.EVENT_TASK_CANCELLED, sink)
        if task.deadline is not None and time.time() > task.deadline:
            return self._finish(db, task, ComputerStatus.FAILED,
                                "deadline excedido", ev.EVENT_TASK_FAILED, sink)
        if task.steps_total >= task.max_steps > 0:
            return self._loop_prevented(db, task, "max_steps", sink)
        if task.actions_total >= task.max_actions > 0:
            return self._loop_prevented(db, task, "max_actions", sink)

        intended = task.plan[task.step_index]
        if intended.action in ("none", "observe"):
            # Passo sem ação no OS: apenas registro como observado.
            task = task.with_(steps_total=task.steps_total + 1,
                              step_index=task.step_index + 1)
            self.store.save(task)
            return task

        verdict = guard_intent(intended, observation=task.last_observation,
                               goal=task.goal)
        if not verdict.ok:
            ev.emit(db, event_type=ev.EVENT_INJECTION_BLOCKED, session_id=task.session_id,
                    status="blocked", meta={"task_id": task.task_id, "reason": verdict.reason})
            ev.audit(db, task_id=task.task_id, action="computer.security.prompt_injection",
                     allowed=False, detail=verdict.reason)
            return self._finish(db, task, ComputerStatus.FAILED,
                                f"bloqueado ({verdict.reason})", ev.EVENT_TASK_FAILED, sink)

        # Permissão combinada: tool (Permission Engine) + tipo de ação + autonomia.
        level = await self._combined_level(db, intended)
        autonomy_max = task.autonomy.max_allowed_level()
        if level > autonomy_max or (task.autonomy.value == "C3" and level > 2) \
                or (task.autonomy.value == "C2" and level > 2):
            ev.emit(db, event_type=ev.EVENT_AUTONOMY_BLOCKED, session_id=task.session_id,
                    status="blocked", meta={"task_id": task.task_id, "level": level,
                                            "autonomy": task.autonomy.value})
            ev.audit(db, task_id=task.task_id, action="computer.security.autonomy_blocked",
                     allowed=False, detail=f"level={level} autonomy={task.autonomy.value}")
            return self._finish(db, task, ComputerStatus.FAILED,
                                "nível de autonomia insuficiente",
                                ev.EVENT_TASK_FAILED, sink)

        # Confirmação: nível >= 2 quando exigida (default).
        need_confirm = level >= 2 and bool(settings.computer_agent_require_confirmation_l2)
        if task.autonomy.value == "C4":
            need_confirm = level >= 3 and not task.explicit_authorization
        if need_confirm and not task.confirmed_pending:
            approval = approval_service.create_approval(
                db,
                session_id=task.session_id or "computer",
                tool_name="computer_action",
                arguments=intended.to_dict(),
                permission_level=level,
                risk="medium",
            )
            task = task.with_(
                confirmations_required=task.confirmations_required + 1,
                pending_approval_ids=task.pending_approval_ids + [approval.id],
                status=ComputerStatus.WAITING_CONFIRMATION,
            )
            ev.emit(db, event_type=ev.EVENT_WAITING_CONFIRMATION,
                    session_id=task.session_id, status="waiting",
                    meta={"task_id": task.task_id, "approval_id": approval.id,
                          "action": intended.action})
            ev.audit(db, task_id=task.task_id, action="computer.task.paused", allowed=None,
                     detail=f"approval={approval.id} action={intended.action}")
            self.store.save(task)
            if sink:
                sink({"type": "approval_request", "task_id": task.task_id,
                      "approval": approval_service.to_out(approval).model_dump(mode="json")})
            return task

        return await self._act_and_verify(db, task, intended, sink=sink)

    async def _act_and_verify(self, db, task, intended, *, sink) -> ComputerTaskState:
        task = task.with_(status=ComputerStatus.EXECUTING)
        ev.emit(db, event_type=ev.EVENT_ACTION_REQUESTED, session_id=task.session_id,
                status="requested", meta={"task_id": task.task_id, "action": intended.action})
        if sink:
            sink({"type": "computer.action.requested", "task_id": task.task_id,
                  "action": intended.action})

        confirmed = bool(task.confirmed_pending)
        task = task.with_(confirmed_pending=False)
        executor = self._executor or (await _executor_factory())
        request = _build_request(intended, task, confirmed=confirmed)
        opts = _build_opts(task, _make_cancel_cb(task))
        result = await executor.execute(request, opts)

        task = task.with_(
            actions_total=task.actions_total + 1,
            steps_total=task.steps_total + 1,
            status=ComputerStatus.OBSERVING,
        )
        summary = _action_summary(intended, result)
        task = task.with_(actions=task.actions[-_POST_OBSERVATIONS_MAX + 1:] + [summary])
        task = task.with_(last_action_summary=summary.get("summary"))
        ok = result.ok
        ev.emit(db, event_type=ev.EVENT_ACTION_EXECUTED if ok else ev.EVENT_ACTION_FAILED,
                session_id=task.session_id,
                status="executed" if ok else (result.status or "failed"),
                meta={"task_id": task.task_id, "action": intended.action,
                      "duration_ms": result.duration_ms})
        if sink:
            sink({"type": "computer.action.executed", "task_id": task.task_id,
                  "action": intended.action, "ok": ok,
                  "duration_ms": result.duration_ms})

        if not ok:
            return await self._handle_failure(db, task, intended,
                                              error=f"{intended.action}: {result.error_message or result.status}",
                                              sink=sink)

        # OBSERVE após a ação → VERIFICA (só "executou" não basta).
        task = task.with_(status=ComputerStatus.OBSERVING)
        obs, error = await self._perceive(db, task.session_id)
        if obs is not None:
            task = task.with_(observations=task.observations[-_POST_OBSERVATIONS_MAX + 1:] + [obs],
                              last_observation=obs)
        ev.emit(db, event_type=ev.EVENT_VERIFICATION_STARTED, session_id=task.session_id,
                status="started", meta={"task_id": task.task_id})
        verdict = self.verifier.verify(obs, intended)
        task = task.with_(
            verification_results=task.verification_results[-20:] + [verdict.to_dict()],
            status=ComputerStatus.VERIFYING,
        )
        ev.emit(db, event_type=ev.EVENT_VERIFICATION_SUCCESS if verdict.ok else ev.EVENT_VERIFICATION_FAILED,
                session_id=task.session_id, status=("success" if verdict.ok else "failed"),
                meta={"task_id": task.task_id, "reason": verdict.reason,
                      "structural": verdict.structural})
        if sink:
            sink({"type": "computer.verification.success" if verdict.ok
                  else "computer.verification.failed",
                  "task_id": task.task_id, "ok": verdict.ok,
                  "reason": verdict.reason})

        if verdict.ok:
            return task.with_(status=ComputerStatus.EXECUTING,
                              step_index=task.step_index + 1)
        return await self._handle_failure(db, task, intended,
                                          error=f"verificação falhou: {verdict.reason}",
                                          sink=sink)

    async def _handle_failure(self, db, task, intended, *, error, sink) -> ComputerTaskState:
        task = task.with_(errors=task.errors[-10:] + [error], status=ComputerStatus.RECOVERING,
                          recoveries=task.recoveries + 1)
        decision = self.recovery.decide(task=task, error=error)
        ev.emit(db, event_type=ev.EVENT_RECOVERY_STARTED, session_id=task.session_id,
                status="started", meta={"task_id": task.task_id, "kind": decision.kind})
        if decision.kind == KIND_RETRY:
            task = task.with_(retries_used=task.retries_used + 1, status=ComputerStatus.EXECUTING)
        elif decision.kind == KIND_REPLAN:
            task = task.with_(plan_retries_used=task.plan_retries_used + 1,
                              status=ComputerStatus.PLANNING)
            outcome = await self.planner.plan(
                db, None, f"{task.goal}\n[Contexto de falha] {error}", task.last_observation)
            new_plan = task.plan[: task.step_index] + list(outcome.steps)
            task = task.with_(
                plan=new_plan,
                steps_total=len(new_plan),
                status=ComputerStatus.EXECUTING,
            )
        elif decision.kind == KIND_GIVE_UP:
            return self._finish(db, task, ComputerStatus.FAILED,
                                error, ev.EVENT_TASK_FAILED, sink)
        ev.emit(db, event_type=ev.EVENT_RECOVERY_COMPLETED, session_id=task.session_id,
                status="completed", meta={"task_id": task.task_id, "kind": decision.kind})
        self.store.save(task)
        return task

    # ------------------------------------------------------------- helpers

    def _consume_pending_decisions(self, db, task) -> ComputerTaskState:
        """Aplica decisões já dadas nos approvals pendentes (resume)."""
        if not task.pending_approval_ids:
            return task
        pending = list(task.pending_approval_ids)
        task = task.with_(pending_approval_ids=[])
        for approval_id in pending:
            approval = approval_service.get_approval(db, approval_id)
            if approval is None:
                continue
            from app.core.enums import ApprovalStatus
            if approval.status == ApprovalStatus.DENIED.value:
                ev.audit(db, task_id=task.task_id, action="computer.task.approval_denied",
                         allowed=False, detail=f"approval={approval_id}")
                task = task.with_(errors=task.errors[-10:] + [f"aprovação negada: {approval_id}"])
                task = task.with_(step_index=task.step_index + 1,
                                  status=ComputerStatus.EXECUTING)
            elif approval.status == ApprovalStatus.PENDING.value:
                return task.with_(pending_approval_ids=pending)
            else:
                approval_service.mark_applied(db, approval)
                ev.audit(db, task_id=task.task_id, action="computer.task.approval_granted",
                         allowed=True, detail=f"approval={approval_id}")
                task = task.with_(confirmed_pending=True, status=ComputerStatus.EXECUTING)
        return task

    async def _combined_level(self, db, intended: IntendedAction) -> int:
        from app.action import registry as action_registry

        tool_level = permission_service.effective_level(db, "computer_action")
        descriptor = action_registry.get_action_registry().get(intended.action)
        action_level = int(descriptor.permission_level if descriptor else 1)
        return max(int(tool_level), action_level)

    def _finish(self, db, task, status: ComputerStatus, error: str, event: str, sink) -> ComputerTaskState:
        now = datetime.now(timezone.utc).isoformat()
        task = task.with_(status=status, error=error, completed_at=now)
        ev.emit(db, event_type=event, session_id=task.session_id, status=status.value,
                meta={"task_id": task.task_id, "goal_hash": _goal_hash(task.goal)})
        ev.audit(db, task_id=task.task_id, action=f"computer.task.{status.value}",
                 allowed=status in (ComputerStatus.COMPLETED, ComputerStatus.CANCELLED),
                 detail=error)
        self.store.save(task)
        if sink:
            sink({"type": f"computer.task.{status.value}", "task_id": task.task_id,
                  "status": status.value, "error": error})
        return task

    def _loop_prevented(self, db, task, reason: str, sink) -> ComputerTaskState:
        task = task.with_(loops_prevented=task.loops_prevented + 1)
        ev.emit(db, event_type=ev.EVENT_LOOP_PREVENTED, session_id=task.session_id,
                status="blocked", meta={"task_id": task.task_id, "reason": reason})
        ev.audit(db, task_id=task.task_id, action="computer.loop.prevented",
                 allowed=False, detail=reason)
        return self._finish(db, task, ComputerStatus.FAILED,
                            f"limite de loop ({reason})", ev.EVENT_TASK_FAILED, sink)

    async def _verify_observe_only(self, db, task, sink) -> ComputerTaskState:
        obs, error = await self._perceive(db, task.session_id)
        if obs is None:
            return self._finish(db, task, ComputerStatus.FAILED,
                                error or "observação indisponível", ev.EVENT_TASK_FAILED, sink)
        task = task.with_(observations=task.observations + [obs], last_observation=obs,
                          status=ComputerStatus.COMPLETED)
        ev.emit(db, event_type=ev.EVENT_OBSERVATION_RECEIVED, session_id=task.session_id,
                status="ok", meta={"task_id": task.task_id})
        self.store.save(task)
        if sink:
            sink({"type": "computer.observation.received", "task_id": task.task_id})
        return self._finish(db, task, ComputerStatus.COMPLETED, "observe-only",
                            ev.EVENT_TASK_COMPLETED, sink)


# ---------------------------------------------------------------------------
# helpers de construção (Action Layer)
# ---------------------------------------------------------------------------

async def _executor_factory():
    from app.action.executor import get_executor
    return get_executor()


def _build_request(intended: IntendedAction, task: ComputerTaskState, *, confirmed: bool):
    from app.action.models import ActionRequest

    return ActionRequest(
        action_type=intended.action,
        params=dict(intended.params or {}),
        requested_by="computer_agent",
        session_id=task.session_id,
        device_id=task.device_id,
        dry_run=False,
        metadata={"computer_task": task.task_id, "confirmed": confirmed},
    )


def _build_opts(task: ComputerTaskState, cancelled_cb: Callable[[], bool]):
    from app.action.executor import ActionExecutorOptions

    return ActionExecutorOptions(
        requested_by="computer_agent",
        session_id=task.session_id,
        device_id=task.device_id,
        cancelled_cb=cancelled_cb,
    )


def _make_cancel_cb(task: ComputerTaskState) -> Callable[[], bool]:
    return lambda: task.cancellation_requested


def _action_summary(intended: IntendedAction, result) -> dict:
    status = getattr(result, "status", None) or "unknown"
    return {"action": intended.action, "status": status,
            "duration_ms": getattr(result, "duration_ms", None),
            "summary": f"{intended.action} ({status})"}


def _clip(value: str, limit: int = 100) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[:limit] + "…"


def _goal_hash(goal: str) -> str:
    import hashlib
    return hashlib.sha256((goal or "").encode("utf-8")).hexdigest()[:12]