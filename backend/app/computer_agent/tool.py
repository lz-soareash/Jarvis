"""Fase 19/20 — tool `computer_use` (Computer Agent no Tool Engine).

O modelo delega UMA tarefa de Computer Use (objetivo + contexto). O agente então
observa, planeja, age e verifica DENTRO do pipeline governado by Permission
Engine / Auditoria / aprovações — nunca executa fora do ToolRegistry.

Fase 20 (VEGA V1): a tool agora executa o loop do agente DENTRO do turno de
chat (single agent). Ela avança a máquina de estados até COMPLETAR/FALHAR/
CANCELAR ou até pausar esperando CONFIRMAÇÃO; os eventos reais do agente
(`computer.task.*`, `computer.action.*`, approval requests) são encaminhados
para o SSE do turno via `ToolContext.extras["emit"]` — nunca fabricados.

A segurança respeita os tetos configurados (`computer_agent_*`) e o nível de
autonomia máximo vem do usuário/config, jamais do modelo.
"""

from __future__ import annotations

import json
import logging
import time

from app.core.config import settings
from app.core.enums import PermissionLevel, RiskLevel
from app.tools.base import Tool, ToolContext, ToolResult

from . import store
from .agent import ComputerAgent
from .models import ComputerStatus

logger = logging.getLogger("jarvis.computer_agent.tool")

# Limite conservador de avanços in-turn por chamada: independe de max_steps
# (que é por tarefa) e evita um turno de chat monopolizado por uma tarefa longa.
_MAX_IN_TURN_ADVANCES = 30


