"""Registro persistente de comandos remotos (Fase 12.3).

Idempotência atômica via coluna UNIQUE `(device_id, command_id)` em
`remote_commands`: duas chegadas simultâneas do mesmo command_id não executam
duas vezes (o INSERT falharia no segundo). Os estados REGISTERED /
PENDING_APPROVAL / EXECUTING / EXECUTED / FAILED / EXPIRED permitem rastrear e
rejeitar comandos duplicados, expirados ou em andamento.

Nada aqui registra payload sensível desnecessário nem secrets: apenas metadados
de comando e resultado textual sanitizado.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.enums import RemoteCommandStatus
from app.models.remote import RemoteCommand
from app.models.session import ensure_utc, utcnow

logger = logging.getLogger("jarvis.remote.commands")


class RemoteCommandError(ValueError):
    """Falha genérica de processamento de comando remoto."""


class DuplicateCommandError(RemoteCommandError):
    """O command_id já foi processado (não executa de novo)."""


class CommandExpiredError(RemoteCommandError):
    """Comando ultrapassou o TTL antes de ser processado."""


def register_command(
    db: OrmSession,
    *,
    device_id: str,
    command_id: str,
    cmd_type: str = "tool_call",
    payload: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> RemoteCommand:
    """Registra um comando de forma ATÔMICA (falha se já existir p/ este device).

    `expires_at = now + TTL`. Se o command_id já foi registrado para o device,
    levanta `DuplicateCommandError` (idempotência de transporte).
    """
    if not command_id or len(command_id) > 128:
        raise RemoteCommandError("command_id inválido")
    cmd = RemoteCommand(
        device_id=device_id,
        command_id=command_id,
        session_id=session_id,
        type=cmd_type,
        payload_json=json.dumps(payload or {}, ensure_ascii=False, default=str),
        status=RemoteCommandStatus.REGISTERED.value,
        created_at=utcnow(),
        expires_at=utcnow() + timedelta(seconds=settings.remote_command_ttl_seconds),
    )
    db.add(cmd)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateCommandError(
            f"comando duplicado (command_id={command_id})"
        ) from None
    db.refresh(cmd)
    return cmd


def get_command(db: OrmSession, device_id: str, command_id: str) -> RemoteCommand | None:
    return db.scalars(
        select(RemoteCommand).where(
            RemoteCommand.device_id == device_id,
            RemoteCommand.command_id == command_id,
        )
    ).first()


def _claim_for_execution(db: OrmSession, device_id: str, command_id: str) -> RemoteCommand:
    """Marca um comando como EXECUTING (apenas se ainda estiver REGISTERED).

    Usado para impedir que duas execuções concorrentes do mesmo command_id
    prossigam: a transição REGISTERED → EXECUTING é atômica sob o lock do agente
    + o INSERT inicial já garantiu a unicidade na chegada.
    """
    cmd = get_command(db, device_id, command_id)
    if cmd is None:
        raise RemoteCommandError(f"comando não registrado: {command_id}")
    if cmd.status not in (
        RemoteCommandStatus.REGISTERED.value,
        RemoteCommandStatus.PENDING_APPROVAL.value,
    ):
        raise DuplicateCommandError(f"comando já processado: {command_id} ({cmd.status})")
    if cmd.expires_at is not None and ensure_utc(cmd.expires_at) <= utcnow():
        cmd.status = RemoteCommandStatus.EXPIRED.value
        db.commit()
        raise CommandExpiredError(f"comando expirado: {command_id}")
    cmd.status = RemoteCommandStatus.EXECUTING.value
    db.commit()
    return cmd


def mark_executed(
    db: OrmSession,
    cmd: RemoteCommand,
    *,
    result: str = "",
    ok: bool = True,
) -> RemoteCommand:
    cmd.status = RemoteCommandStatus.EXECUTED.value if ok else RemoteCommandStatus.FAILED.value
    cmd.executed_at = utcnow()
    cmd.result_json = json.dumps({"ok": ok, "output": result[:2000]}, ensure_ascii=False)
    if not ok:
        cmd.error = result[:2000]
    db.commit()
    return cmd


def mark_pending_approval(db: OrmSession, cmd: RemoteCommand) -> RemoteCommand:
    cmd.status = RemoteCommandStatus.PENDING_APPROVAL.value
    db.commit()
    return cmd


def list_commands(
    db: OrmSession,
    *,
    device_id: str | None = None,
    active_only: bool = False,
    limit: int = 100,
) -> list[RemoteCommand]:
    stmt = select(RemoteCommand).order_by(RemoteCommand.created_at.desc()).limit(limit)
    if device_id is not None:
        stmt = stmt.where(RemoteCommand.device_id == device_id)
    if active_only:
        stmt = stmt.where(
            RemoteCommand.status.in_(
                [
                    RemoteCommandStatus.REGISTERED.value,
                    RemoteCommandStatus.PENDING_APPROVAL.value,
                    RemoteCommandStatus.EXECUTING.value,
                ]
            )
        )
    return list(db.scalars(stmt).all())


def command_meta(cmd: RemoteCommand) -> dict[str, Any]:
    """Metadados observáveis de um comando (jamais secretos)."""
    return {
        "id": cmd.id,
        "device_id": cmd.device_id,
        "command_id": cmd.command_id,
        "session_id": cmd.session_id,
        "type": cmd.type,
        "status": cmd.status,
        "created_at": cmd.created_at,
        "expires_at": cmd.expires_at,
        "executed_at": cmd.executed_at,
    }
