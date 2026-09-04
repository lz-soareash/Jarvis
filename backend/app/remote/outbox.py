"""Fila de re-entrega (Fase 12.7) — outbox persistente de resultados.

O push de `COMMAND_RESULT` é best-effort: se o device estiver offline no momento,
o resultado fica AGUARDANDO re-entrega. Esta camada persiste esses resultados num
outbox transacional e os re-entrega de forma direcionada quando o `RemoteAgent`
(re)conecta — sem depender da conexão que falhou e sem perder o resultado.

O outbox é **idempotente** por `(device_id, command_id)`: um comando tem no máximo
uma entrada pendente, evitando reenvios duplicados.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.models.remote import RemoteOutbox
from app.models.session import ensure_utc, utcnow

logger = logging.getLogger("jarvis.remote.outbox")


def enqueue_result(
    db: OrmSession,
    *,
    device_id: str,
    command_id: str,
    payload: dict[str, Any],
) -> RemoteOutbox:
    """Persiste um resultado a re-entregar (idempotente por device+command).

    Se já existir entrada para o par, retorna o existente (não duplica). O
    payload é serializado sanitizado (teto de tamanho) e nunca contém secrets.
    """
    existing = _get(db, device_id, command_id)
    if existing is not None:
        return existing

    entry = RemoteOutbox(
        device_id=device_id,
        command_id=command_id,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str)[:64_000],
        created_at=utcnow(),
    )
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _get(db, device_id, command_id)  # pragma: no cover — corrida rara
    db.refresh(entry)
    return entry


def list_undelivered(
    db: OrmSession, *, device_id: str | None = None
) -> list[RemoteOutbox]:
    """Entradas ainda não entregues (por device, opcional), na ordem de chegada."""
    stmt = select(RemoteOutbox).where(RemoteOutbox.delivered_at.is_(None))
    if device_id is not None:
        stmt = stmt.where(RemoteOutbox.device_id == device_id)
    stmt = stmt.order_by(RemoteOutbox.created_at.asc())
    return list(db.scalars(stmt).all())


def payload_of(entry: RemoteOutbox) -> dict[str, Any]:
    """Decodifica o payload sanitizado da entrada (nunca lança)."""
    try:
        parsed = json.loads(entry.payload_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):  # noqa: BLE001
        return {}


def mark_delivered(db: OrmSession, entry: RemoteOutbox) -> RemoteOutbox:
    """Marca a entrada como entregue (terminal para re-entrega)."""
    entry.delivered_at = utcnow()
    db.commit()
    return entry


def purge_delivered(
    db: OrmSession, *, older_than_days: int = 7
) -> int:
    """Remove entradas já entregues há mais de N dias (housekeeping).

    Fase 12.8: a limpeza agora usa DELETE direto no banco com filtro SQL
    (antes carregava todas as entregues em memória e filtrava em Python).
    """
    cutoff = ensure_utc(utcnow()) - timedelta(days=older_than_days)
    rows = db.execute(
        delete(RemoteOutbox).where(
            RemoteOutbox.delivered_at.is_not(None),
            RemoteOutbox.delivered_at <= cutoff,
        )
    )
    removed = rows.rowcount
    if removed:
        db.commit()
    return removed


def _get(db: OrmSession, device_id: str, command_id: str) -> RemoteOutbox | None:
    return db.scalars(
        select(RemoteOutbox).where(
            RemoteOutbox.device_id == device_id,
            RemoteOutbox.command_id == command_id,
        )
    ).first()
