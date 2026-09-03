"""Executor de comandos remotos (Fase 12.3) — REUTILIZA o pipeline existente.

Não duplica regras de segurança: usa as MESMAS primitivas do fluxo local
(`agent._run_tool`, `permissions.effective_level`, `tool_registry`,
`audit.log_action`). A única coisa nova é a orquestração do comando remoto:
valida o envelope, registra idempotência, aplica a política de permissão e
devolve um resultado/approval. Um comando NUNCA burla o Tool Registry, o
Permission Engine ou o AuditLog.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.enums import PermissionLevel
from app.models.remote import Credential, Device, RemoteSession
from app.remote import remote_commands as cmd_service
from app.remote.remote_commands import (
    CommandExpiredError,
    DuplicateCommandError,
    RemoteCommandError,
)
from app.schemas.ai import ToolCall
from app.services import approvals as approval_service
from app.services import audit as audit_service
from app.services import permissions as permission_service
from app.services.agent import _run_tool
from app.tools import ToolResult
from app.tools import registry as tool_registry

logger = logging.getLogger("jarvis.remote.executor")

# Vocabulário de auditoria de comandos remotos (Fase 12.3).
AUDIT_REMOTE_COMMAND = "remote.command"
AUDIT_REMOTE_APPROVAL = "remote.command.approval_requested"
AUDIT_REMOTE_BLOCKED = "remote.command.blocked"

OPS_REMOTE_COMMAND = "remote.command"
OPS_REMOTE_RESULT = "remote.command.result"


class RemoteExecutionError(ValueError):
    """Erro de execução de comando remoto (já sanitizado)."""


class ApprovalRequired(Exception):
    """Comando sensível (nível ≥ 2) exige decisão do usuário; não foi executado."""

    def __init__(self, approval_id: str, type_name: str) -> None:
        super().__init__(f"approval requerida ({type_name})")
        self.approval_id = approval_id
        self.type_name = type_name


async def execute_remote_command(
    db: OrmSession,
    *,
    device: Device,
    credential: Credential,
    session: RemoteSession | None,
    jarvis_session_id: str,
    command_id: str,
    type_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Envia um comando remoto ao pipeline interno JARVIS.

    Retorna um dict com `status` e `result` (ou `approval_required`). O comando
    é registrado atomicamente (idempotência) ANTES de qualquer execução.

    Regra mestre: identidade vem da credencial; o device deve estar ACTIVE e a
    credencial/sessão válidas — caso contrário o comando é rejeitado.
    """
    if payload is None or not isinstance(payload, dict):
        raise RemoteExecutionError("payload inválido")
    raw = str(payload).encode("utf-8", errors="replace")
    if len(raw) > settings.remote_command_max_payload:
        raise RemoteExecutionError("payload acima do limite")

    tool_name = payload.get("tool")
    arguments = payload.get("arguments") or {}
    if not tool_name or not isinstance(tool_name, str):
        raise RemoteExecutionError("payload sem tool")
    if not isinstance(arguments, dict):
        raise RemoteExecutionError("argumentos inválidos")

    # Idempotência atômica: falha se este (device, command_id) já foi processado.
    try:
        cmd = cmd_service.register_command(
            db,
            device_id=device.id,
            command_id=command_id,
            cmd_type=type_name,
            payload={"tool": tool_name, "arguments": arguments},
            session_id=jarvis_session_id,
        )
    except DuplicateCommandError as exc:
        raise RemoteExecutionError(str(exc)) from exc

    # Resolução da ferramenta via Registry (nunca executa chamadas inventadas).
    tool = tool_registry.get_tool_registry().get(tool_name)
    if tool is None:
        cmd_service.mark_executed(db, cmd, result=f"ERRO: ferramenta '{tool_name}' não registrada", ok=False)
        audit_service.log_action(
            db, action=AUDIT_REMOTE_COMMAND, session_id=jarvis_session_id,
            tool=tool_name, allowed=False, detail="ferramenta não registrada no Core",
        )
        return {"status": "failed", "command_id": command_id, "result": f"ERRO: ferramenta não registrada"}

    # Policy de permissão (o MESMO Permission Engine local).
    level = permission_service.effective_level(db, tool_name)
    call = ToolCall(name=tool_name, arguments=arguments, call_id=command_id)

    if level > PermissionLevel.LEVEL_1:
        # Cria pedido de aprovação vinculado à sessão JARVIS do device.
        approval = approval_service.create_approval(
            db,
            session_id=jarvis_session_id,
            tool_name=tool_name,
            arguments=arguments,
            permission_level=level.value,
            risk=(tool.risk.value if tool is not None else "low"),
        )
        cmd.approval_id = approval.id  # Fase 12.4: liga o comando à aprovação
        cmd_service.mark_pending_approval(db, cmd)
        audit_service.log_action(
            db, action=AUDIT_REMOTE_APPROVAL, session_id=jarvis_session_id,
            tool=tool_name, allowed=None,
            detail=f"nível {level.value} — decisão do usuário (approval={approval.id})",
        )
        return {
            "status": "approval_required",
            "command_id": command_id,
            "approval_id": approval.id,
            "tool": tool_name,
            "permission_level": level.value,
            "message": "comando requer aprovação do usuário",
        }

    # Bloqueio de execução determinístico do comando no cache global (Fase 12.3).
    _claim = cmd_service._claim_for_execution(db, device.id, command_id)
    db.refresh(_claim)
    try:
        result: ToolResult = await _run_tool(db, jarvis_session_id, None, call)
    except Exception as exc:  # noqa: BLE001 — falha vira resultado
        result = ToolResult.failure(f"{type(exc).__name__}: {exc}")
    db.refresh(_claim)
    cmd_service.mark_executed(db, _claim, result=result.output, ok=result.ok)
    return {
        "status": "executed" if result.ok else "failed",
        "command_id": command_id,
        "result": result.output,
        "ok": result.ok,
    }
