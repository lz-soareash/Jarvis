"""Retomada de comandos remotos pós-aprovação (Fase 12.4).

Quando um comando remoto de nível ≥ 2 é pausado em PENDING_APPROVAL (Fase 12.3),
o JARVIS fica aguardando a decisão do usuário. Esta camada RESUME esse comando
exato: aplica a decisão (aprovado/negado) por um caminho QUE NUNCA depende da
conexão WebSocket — o resultado é persistido de forma transacional; a entrega
pela conexão é best-effort e fica a cargo do RemoteAgent (ver `deliver_result`).

Reutiliza o pipeline interno (Tool Registry → Permission Engine → AuditLog →
`agent._run_tool`), exatamente como o executor da 12.3. Não duplica loops de
agente nem regras de segurança.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.core.enums import ApprovalStatus
from app.models.remote import Device, RemoteCommand
from app.remote import remote_commands as cmd_service
from app.schemas.ai import ToolCall
from app.services import audit as audit_service
from app.services.agent import _run_tool
from app.tools import ToolResult

logger = logging.getLogger("jarvis.remote.resume")

AUDIT_REMOTE_APPROVED = "remote.command.approved"
AUDIT_REMOTE_DENIED = "remote.command.denied"


class RemoteResumeError(ValueError):
    """Falha na retomada de um comando remoto (mensagem já sanitizada)."""


def resolve_command_for_approval(
    db: OrmSession, approval_id: str
) -> RemoteCommand | None:
    """Localiza o comando remoto aguardando a aprovação dada."""
    return cmd_service.get_command_by_approval(db, approval_id)


async def resume_remote_command(
    db: OrmSession,
    *,
    command: RemoteCommand,
    device: Device,
    jarvis_session_id: str,
    approval_status: str,
) -> dict[str, Any]:
    """Aplica a decisão do usuário a um comando remoto pausado.

    - Aprovado: re-claim (PENDING_APPROVAL → EXECUTING), executa via `_run_tool`,
      marca EXECUTED/FAILED e audita `remote.command.approved`.
    - Negado: marca FAILED ("negado pelo usuário") e audita `remote.command.denied`.

    A retomada é puramente transacional (não depende da conexão WebSocket). O
    `command_id` identifica o comando; a identidade já foi autenticada na chegada
    do comando (Fase 12.3) e o device ACTIVE é revalidado pelo chamador.
    """
    if command.status != "pending_approval":
        raise RemoteResumeError(f"comando não está aguardando aprovação: {command.command_id} ({command.status})")

    denied = approval_status != ApprovalStatus.APPROVED.value
    if denied:
        denied_tool = (_safe_payload(command) or {}).get("tool") or command.type
        cmd_service.mark_executed(
            db,
            command,
            result="O usuário negou a execução deste comando remoto.",
            ok=False,
        )
        audit_service.log_action(
            db,
            action=AUDIT_REMOTE_DENIED,
            session_id=jarvis_session_id,
            tool=denied_tool,
            allowed=False,
            detail=f"comando {command.command_id} negado pelo usuário",
        )
        return {
            "status": "denied",
            "command_id": command.command_id,
            "result": "O usuário negou a execução deste comando remoto.",
            "ok": False,
        }

    payload = _safe_payload(command)
    tool_name = (payload or {}).get("tool")
    arguments = (payload or {}).get("arguments") or {}
    if not tool_name:
        raise RemoteResumeError("comando sem tool válida para retomada")

    try:
        claim = cmd_service._claim_for_execution(db, device.id, command.command_id)
    except cmd_service.DuplicateCommandError as exc:
        # Já processado ou em execução por outra decisão concorrente (fecho de
        # concorrência {A:APPROVE + B:APPROVE}): mensagem sanitizada p/ o cliente.
        raise RemoteResumeError("comando já processado ou em execução") from exc
    except cmd_service.CommandExpiredError as exc:
        raise RemoteResumeError("comando expirou antes da retomada") from exc
    db.refresh(claim)
    try:
        call = ToolCall(name=tool_name, arguments=arguments or {}, call_id=command.command_id)
        result: ToolResult = await _run_tool(db, jarvis_session_id, None, call)
    except Exception as exc:  # noqa: BLE001 — falha vira resultado
        result = ToolResult.failure(f"{type(exc).__name__}: {exc}")
    db.refresh(claim)
    cmd_service.mark_executed(db, claim, result=result.output, ok=result.ok)
    audit_service.log_action(
        db,
        action=AUDIT_REMOTE_APPROVED,
        session_id=jarvis_session_id,
        tool=tool_name,
        allowed=result.ok,
        detail=f"comando {command.command_id} aprovado e executado",
    )
    return {
        "status": "executed" if result.ok else "failed",
        "command_id": command.command_id,
        "result": result.output,
        "ok": result.ok,
    }


def _safe_payload(command: RemoteCommand) -> dict[str, Any]:
    """Lê o payload do comando da forma mais segura possível (sem secrets)."""
    try:
        import json

        raw = command.payload_json or "{}"
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):  # noqa: BLE001
        return {}
