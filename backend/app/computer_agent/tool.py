"""Fase 19 — tool `computer_use` (Computer Agent no Tool Engine).

O modelo delega UMA tarefa de Computer Use (objetivo + contexto). O agente então
observa, planeja, age e verifica DENTRO do pipeline governado by Permission
Engine / Auditoria / aprovações — nunca executa fora do ToolRegistry.

A segurança respeita os tetos configurados (`computer_agent_*`) e o nível de
autonomia máximo vem do usuário/config, jamais do modelo.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.enums import PermissionLevel, RiskLevel
from app.tools.base import Tool, ToolContext, ToolResult

from . import store


class ComputerUseTool(Tool):
    """Delega um objetivo de Computer Use ao Computer Agent (loop observação→ação)."""

    name = "computer_use"
    description = (
        "Delega um OBJETIVO de computador para o Computer Agent, que observa a "
        "tela, planeja passos, executa ações e verifica o resultado de forma "
        "autônoma. Forneça `goal` claro e escopo limitado. Ações seguem "
        "autonomia configurada e pedem confirmação via aprovações quando "
        "necessário."
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
        },
        "required": ["goal"],
    }

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        goal = arguments.get("goal")
        session_id = arguments.get("session_id")

        if not isinstance(goal, str) or not goal.strip():
            return ToolResult.failure("goal ausente")
        if not settings.computer_agent_enabled:
            return ToolResult.failure(
                "Computer Agent desabilitado (computer_agent_enabled=false)."
            )

        task = store.create_task(
            goal=goal.strip(),
            session_id=session_id or context.session_id,
            requested_by="agent",
            device_id=None,
        )
        store.get_store().save(task)
        return ToolResult.success(
            {
                "task_id": task.task_id,
                "status": task.status.value,
                "autonomy": task.autonomy.value,
                "enabled": True,
            }
        )