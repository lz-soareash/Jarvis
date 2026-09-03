"""Aprovações (Fase 4 — Permissions): ciclo de vida dos pedidos de autorização.

O agente cria um pedido quando o modelo propõe uma ferramenta que exige
confirmação (nível ≥ 2) e pausa o turno. A decisão do usuário é registrada aqui
e aplicada na retomada (`run_agent`), que transforma a decisão em resultado de
ferramenta para o modelo.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.enums import ApprovalStatus
from app.models import ApprovalRequest, utcnow
from app.schemas.approvals import ApprovalOut

logger = logging.getLogger("jarvis.approvals")


def create_approval(
    db: OrmSession,
    *,
    session_id: str,
    tool_name: str,
    arguments: dict,
    permission_level: int,
    risk: str,
) -> ApprovalRequest:
    """Cria um pedido de aprovação pendente para a sessão (com expiração)."""
    approval = ApprovalRequest(
        session_id=session_id,
        tool_name=tool_name,
        arguments=json.dumps(arguments, ensure_ascii=False),
        risk=risk,
        permission_level=permission_level,
        status=ApprovalStatus.PENDING.value,
        expires_at=utcnow() + timedelta(seconds=settings.approval_ttl_seconds),
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    logger.info("Aprovação requisitada: %s (sessão %s)", tool_name, session_id)
    return approval


def get_approval(db: OrmSession, approval_id: str) -> ApprovalRequest | None:
    return db.get(ApprovalRequest, approval_id)


def pending_for_session(db: OrmSession, session_id: str | None = None) -> list[ApprovalRequest]:
    stmt = (
        select(ApprovalRequest)
        .where(ApprovalRequest.status == ApprovalStatus.PENDING.value)
        .order_by(ApprovalRequest.created_at.asc())
    )
    if session_id is not None:
        stmt = stmt.where(ApprovalRequest.session_id == session_id)
    return list(db.scalars(stmt).all())


def decided_unapplied_for_session(db: OrmSession, session_id: str) -> list[ApprovalRequest]:
    """Decisões já dadas (approved/denied) ainda não aplicadas ao histórico do agente."""
    stmt = (
        select(ApprovalRequest)
        .where(
            ApprovalRequest.session_id == session_id,
            ApprovalRequest.status.in_(
                [ApprovalStatus.APPROVED.value, ApprovalStatus.DENIED.value]
            ),
            ApprovalRequest.applied.is_(False),
        )
        .order_by(ApprovalRequest.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def _as_naive_utc(dt: datetime) -> datetime:
    """SQLite devolve DateTime(timezone=True) como naive (UTC) na leitura."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def is_expired(approval: ApprovalRequest) -> bool:
    return _as_naive_utc(datetime.now(timezone.utc)) > _as_naive_utc(approval.expires_at)


def mark_applied(db: OrmSession, approval: ApprovalRequest) -> None:
    approval.applied = True
    db.commit()


def mark_decided(
    db: OrmSession, approval: ApprovalRequest, approved: bool
) -> tuple[ApprovalRequest, bool]:
    """Registra a decisão do usuário de forma ATÔMICA (Fase 12.4-R1).

    Usa `UPDATE ... WHERE status = pending` para que, sob decisões concorrentes
    {A:APPROVE + B:APPROVE} (ou decisão posterior), apenas UMA efetive a decisão:
    o retorno `True` indica que ESTA chamada foi a vencedora. A outra observa 0
    linhas alteradas e deve ser tratada pelo chamador como "já decidido".
    """
    rows = db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == approval.id)
        .where(ApprovalRequest.status == ApprovalStatus.PENDING.value)
        .values(
            status=(
                ApprovalStatus.APPROVED.value
                if approved
                else ApprovalStatus.DENIED.value
            ),
            responded_at=utcnow(),
        )
    )
    if rows.rowcount != 1:
        db.rollback()
        db.refresh(approval)
        logger.info(
            "Aprovação %s já decidida (concorrente): %s", approval.id, approval.status
        )
        return approval, False
    db.commit()
    db.refresh(approval)
    logger.info("Aprovação %s (%s) pelo usuário", approval.id, approval.status)
    return approval, True


def to_out(approval: ApprovalRequest) -> ApprovalOut:
    return ApprovalOut(
        id=approval.id,
        session_id=approval.session_id,
        tool_name=approval.tool_name,
        arguments=approval.arguments_dict,
        risk=approval.risk,
        permission_level=approval.permission_level,
        status=approval.status,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
    )