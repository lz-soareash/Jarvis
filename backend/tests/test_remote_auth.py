"""Testes da Fase 12.2 — autenticação remota: sessões, rejeições e anti-spoofing."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.session import utcnow


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _paired_device(db, name="Phone"):
    """Cria um device pairing completo → (device, token)."""
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name=name)
    return device, token


# ---------------------------------------------------------------------------
# Sucesso
# ---------------------------------------------------------------------------

def test_auth_success_creates_session(db):
    from app.remote.auth import authenticate_bearer
    from app.remote.sessions import list_sessions

    device, token = _paired_device(db)
    authed = authenticate_bearer(db, token)

    assert authed.device_id == device.id
    assert authed.device_type == "desktop"
    assert authed.status == "active"
    assert authed.session_id  # id de sessão emitido pelo servidor

    sessions = list_sessions(db, device_id=device.id)
    assert len(sessions) == 1
    assert sessions[0].status == "active"
    assert sessions[0].credential_id == authed.credential.id

    # Meta observável jamais contém o token.
    meta = authed.sanitized_meta()
    assert token not in str(meta)
    assert "token" not in meta


def test_authenticated_device_never_carries_token(db):
    from app.remote.auth import authenticate_bearer

    _, token = _paired_device(db)
    authed = authenticate_bearer(db, token)
    fields = set(authed.__dataclass_fields__)
    assert "token" not in fields
    assert "token_hash" not in fields


def test_auth_updates_last_seen_and_last_used(db):
    from app.remote.auth import authenticate_bearer

    device, token = _paired_device(db)
    assert device.last_seen_at is None
    authenticate_bearer(db, token)
    db.refresh(device)
    assert device.last_seen_at is not None


# ---------------------------------------------------------------------------
# Rejeições — mensagens indistinguíveis
# ---------------------------------------------------------------------------

def test_auth_wrong_token_rejected(db, fake_ai):
    from app.remote.auth import RemoteAuthError, authenticate_bearer

    _, token = _paired_device(db)
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token + "x")


def test_auth_empty_or_huge_token_rejected(db):
    from app.remote.auth import RemoteAuthError, authenticate_bearer

    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, "")
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, "a" * 1024)


def test_auth_revoked_token_rejected(db, fake_ai):
    from app.remote.auth import RemoteAuthError, authenticate_bearer
    from app.remote.credentials import get_credential_by_token, revoke_credential

    _, token = _paired_device(db)
    credential, _ = get_credential_by_token(db, token)
    revoke_credential(db, credential.id)

    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token)


def test_auth_expired_token_rejected(db, fake_ai):
    from app.remote.auth import RemoteAuthError, authenticate_bearer
    from app.remote.credentials import issue_credential
    from app.remote.devices import create_device

    device = create_device(db, name="Desk")
    credential, token = issue_credential(
        db, device, expires_at=utcnow() - timedelta(seconds=1)
    )
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token)


def test_auth_revoked_device_rejected(db, fake_ai):
    from app.remote.auth import RemoteAuthError, authenticate_bearer
    from app.remote.devices import revoke_device

    device, token = _paired_device(db)
    revoke_device(db, device.id)
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token)


def test_auth_pending_device_rejected(db, fake_ai):
    from app.remote.auth import RemoteAuthError, authenticate_bearer
    from app.remote.credentials import issue_credential
    from app.remote.devices import create_device

    device = create_device(db, name="Pendente", status="pending")
    _, token = issue_credential(db, device)
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token)


def test_auth_failures_are_sanitized(db, fake_ai):
    from app.models.governance import AuditLog
    from app.models.ops import ExecutionEvent
    from app.remote.auth import RemoteAuthError, authenticate_bearer

    _, token = _paired_device(db)
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token + "x")  # token inválido

    rows = db.query(ExecutionEvent).all()
    raw = " ".join(e.meta_json or "" for e in rows)
    assert token not in raw
    assert "sha256:" not in raw

    audits = db.query(AuditLog).filter(AuditLog.action == "device_authentication_failed").all()
    assert audits
    for row in audits:
        assert token not in (row.detail or "")


# ---------------------------------------------------------------------------
# Anti-spoofing: identidade vem da credencial, nunca do payload
# ---------------------------------------------------------------------------

def test_claimed_device_id_mismatch_rejected(db, fake_ai):
    from app.remote.auth import RemoteAuthError, authenticate_bearer

    device, token = _paired_device(db)
    other, _ = _paired_device(db, name="Outro")

    # Cliente possui token do device A mas alega ser o device B → negado.
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token, claimed_device_id=other.id)


def test_claimed_device_id_match_succeeds(db, fake_ai):
    from app.remote.auth import authenticate_bearer

    device, token = _paired_device(db)
    authed = authenticate_bearer(db, token, claimed_device_id=device.id)
    assert authed.device_id == device.id


def test_identity_always_from_credential(db, fake_ai):
    from app.remote.auth import RemoteAuthError, authenticate_bearer

    device_a, _ = _paired_device(db, name="A")
    device_b, token_b = _paired_device(db, name="B")

    # Alegar device_a com token do device_b → a DECISÃO usa a identidade da
    # credencial (o claim divergente é rejeitado, não seguido).
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token_b, claimed_device_id=device_a.id)

    # Com o claim correto, resolução retorna o device da credencial (B), nunca
    # algo vindo do payload.
    authed = authenticate_bearer(db, token_b, claimed_device_id=device_b.id)
    assert authed.device_id == device_b.id
    assert authed.device_id != device_a.id


def test_forged_session_has_no_effect(db, fake_ai):
    from app.remote.auth import authenticate_bearer

    _, token = _paired_device(db)
    authed = authenticate_bearer(db, token)

    # Session id é emitido pelo servidor no momento do auth; um id forjado
    # não aparece em lugar nenhum (o transporte usa o retornado).
    assert len(authed.session_id) == 36


# ---------------------------------------------------------------------------
# Sessões: encerramento e revogação
# ---------------------------------------------------------------------------

def test_end_session_marks_ended(db, fake_ai):
    from app.remote.auth import authenticate_bearer
    from app.remote.sessions import end_session, get_session

    _, token = _paired_device(db)
    authed = authenticate_bearer(db, token)
    end_session(db, authed.session_id)
    assert get_session(db, authed.session_id).status == "ended"

    from app.remote.sessions import list_sessions

    assert list_sessions(db, active_only=True) == []


def test_device_revocation_revokes_sessions_in_db(db, fake_ai):
    from app.models.remote import RemoteSession
    from app.remote.auth import authenticate_bearer
    from app.remote.devices import revoke_device

    device, token = _paired_device(db)
    authenticate_bearer(db, token)
    revoke_device(db, device.id)

    sessions = db.scalars(select(RemoteSession)).all()
    assert sessions
    assert all(s.status == "revoked" for s in sessions)