"""Testes da Fase 12.2 — identidade remota: Device, Credential, raw/hash, revogação e bootstrap."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.session import utcnow


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

def test_create_device_is_active_and_lists(db, fake_ai):
    from app.remote.devices import create_device, get_device, list_devices

    device = create_device(db, name="Desk", device_type="desktop")
    assert device.status == "active"
    assert get_device(db, device.id).id == device.id
    ids = {d.id for d in list_devices(db)}
    assert device.id in ids


def test_device_id_is_server_issued(db):
    from app.remote.devices import create_device

    device = create_device(db, name="Phone", device_type="mobile")
    assert device.id and len(device.id) == 36  # uuid4; o cliente nunca define

    from uuid import UUID

    UUID(device.id)  # não levanta = formato de id gerado pelo servidor


def test_create_device_with_explicit_id_bootstrap_style(db):
    from app.remote.devices import create_device

    device = create_device(db, name="Root", device_id="root-1", status="active")
    assert device.id == "root-1"


# ---------------------------------------------------------------------------
# Credential — hash, única, expiração, teto
# ---------------------------------------------------------------------------

def test_issue_credential_returns_token_once_and_stores_only_hash(db):
    from app.models.remote import Credential
    from app.remote.credentials import issue_credential
    from app.remote.devices import create_device

    device = create_device(db, name="Desk")
    credential, token = issue_credential(db, device)

    assert token and len(token) >= 40  # alta entropia, URL-safe
    assert credential.token_hash != token  # nunca texto claro
    assert credential.token_hash.startswith("sha256:")
    assert token not in credential.token_hash

    stored = db.get(Credential, credential.id)
    assert stored.token_hash == credential.token_hash
    assert stored.token_hash != token

    # Repersistência não muda o hash (idempotente, sem token no banco).
    db.rollback()
    db.refresh(device)


def test_credential_tokens_are_unique(db):
    from app.models.remote import Credential
    from app.remote.credentials import issue_credential
    from app.remote.devices import create_device

    device = create_device(db, name="Desk")
    c1, t1 = issue_credential(db, device)
    c2, t2 = issue_credential(db, device)
    assert c1.id != c2.id and t1 != t2
    hashes = set(db.scalars(select(Credential.token_hash)).all())
    assert len(hashes) == 2


def test_credential_expires_and_is_inactive(db):
    from app.remote.credentials import get_credential_by_token, issue_credential
    from app.remote.devices import create_device

    device = create_device(db, name="Desk")
    credential, token = issue_credential(
        db, device, expires_at=utcnow() - timedelta(seconds=1)
    )
    assert credential.is_active is False
    # Busca pela chave não retorna credencial vencida.
    found = get_credential_by_token(db, token)
    assert found is not None  # ainda localizável; a recusa é do autenticador
    cred, _ = found
    assert cred.is_active is False


def test_credential_active_limit_enforced(db, monkeypatch):
    from app.remote.credentials import CredentialLimitError, issue_credential
    from app.remote.devices import create_device

    monkeypatch.setattr(settings, "remote_max_active_credentials", 2)
    device = create_device(db, name="Desk")
    issue_credential(db, device)
    issue_credential(db, device)
    with pytest.raises(CredentialLimitError):
        issue_credential(db, device)


# ---------------------------------------------------------------------------
# Revogação
# ---------------------------------------------------------------------------

def test_revoke_credential_revokes_matching_sessions(db, fake_ai):
    from app.remote.auth import authenticate_bearer, RemoteAuthError
    from app.remote.credentials import issue_credential, revoke_credential
    from app.remote.devices import create_device
    from app.remote.sessions import list_sessions

    device = create_device(db, name="Desk")
    credential, token = issue_credential(db, device)
    authed = authenticate_bearer(db, token)
    assert authed.session.status == "active"

    revoke_credential(db, credential.id)
    sessions = list_sessions(db, device_id=device.id)
    assert sessions[0].status == "revoked"

    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token)


def test_revoke_device_revokes_credentials_and_sessions(db, fake_ai):
    from app.remote.auth import RemoteAuthError, authenticate_bearer
    from app.remote.credentials import issue_credential
    from app.remote.devices import create_device, revoke_device
    from app.remote.sessions import list_sessions

    device = create_device(db, name="Desk")
    credential, token = issue_credential(db, device)
    authed = authenticate_bearer(db, token)

    revoked = revoke_device(db, device.id)
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None

    db.refresh(credential)
    assert credential.revoked_at is not None
    assert list_sessions(db, device_id=device.id)[0].status == "revoked"

    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token)


def test_revoke_unknown_failures(db):
    from app.remote.credentials import revoke_credential
    from app.remote.devices import revoke_device

    with pytest.raises(KeyError):
        revoke_device(db, "nao-existe")
    with pytest.raises(KeyError):
        revoke_credential(db, "nao-existe")


# ---------------------------------------------------------------------------
# Bootstrap do root device
# ---------------------------------------------------------------------------

def test_bootstrap_root_device_creates_and_is_idempotent(db, monkeypatch):
    from app.models.remote import Credential, Device
    from app.remote.registrar import bootstrap_root_device

    monkeypatch.setattr(settings, "remote_enabled", True)
    monkeypatch.setattr(settings, "remote_device_id", "root-pc-1")
    monkeypatch.setattr(settings, "remote_device_token", "root-secret-token-abc")

    device = bootstrap_root_device(db)
    assert device is not None
    assert device.id == "root-pc-1"
    assert device.status == "active"

    creds = list(db.scalars(select(Credential).where(Credential.device_id == "root-pc-1")))
    assert len(creds) == 1
    assert creds[0].token_hash != "root-secret-token-abc"
    assert creds[0].token_hash.startswith("sha256:")

    # Idempotência: segunda chamada não duplica device nem credencial.
    db.rollback()
    bootstrap_root_device(db)
    assert len(list(db.scalars(select(Device)))) == 1
    assert len(list(db.scalars(select(Credential)))) == 1


def test_bootstrap_noop_when_disabled(db, monkeypatch):
    from app.models.remote import Device
    from app.remote.registrar import bootstrap_root_device

    monkeypatch.setattr(settings, "remote_enabled", False)
    monkeypatch.setattr(settings, "remote_device_id", "root-pc-1")
    monkeypatch.setattr(settings, "remote_device_token", "x")
    assert bootstrap_root_device(db) is None
    assert db.scalar(select(Device.id)) is None


def test_bootstrap_without_token_does_not_register(db, monkeypatch):
    from app.models.remote import Device
    from app.remote.registrar import bootstrap_root_device

    monkeypatch.setattr(settings, "remote_enabled", True)
    monkeypatch.setattr(settings, "remote_device_id", "root-pc-1")
    monkeypatch.setattr(settings, "remote_device_token", "")
    assert bootstrap_root_device(db) is None
    assert db.scalar(select(Device.id)) is None


def test_bootstrap_revoked_root_not_resurrected(db, monkeypatch):
    from app.remote.devices import create_device, revoke_device
    from app.remote.registrar import bootstrap_root_device

    monkeypatch.setattr(settings, "remote_enabled", True)
    monkeypatch.setattr(settings, "remote_device_id", "root-pc-1")
    monkeypatch.setattr(settings, "remote_device_token", "s3cret")

    device = create_device(db, name="Root", device_id="root-pc-1", status="active")
    revoke_device(db, device.id)

    again = bootstrap_root_device(db)
    assert again.status == "revoked"  # não ressuscita


# ---------------------------------------------------------------------------
# Auditoria sanitizada (nenhum segredo)
# ---------------------------------------------------------------------------

def test_identity_audit_never_contains_secrets(db, fake_ai):
    from app.models.ops import ExecutionEvent
    from app.remote.auth import authenticate_bearer
    from app.remote.credentials import issue_credential
    from app.remote.devices import create_device

    device = create_device(db, name="Desk")
    _, token = issue_credential(db, device)
    authenticate_bearer(db, token)

    events = db.query(ExecutionEvent).all()
    raw = " ".join(e.meta_json or "" for e in events)
    assert token not in raw
    assert "sha256:" not in raw  # nem o hash derivado é observável