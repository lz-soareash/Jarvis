"""Fase 28 — Remote Operations: ferramenta `remote_operation` do agente.

O modelo propõe um PLANO de passos (estruturado) e o Core executa cada passo com
política segura: per-alvo resolve, capability/eval nível efetivo, L2 pausa com
`ApprovalRequest` vinculado à operação e L3 é bloqueado. A tool em si é LEVEL_1
(coordenadora) — a segurança real é por PASSO nas ferramentas subjacentes e no
Permission Engine existente.

Segurança:
- O LLM NUNCA escolhe device_id/transporte/tokens: apenas `target` amigável.
- Resultado sempre em JSON estruturado + sanitizado (`remote.operation.result`).
- Idempotência por `operation_id`: retry devolve o estado persistido.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.enums import PermissionLevel, RiskLevel
from app.remote import operations as ops_service
from app.tools.base import Tool, ToolContext, ToolResult


def _fail(message: str) -> ToolResult:
    return ToolResult(
        output=json.dumps(
            {"type": "remote.operation.result", "status": "failed", "error": message},
            ensure_ascii=False,
        ),
        ok=False,
    )


class RemoteOperationTool(Tool):
    name = "remote_operation"
    description = (
        "Coordena uma operação em múltiplos passos e dispositivos (ex.: abrir o "
        "YouTube no computador e depois o Chrome no celular). Cada passo é "
        "executado com a política de permissão existente; passos que exigem "
        "confirmação pausam aguardando decisão do usuário. Use 'target' como "
        "nome amigável ('computador'/'pc' ou o nome do celular) e 'action' no "
        "catálogo OpenAPI (OPEN_URL, OPEN_APP, DEVICE_INFO, BATTERY_STATUS, ...)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "Rótulo curto da operação (opcional).",
            },
            "operation_id": {
                "type": "string",
                "description": "ID de retomada/idempotência (opcional; reusa o estado existente).",
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "Alvo amigável: 'computador'/'pc' ou o nome de um celular pareado.",
                        },
                        "action": {
                            "type": "string",
                            "description": (
                                "Ação canônica: OPEN_URL, OPEN_APP, DEVICE_INFO, BATTERY_STATUS, "
                                "NETWORK_STATUS, MEDIA_STATUS, GET_SYSTEM_STATS, GET_SYSTEM_STATUS, "
                                "VIBRATE, SET_VOLUME, SET_BRIGHTNESS, CLOSE_APP."
                            ),
                        },
                        "params": {
                            "type": "object",
                            "description": "Parâmetros da ação (url, target/package_name p/ OPEN_APP, level...).",
                        },
                        "timeout_ms": {"type": "integer"},
                    },
                    "required": ["action"],
                },
                "minItems": 1,
                "maxItems": 8,
            },
        },
        "required": ["steps"],
    }
    permission_level = PermissionLevel.LEVEL_1
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments: Any) -> ToolResult:
        db = context.db
        if db is None:
            return _fail("Core sem acesso ao banco de operações.")

        raw_steps = arguments.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            return _fail("steps ausentes ou vazios.")
        if len(raw_steps) > ops_service._MAX_STEPS:  # noqa: SLF001
            return _fail(f"operação com mais de {ops_service._MAX_STEPS} passos.")

        clean: list[dict[str, Any]] = []
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            action_name = str(step.get("action") or "").strip()
            action = ops_service.get_action(action_name)
            if action is None:
                clean.append(
                    {
                        "target": (step.get("target") or "").strip() or None,
                        "action": action_name or "?",
                        "params": {},
                        "message": f"ação desconhecida: {action_name or '?'}",
                    }
                )
                continue
            timeout = step.get("timeout_ms")
            try:
                step_timeout = int(timeout) if timeout else None
            except (TypeError, ValueError):
                step_timeout = None
            clean.append(
                {
                    "target": (step.get("target") or "").strip() or None,
                    "action": action.name,
                    "params": ops_service._sanitize_params(step.get("params")),  # noqa: SLF001
                    "timeout_ms": step_timeout,
                }
            )

        session_id = context.session_id
        operation_id = arguments.get("operation_id")
        if operation_id is not None and not isinstance(operation_id, str):
            operation_id = None

        try:
            operation = ops_service.create_or_get_operation(
                db,
                session_id=session_id,
                requested_action=arguments.get("operation") or "",
                steps=clean,
                operation_id=operation_id,
            )
            outcome = await ops_service.run_operation(
                db, operation=operation, session_id=session_id
            )
        except ops_service.OperationError as exc:
            return _fail(str(exc))
        except Exception as exc:  # noqa: BLE001 — falha interna sanitizada
            return _fail(f"{type(exc).__name__}: {str(exc)[:240]}")

        payload = ops_service.to_payload(outcome)
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            ok=outcome.ok,
        )


def operations_tools() -> list[Tool]:
    return [RemoteOperationTool()]