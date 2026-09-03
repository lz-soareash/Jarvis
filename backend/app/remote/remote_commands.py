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

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.enums import RemoteCommandStatus
from app.models.remote import RemoteCommand
from app.models.session import ensure_utc, utcnow

logger = logging.getLogger("jarvis.remote.commands")


def publish_command_event(cmd: RemoteCommand, status: str | None = None) -> None:
    """Fase 12.5 — streama a transição de comando ao barramento SSE (sanitizado)."""
    from app.remote.events import publish_event

    state = status or (cmd.status if getattr(cmd, "status", None) else "unknown")
    publish_event(
        "remote.command." + str(state),
        {
            "device_id": cmd.device_id,
            "command_id": cmd.command_id,
            "status": str(cmd.status) if getattr(cmd, "status", None) else str(state),
        },
    )


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
    publish_command_event(cmd, RemoteCommandStatus.REGISTERED.value)
    return cmd


def get_command(db: OrmSession, device_id: str, command_id: str) -> RemoteCommand | None:
    return db.scalars(
        select(RemoteCommand).where(
            RemoteCommand.device_id == device_id,
            RemoteCommand.command_id == command_id,
        )
    ).first()


def get_command_by_approval(db: OrmSession, approval_id: str) -> RemoteCommand | None:
    """Fase 12.4 — comando regido pela aprovação dada (qualquer estado).

    NÃO filtra por `status`: mesmo após concluído/negado o comando continua
    ligado à aprovação (1:1 via `approval_id` UNIQUE), permitindo que um
    pedido de decisão concorrente identifique corretamente o comando remoto e
    responda "já processado" em vez de cair no loop local do agente.
    """
    return db.scalars(
        select(RemoteCommand).where(RemoteCommand.approval_id == approval_id)
    ).first()


def _claim_for_execution(db: OrmSession, device_id: str, command_id: str) -> RemoteCommand:
    """Reclama um comando p/ execução de forma ATÔMICA (REGISTERED/
    PENDING_APPROVAL → EXECUTING), garantindo que só UM executor prossiga.

    Usa um único `UPDATE ... WHERE status IN (registered, pending_approval)`:
    a instrução é atômica no banco, então duas tentativas concorrentes — mesmo
    em conexões diferentes (SQLite WAL / file DB) — só permitem que UMA veja
    `rowcount == 1` (a outra observa 0 linhas e recebe `DuplicateCommandError`).
    Isso serve de fecho de concorrência {A:APPROVE + B:APPROVE} e impede
    re-execução de comandos já em EXECUTING/tratamento ou terminais.

    Comandos `EXECUTING` presos (crash entre claim e mark_executed) NÃO são
    reclamados automaticamente aqui: permanecem para recuperação explícita via
    `recover_executing` (resultado desconhecido → `INTERRUPTED`, sem re-exec).
    """
    now = utcnow()
    cmd = get_command(db, device_id, command_id)
    if cmd is None:
        raise RemoteCommandError(f"comando não registrado: {command_id}")
    if cmd.expires_at is not None and ensure_utc(cmd.expires_at) <= now:
        cmd.status = RemoteCommandStatus.EXPIRED.value
        db.commit()
        raise CommandExpiredError(f"comando expirado: {command_id}")
    if cmd.status not in (
        RemoteCommandStatus.REGISTERED.value,
        RemoteCommandStatus.PENDING_APPROVAL.value,
    ):
        raise DuplicateCommandError(f"comando já processado: {command_id} ({cmd.status})")

    claimed_rows = db.execute(
        update(RemoteCommand)
        .where(
            RemoteCommand.device_id == device_id,
            RemoteCommand.command_id == command_id,
            RemoteCommand.status.in_(
                [
                    RemoteCommandStatus.REGISTERED.value,
                    RemoteCommandStatus.PENDING_APPROVAL.value,
                ]
            ),
        )
        .values(status=RemoteCommandStatus.EXECUTING.value)
    )
    if claimed_rows.rowcount != 1:
        db.rollback()
        current = get_command(db, device_id, command_id)
        raise DuplicateCommandError(
            f"comando já processado: {command_id} ({current.status if current else '?'})"
        )
    db.commit()
    db.expire_all()
    claimed = get_command(db, device_id, command_id)
    if claimed is None:  # pragma: no cover — invariante interno
        raise RemoteCommandError(f"comando não registrado: {command_id}")
    publish_command_event(claimed, RemoteCommandStatus.EXECUTING.value)
    return claimed


