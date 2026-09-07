"""Computer Action Layer (Fase 18) — ferramenta registrada no Tool Registry.

Única tool estruturada `computer_action`. Recebe `action` + `params` e
delega ao ActionExecutor (única porta de execução). Não há bypass aqui:
qualquer ação passa pelo pipeline completo.
"""

from __future__ import annotations

from app.core.enums import PermissionLevel, RiskLevel
from app.tools.base import Tool, ToolContext, ToolResult

from .executor import ActionExecutorOptions
from .models import ActionRequest
from .registry import get_action_registry

USER_MARKER = "@user"  # conflita com nada; não usado como segurança


class ComputerActionTool(Tool):
    """Execução controlada de ações de computador (mouse/teclado/janela)."""

    name = "computer_action"
    description = (
        "Executa uma ação controlada no computador (mouse, teclado, janela) "
        "após validação, política de segurança e autorização. Use action_type "
        "e params conforme os tipos suportados. Combinações perigosas são "
        "bloqueadas; dry_run=true executa a cadeia sem agir fisicamente."
    )
    permission_level = PermissionLevel.LEVEL_1
    risk = RiskLevel.MEDIUM

    parameters = {
        "type": "object",
        "properties": {
            "action_type": {
                "type": "string",
                "enum": [
                    "mouse_move", "click", "double_click", "right_click",
                    "scroll", "key_press", "hotkey", "type_text", "focus_window",
                ],
                "description": "Tipo de ação de computador",
            },
            "params": {
                "type": "object",
                "description": "Parâmetros da ação (validados por tipo)",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Valida e autoriza sem agir fisicamente",
            },
        },
        "required": ["action_type"],
    }

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        action_type_raw = arguments.get("action_type")
        params = arguments.get("params") or {}
        dry_run = bool(arguments.get("dry_run", False))

        if not isinstance(action_type_raw, str) or not action_type_raw:
            return ToolResult.failure("action_type ausente")

        try:
            from .executor import get_executor

            request = ActionRequest(
                action_type=action_type_raw,
                params=params,
                dry_run=dry_run,
                requested_by="agent",
                session_id=context.session_id,
            )
            opts = ActionExecutorOptions(
                requested_by="agent",
                session_id=context.session_id,
                db=context.db,
                cancelled_cb=(
                    (lambda: bool((context.extras or {}).get("cancelled")))
                    if hasattr(context, "extras")
                    else None
                ),
            )
            result = await get_executor().execute(request, opts)
            return ToolResult.success(result.to_dict())
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(str(e))