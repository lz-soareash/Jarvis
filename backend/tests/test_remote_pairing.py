"""Testes da Fase 12.2 — pairing seguro: single-use, TTL, tentativas, rate limit."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.session import utcnow
from app.remote.ratelimit import RateLimiter


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Criação
# ---------------------------------------------------------------------------

def test_create_pairing_returns_numeric_code_and_ttl(db):
    from app.remote.pairing import create_pairing, get_pairing

    request, code = create_pairing(db)
    assert len(code) == settings.remote_pairing_code_length
    assert code.isdigit()
    assert request.status == "created"
    assert get_pairing(db, request.id).id == request.id

    ttl = (request.expires_at - request.created_at).total_seconds()
    assert ttl <= settings.remote_pairing_ttl_seconds


def test_pairing_stores_only_hash(db):
    from app.models.remote import PairingRequest
    from app.remote.pairing import create_pairing

    _, code = create_pairing(db)
    stored = db.scalars(select(PairingRequest.code_hash)).all()
    assert stored[0] != code
    assert stored[0].startswith("sha256:")
    assert code not in stored[0]


def test_create_pairing_respects_active_cap(db, monkeypatch):
    from app.remote.pairing import (
        PairingLimitExceeded,
        create_pairing,
    )

    monkeypatch.setattr(settings, "remote_pairing_max_active", 1)
    create_pairing(db)
    with pytest.raises(PairingLimitExceeded):
        create_pairing(db)


def test_expired_pairings_are_cleaned_on_create(db):
    from app.models.remote import PairingRequest
    from app.remote.pairing import create_pairing

    request, _ = create_pairing(db)
    request.expires_at = utcnow() - timedelta(seconds=5)
    db.commit()

    create_pairing(db)  # dispara cleanup
    db.refresh(request)
    assert request.status == "expired"


# ---------------------------------------------------------------------------
# Validação / consumo
# ---------------------------------------------------------------------------

def test_submit_code_success_creates_device_and_token(db):
    from app.models.remote import Credential, Device, PairingRequest
    from app.remote.pairing import PairingInvalid, create_pairing, submit_code

    request, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name="Meu Phone")

    assert device.status == "active"
    assert device.id != request.id
    assert token and len(token) >= 40

    db.refresh(request)
    assert request.status == "consumed"
    assert request.device_id == device.id
    assert request.consumed_at is not None

    stored = db.scalars(select(Credential.token_hash)).all()
    assert stored[0] != token and stored[0].startswith("sha256:")

    devices = db.scalars(select(Device)).all()
    assert len(devices) == 1
    assert devices[0].device_type == "desktop"


def test_pairing_is_single_use(db):
    from app.remote.pairing import PairingInvalid, create_pairing, submit_code

    _, code = create_pairing(db)
    submit_code(db, code=code, device_name="Phone")

    with pytest.raises(PairingInvalid):
        submit_code(db, code=code, device_name="Phone 2")


def test_submit_rejects_bad_format(db):
    from app.remote.pairing import PairingInvalid, create_pairing, submit_code

    _, code = create_pairing(db)
    with pytest.raises(PairingInvalid):
        submit_code(db, code="123", device_name="Phone")
    with pytest.raises(PairingInvalid):
        submit_code(db, code=code[:9] + "x", device_name="Phone")
    with pytest.raises(PairingInvalid):
        submit_code(db, code=code, device_name="   ")


def _wrong_code(code: str) -> str:
    """Variação garantidamente DIFERENTE do código (muda o último dígito)."""
    return code[:-1] + str((int(code[-1]) + 1) % 10)


def test_wrong_code_increments_attempts_but_allows_retry(db):
    from app.remote.pairing import PairingInvalid, create_pairing, get_pairing, submit_code

    request, code = create_pairing(db)
    wrong = _wrong_code(code)

    with pytest.raises(PairingInvalid):
        submit_code(db, code=wrong, device_name="Phone", pairing_id=request.id)
    db.refresh(request)
    assert request.attempts == 1
    assert request.status == "created"

    _, token = submit_code(db, code=code, device_name="Phone", pairing_id=request.id)
    assert token
    db.refresh(request)
    assert request.status == "consumed"


def test_attempts_lock_blocks_even_correct_code(db, monkeypatch):
    from app.remote.pairing import (
        PairingInvalid,
        PairingLocked,
        create_pairing,
        submit_code,
    )

    monkeypatch.setattr(settings, "remote_pairing_max_attempts", 2)
    request, code = create_pairing(db)
    wrong = _wrong_code(code)

    with pytest.raises(PairingInvalid):  # tentativa 1: falha, ainda aberto
        submit_code(db, code=wrong, device_name="Phone", pairing_id=request.id)
    with pytest.raises(PairingLocked):  # tentativa 2: atinge o teto → bloqueia
        submit_code(db, code=wrong, device_name="Phone", pairing_id=request.id)

    db.refresh(request)
    assert request.status == "expired"
    with pytest.raises(PairingLocked):
        submit_code(db, code=code, device_name="Phone", pairing_id=request.id)


def test_ttl_expiry_rejected(db):
    from app.remote.pairing import PairingExpired, create_pairing, submit_code

    request, code = create_pairing(db)
    request.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()

    with pytest.raises(PairingExpired):
        submit_code(db, code=code, device_name="Phone", pairing_id=request.id)


def test_global_rate_limit_gate(db, monkeypatch):
    from app.remote import pairing as pairing_mod
    from app.remote.pairing import (
        PairingRateLimited,
        create_pairing,
        reset_pairing_gate,
        submit_code,
    )

    _, code = create_pairing(db)
    pairing_mod._gate = RateLimiter(capacity=1.0, refill_per_second=1e-6)

    from app.remote.pairing import PairingInvalid

    # Consome o token único do bucket com uma falha real (código inexistente).
    with pytest.raises(PairingInvalid):
        submit_code(db, code="0" * 10, device_name="Phone")
    # Segunda submissão em seguida → gate global nega.
    with pytest.raises(PairingRateLimited):
        submit_code(db, code="0" * 10, device_name="Phone")

    reset_pairing_gate()  # restaura para os demais testes


def test_submit_payload_cannot_choose_device_id(db):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name="Phone")

    # O id veio do servidor (uuid4), não do payload do cliente.
    from uuid import UUID

    UUID(device.id)


# ---------------------------------------------------------------------------
# Auditoria sanitizada + não reutilização
# ---------------------------------------------------------------------------

def test_pairing_audit_never_contains_code_or_token(db):
    from app.models.governance import AuditLog
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name="Phone")

    rows = db.query(AuditLog).filter(AuditLog.action.like("pairing%")).all()
    assert rows, "esperava eventos de pairing"
    for row in rows:
        assert code not in (row.detail or "")
        assert token not in (row.detail or "")
        assert "sha256:" not in (row.detail or "")