def mark_interrupted(
    db: OrmSession,
    device_id: str,
    command_id: str,
    *,
    reason: str = "execução interrompida / resultado desconhecido",
) -> RemoteCommand:
    """Transição atômica EXECUTING → INTERRUPTED (Fase 12.4-R1).

    Usada para registrar que um comando ficou preso/resultado desconhecido sem
    re-executar (não idempotente). Apenas estados EXECUTING são aceitos; um
    comando já em estado terminal não é sobrescrito.
    """
    rows = db.execute(
        update(RemoteCommand)
        .where(
            RemoteCommand.device_id == device_id,
            RemoteCommand.command_id == command_id,
            RemoteCommand.status == RemoteCommandStatus.EXECUTING.value,
        )
        .values(
            status=RemoteCommandStatus.INTERRUPTED.value,
            error=reason[:2000],
        )
    )
    if rows.rowcount != 1:
        db.rollback()
        cmd = get_command(db, device_id, command_id)
        if cmd is None:
            raise RemoteCommandError(f"comando não registrado: {command_id}")
        raise RemoteCommandError(f"comando não está em execução: {command_id} ({cmd.status})")
    db.commit()
    db.expire_all()
    claimed = get_command(db, device_id, command_id)
    if claimed is None:  # pragma: no cover — invariante interno
        raise RemoteCommandError(f"comando não registrado: {command_id}")
    publish_command_event(claimed)
    return claimed


def find_stuck_executing(
    db: OrmSession,
    *,
    device_id: str | None = None,
    older_than_seconds: int = 0,
) -> list[RemoteCommand]:
    """Encontra comandos presos em EXECUTING ('stuck') p/ recuperação manual.

    Retorna apenas comandos naquele estado (não re-executa nada). O chamador
    decide, por intervenção explícita, chamar `recover_executing`.
    """
    stmt = select(RemoteCommand).where(
        RemoteCommand.status == RemoteCommandStatus.EXECUTING.value
    )
    if device_id is not None:
        stmt = stmt.where(RemoteCommand.device_id == device_id)
    if older_than_seconds > 0:
        stmt = stmt.where(
            RemoteCommand.created_at <= utcnow()
        )
    return list(db.scalars(stmt).all())


def recover_executing(
    db: OrmSession,
    device_id: str,
    command_id: str,
    *,
    reason: str = "execução interrompida / resultado desconhecido",
) -> RemoteCommand:
    """Recupera um comando preso em EXECUTING sem re-executar (Fase 12.4-R1).

    Marca `INTERRUPTED` (resultado desconhecido) para que o rastreio não exiba
    um comando eternamente 'em execução'. NÃO re-executa a operação: quem quiser
    rodar de novo precisa registrar um novo comando explicitamente.
    """
    return mark_interrupted(db, device_id, command_id, reason=reason)


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
    publish_command_event(cmd)
    return cmd


def mark_pending_approval(db: OrmSession, cmd: RemoteCommand) -> RemoteCommand:
    cmd.status = RemoteCommandStatus.PENDING_APPROVAL.value
    db.commit()
    publish_command_event(cmd, RemoteCommandStatus.PENDING_APPROVAL.value)
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


def command_detail(cmd: RemoteCommand) -> dict[str, Any]:
    """Detalhe público de um comando (Fase 12.4) p/ o endpoint de consulta.

    Inclui o resultado sanitizado (sem payload de entrada cru jamais retornado
    fora do necessário). `result` é o output textual do tool; `error` só quando
    houve falha/negação.
    """
    detail = command_meta(cmd)
    detail["result"] = None
    detail["error"] = None
    if cmd.result_json:
        try:
            detail["result"] = json.loads(cmd.result_json).get("output")
        except (ValueError, TypeError):  # noqa: BLE001
            detail["result"] = None
    detail["error"] = cmd.error
    return detail