class ComputerUseTool(Tool):
    """Delega um objetivo de Computer Use ao Computer Agent (loop observação→ação)."""

    name = "computer_use"
    description = (
        "Delega um OBJETIVO de computador para o Computer Agent, que observa a "
        "tela, planeja passos, executa ações e verifica o resultado de forma "
        "autônoma. Forneça `goal` claro e escopo limitado. Ações seguem "
        "autonomia configurada e pedem confirmação via aprovações quando "
        "necessário. Se a execução for interrompida por confirmação necessária, "
        "continue chamando `computer_use` com o mesmo `task_id` após a decisão."
    )
    permission_level = PermissionLevel.LEVEL_2
    risk = RiskLevel.MEDIUM

    parameters = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "Objetivo claro e escopo limitado.",
            },
            "session_id": {
                "type": "string",
                "description": "Identificador da sessão (opcional).",
            },
            "task_id": {
                "type": "string",
                "description": (
                    "Retoma uma tarefa existente (ex.: pausada aguardando "
                    "confirmação). Quando presente, `goal` é ignorado e a "
                    "tarefa avança a partir de onde parou."
                ),
            },
        },
        "required": ["goal"],
    }

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        if not settings.computer_agent_enabled:
            return ToolResult.failure(
                "Computer Agent desabilitado (computer_agent_enabled=false)."
            )
        if context.db is None or context.provider is None:
            return ToolResult.failure(
                "computer_use requer um turno de agente com provider (db/provider)."
            )

        emit = context.extras.get("emit") if context.extras else None
        goal = arguments.get("goal")
        session_id = arguments.get("session_id") or context.session_id
        task_id = arguments.get("task_id")

        if isinstance(task_id, str) and task_id.strip():
            task = store.get_store().get(task_id.strip())
            if task is None:
                return ToolResult.failure(
                    f"Tarefa {task_id!r} não encontrada para retomar."
                )
            task.session_id = session_id
        else:
            if not isinstance(goal, str) or not goal.strip():
                return ToolResult.failure("goal ausente")
            task = store.create_task(
                goal=goal.strip(),
                session_id=session_id,
                requested_by="agent",
                device_id=None,
            )
            store.get_store().save(task)

        agent = ComputerAgent()
        started_at = time.time()
        advances = 0
        try:
            while advances < _MAX_IN_TURN_ADVANCES:
                if task.status not in (
                    ComputerStatus.WAITING_CONFIRMATION,
                    ComputerStatus.EXECUTING,
                    ComputerStatus.PLANNING,
                    ComputerStatus.PERCEIVING,
                    ComputerStatus.RECOVERING,
                    ComputerStatus.OBSERVING,
                    ComputerStatus.VERIFYING,
                    ComputerStatus.IDLE,
                ):
                    break
                terminal_before = task.status
                task = await agent.advance(context.db, context.provider, task, sink=emit)
                advances += 1
                if terminal_before in (
                    ComputerStatus.WAITING_CONFIRMATION,
                    ComputerStatus.EXECUTING,
                ) and task.status == ComputerStatus.WAITING_CONFIRMATION:
                    # Parou esperando decisão do usuário — sai do turno.
                    break
        except Exception:  # noqa: BLE001 — nunca derruba o turno de chat
            logger.exception("computer_use: erro ao avançar o agente")
            return ToolResult.failure("Falha ao executar o Computer Agent.")

        duration_ms = int((time.time() - started_at) * 1000)
        status = task.status.value

        if status == ComputerStatus.COMPLETED.value:
            return ToolResult.success(_json(self._summarize(task, duration_ms)))
        if status == ComputerStatus.CANCELLED.value:
            return ToolResult.failure(
                f"Tarefa cancelada: {task.error or 'cancelamento solicitado'}"
            )
        if status == ComputerStatus.FAILED.value:
            return ToolResult.failure(
                f"Tarefa falhou: {task.error or 'erro desconhecido'}"
            )
        if status == ComputerStatus.WAITING_CONFIRMATION.value:
            return ToolResult.success(
                _json(
                    {
                        "task_id": task.task_id,
                        "status": "waiting_confirmation",
                        "message": (
                            "A tarefa aguarda sua confirmação para continuar. A "
                            "decisão já foi solicitada via aprovação. Depois de "
                            "decidida, retome chamando `computer_use` com o mesmo "
                            "`task_id`."
                        ),
                        "waiting": True,
                        "task": _task_public(task),
                        "_approvals": _pending_approvals(context.db, task),
                    }
                )
            )

        # EXECUTANDO/NÃO terminal após limite in-turn: devolve estado para o
        # modelo continuar num próximo turno via task_id.
        return ToolResult.success(
            _json(
                {
                    "task_id": task.task_id,
                    "status": status,
                    "message": (
                        "A tarefa continua em execução. Retome chamando "
                        "`computer_use` com o mesmo `task_id` para avançar."
                    ),
                    "task": _task_public(task),
                    "continued": True,
                }
            )
        )

    # ------------------------------------------------------------------ utils

    def _summarize(self, task, duration_ms: int) -> dict:
        text = "\n".join(
            a.get("summary", "")
            for a in (task.actions or [])[-10:]
            if a.get("summary")
        )
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "goal": task.goal[:200],
            "steps_taken": task.steps_total,
            "actions_taken": task.actions_total,
            "verifications_ok": sum(
                1 for v in (task.verification_results or []) if v.get("ok")
            ),
            "recoveries": task.recoveries,
            "duration_ms": duration_ms,
            "applied_summary": text[:800] or "sem ações aplicadas (observação)",
            "task": _task_public(task),
        }


def _json(payload: dict) -> str:
    """Serializa o resultado da tool em string (contrato `ToolResult.output`)."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def _task_public(task) -> dict:
    return {
        "task_id": task.task_id,
        "goal": (task.goal or "")[:200],
        "status": task.status.value,
        "autonomy": task.autonomy.value,
        "steps_total": task.steps_total,
        "actions_total": task.actions_total,
        "step_index": task.step_index,
        "plan": [a.to_dict() for a in (task.plan or [])],
        "last_action_summary": task.last_action_summary,
        "error": task.error,
        "completed_at": task.completed_at,
    }


def _pending_approvals(db, task):
    """Retorna os approvals pendentes da tarefa (para o frontend exibir)."""
    if db is None:
        return []
    try:
        from app.services import approvals as approval_service

        out = []
        for approval_id in task.pending_approval_ids:
            approval = approval_service.get_approval(db, approval_id)
            if approval is not None:
                out.append(
                    approval_service.to_out(approval).model_dump(mode="json")
                )
        return out
    except Exception:  # noqa: BLE001 — nunca quebra o turno
        return []