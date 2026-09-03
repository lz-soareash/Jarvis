"""Serviço de Pairing (Fase 12.2): códigos curtos seguros.

Design de segurança:
- O código (10 dígitos, TTL 10min) é armazenado apenas como hash (`sha256:`);
  o valor bruto retorna ao chamador apenas na criação, para exibição única.
- Single-use: o consume é atômico (`UPDATE ... WHERE status=ACTIVE` + rowcount)
  para impedir dupla emissão de credencial em corrida.
- Anti brute-force em camadas: (1) gate global token-bucket por processo
  (independente de IP); (2) teto de pairings ativos; (3) contador de tentativas
  por pedido quando o chamador referencia o `pairing_id` (com `pairing_id`
  errado, tentativas não são atribuídas a um pedido alvo); (4) espaço 10^10.
- `device_id` é sempre emitido pelo servidor; o cliente envia apenas `name`.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.enums import DeviceStatus, DeviceType, PairingStatus
from app.models.remote import Device, PairingRequest
from app.models.session import ensure_utc, utcnow
from app.remote.crypto import derive_secret, generate_pairing_code, verify_secret
from app.remote.credentials import issue_credential
from app.remote.devices import create_device, json_meta
from app.remote.identity_events import (
    AUDIT_PAIRING_CREATED,
    AUDIT_PAIRING_FAILED,
    AUDIT_PAIRING_SUCCEEDED,
    OPS_PAIRING_CREATED,
    OPS_PAIRING_FAILED,
    OPS_PAIRING_SUCCEEDED,
    log_identity_event,
)
from app.remote.ratelimit import RateLimiter

logger = logging.getLogger("jarvis.remote")

_OPEN_STATUSES = (PairingStatus.CREATED.value, PairingStatus.ACTIVE.value)

# Gate global anti brute-force (por processo; não depende de IP).
_gate = RateLimiter(
    settings.remote_pairing_gate_capacity,
    settings.remote_pairing_gate_refill,
)


class PairingError(ValueError):
    """Base dos erros de pairing (mensagens sem qualquer segredo)."""


class PairingInvalid(PairingError):
    """Código inexistente, já consumido ou formato inválido."""


class PairingExpired(PairingError):
    """Código venceu (TTL) ou o pedido foi bloqueado por tentativas."""


class PairingLocked(PairingError):
    """O pedido excedeu o limite de tentativas e foi bloqueado."""


class PairingRateLimited(PairingError):
    """Submissões excederam o limite global de tentativas."""


class PairingLimitExceeded(PairingError):
    """Atingiu o teto de pedidos de pairing ativos."""


def _fail(reason: str, meta: dict[str, Any] | None = None) -> None:
    log_identity_event(
        audit_action=AUDIT_PAIRING_FAILED,
        ops_event=OPS_PAIRING_FAILED,
        allowed=False,
        status="failed",
        meta=meta or {"reason": reason},
    )


def create_pairing(
    db: OrmSession, *, metadata: dict[str, Any] | None = None
) -> tuple[PairingRequest, str]:
    """Cria um pedido de pairing e retorna (request, code) — código exibido uma vez."""
    _cleanup_expired(db)
    active = active_pairing_count(db)
    if active >= settings.remote_pairing_max_active:
        _fail("limit_active_pairings", {"active": active})
        raise PairingLimitExceeded(
            f"atingiu o teto de pairings ativos ({settings.remote_pairing_max_active})"
        )

    code = generate_pairing_code()
    request = PairingRequest(
        code_hash=derive_secret(code),
        status=PairingStatus.CREATED.value,
        expires_at=utcnow().replace(microsecond=0) + _ttl(),
        metadata_json=json_meta(metadata),
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    log_identity_event(
        audit_action=AUDIT_PAIRING_CREATED,
        ops_event=OPS_PAIRING_CREATED,
        meta={"pairing_id": request.id, "expires_at": request.expires_at},
    )
    return request, code


def submit_code(
    db: OrmSession,
    *,
    code: str,
    device_name: str,
    device_type: str = DeviceType.DESKTOP.value,
    pairing_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[Device, str]:
    """Valida o código e, em sucesso, cria o device + emite a credencial.

    Retorna (device, token_bruto) — o token só é visto uma vez.
    """
    code = (code or "").strip()
    if (
        len(code) != settings.remote_pairing_code_length
        or not code.isdigit()
        or not device_name.strip()
    ):
        _fail("invalid_format", {"reason": "formato do código ou nome inválido"})
        raise PairingInvalid("código ou nome de device inválido")

    if not _gate.allow():
        _fail("rate_limited")
        raise PairingRateLimited("muitas tentativas de pairing; aguarde e tente novamente")

    # -- localiza o pedido (por pairing_id explícito ou por hash do código) -----
    request: PairingRequest | None = None
    if pairing_id:
        request = db.get(PairingRequest, pairing_id)
        if request is None:
            _fail("pairing_not_found", {"pairing_id": pairing_id})
            raise PairingInvalid("pedido de pairing não encontrado")
        if request.status in (PairingStatus.CONSUMED.value, PairingStatus.REVOKED.value):
            _fail("already_consumed", {"pairing_id": pairing_id})
            raise PairingInvalid("pedido de pairing já utilizada ou revogado")
        if request.status == PairingStatus.EXPIRED.value:
            if request.attempts >= settings.remote_pairing_max_attempts:
                _fail("attempts_locked", {"pairing_id": pairing_id})
                raise PairingLocked("pedido de pairing bloqueado por excesso de tentativas")
            _fail("expired", {"pairing_id": pairing_id})
            raise PairingExpired("pedido de pairing expirado")
        _register_attempt(db, request)
    else:
        request = _find_open_by_hash(db, derive_secret(code))
        if request is None:
            _fail("invalid_code")
            raise PairingInvalid("código de pairing inválido")

    # -- verificação (apenas fluxos que ainda têm pedido aberto chegam aqui) ----
    if not verify_secret(code, request.code_hash):
        db.refresh(request)
        if request.attempts >= settings.remote_pairing_max_attempts:
            request.status = PairingStatus.EXPIRED.value
            request.revoked_at = utcnow()
            db.commit()
            _fail("attempts_locked", {"pairing_id": request.id})
            raise PairingLocked("pedido de pairing bloqueado por excesso de tentativas")
        _fail("wrong_code", {"pairing_id": request.id})
        raise PairingInvalid("código de pairing inválido")

    if ensure_utc(request.expires_at) <= utcnow():
        request.status = PairingStatus.EXPIRED.value
        request.revoked_at = utcnow()
        db.commit()
        _fail("expired", {"pairing_id": request.id})
        raise PairingExpired("código de pairing expirado")

    consumed = _consume(db, request)
    if not consumed:
        _fail("already_consumed", {"pairing_id": request.id})
        raise PairingInvalid("pedido de pairing já utilizada")

    device = create_device(
        db,
        name=device_name.strip()[:120],
        device_type=device_type,
        status=DeviceStatus.ACTIVE.value,
        metadata=metadata,
    )
    credential, token = issue_credential(db, device)

    request.device_id = device.id
    db.commit()

    log_identity_event(
        audit_action=AUDIT_PAIRING_SUCCEEDED,
        ops_event=OPS_PAIRING_SUCCEEDED,
        meta={"pairing_id": request.id, "device_id": device.id},
    )
    # device_created / credential_created já são auditados por create_device()
    # e issue_credential() — evitando duplicidade no histórico.
    return device, token


def get_pairing(db: OrmSession, pairing_id: str) -> PairingRequest | None:
    return db.get(PairingRequest, pairing_id)


def list_pairings(
    db: OrmSession, *, active_only: bool = False, limit: int = 100
) -> list[PairingRequest]:
    stmt = select(PairingRequest).order_by(PairingRequest.created_at.desc()).limit(limit)
    if active_only:
        stmt = stmt.where(PairingRequest.status.in_(_OPEN_STATUSES))
    return list(db.scalars(stmt).all())


def active_pairing_count(db: OrmSession) -> int:
    _cleanup_expired(db)
    rows = list(
        db.scalars(
            select(PairingRequest).where(PairingRequest.status.in_(_OPEN_STATUSES))
        ).all()
    )
    return len(rows)


def _find_open_by_hash(db: OrmSession, code_hash: str) -> PairingRequest | None:
    _cleanup_expired(db)
    return db.scalar(
        select(PairingRequest).where(
            PairingRequest.code_hash == code_hash,
            PairingRequest.status.in_(_OPEN_STATUSES),
        )
    )


def _register_attempt(db: OrmSession, request: PairingRequest) -> None:
    """Soma uma tentativa ao pedido (o bloqueio ocorre na falha, ver submit)."""
    request.attempts += 1
    db.commit()
    db.refresh(request)


def _consume(db: OrmSession, request: PairingRequest) -> bool:
    """Consumo atômico single-use: só UMA chamada obtém rowcount=1."""
    result = db.execute(
        update(PairingRequest)
        .where(
            PairingRequest.id == request.id,
            PairingRequest.status.in_(_OPEN_STATUSES),
        )
        .values(
            status=PairingStatus.CONSUMED.value,
            consumed_at=utcnow(),
        )
    )
    db.commit()
    return result.rowcount == 1


def _cleanup_expired(db: OrmSession) -> None:
    """Marca como EXPIRED pedidos abertos vencidos (TTL). Idempotente, leve."""
    now = utcnow()
    rows = db.scalars(
        select(PairingRequest).where(
            PairingRequest.status.in_(_OPEN_STATUSES),
            PairingRequest.expires_at <= now,
        )
    ).all()
    for row in rows:
        row.status = PairingStatus.EXPIRED.value
        row.revoked_at = now
    if rows:
        db.commit()


def _ttl():
    from datetime import timedelta

    return timedelta(seconds=settings.remote_pairing_ttl_seconds)


def reset_pairing_gate() -> None:
    """Zera o gate global (usado em testes para cenários de rate limit)."""
    global _gate
    _gate = RateLimiter(
        settings.remote_pairing_gate_capacity,
        settings.remote_pairing_gate_refill,
    )