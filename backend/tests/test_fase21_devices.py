"""Testes da Fase 21 — Device Bridge multiplataforma (clientes finos).

Cobrem o dispositivo de identidade de clientes finos (Desktop/Mobile) sobre o
Remote Gateway existente — SEM duplicar caminhos `/api/mobile|desktop/*`:

- DEVICE: registro PENDING (id do servidor), rename, capabilities, tetos;
- PAIRING: ancoragem do registro pendente, teto de devices confiáveis,
  segurança (single-use/hash/lock) preservada;
- SESSION TRUST: PAIRED/ACTIVE autenticam; REVOKED/EXPIRED/PENDING não;
- CONNECTION: heartbeat connect/reconnect/disconnect + eventos observáveis;
- SESSION SHARING: continuação entre dispositivos na MESMA conversa do Core;
- SECURITY: cliente nunca escolhe permissão/autonomia; sem secrets nos eventos;
- SYNC: event_id/dedup do barramento; OPS: bloco `devices` da Central.

Tudo hermético: REMOTE_ENABLED=false por padrão, banco :memory:, sem rede.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.remote import Credential


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "remote_enabled", True)


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _pair_via_api(client):
    created = client.post("/api/remote/pairings").json()
    validated = client.post(
        "/api/remote/pairings/validate",
        json={"code": created["code"], "device_name": "Desk"},
    ).json()
    return validated


def _authed(db, monkeypatch, device_name="Desk"):
    """Autentica um device (fluxo de serviço) → AuthenticatedDevice."""
    from app.remote.pairing import create_pairing, submit_code

    _enable(monkeypatch)
    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name=device_name)
    from app.remote.auth import authenticate_bearer

    return authenticate_bearer(db, token)


def _register(db, name, **kwargs):
    from app.remote.devices import register_device

    return register_device(db, name=name, **kwargs)


# ---------------------------------------------------------------------------
# DEVICE — registro PENDING (identidade visível, sem segredo)
# ---------------------------------------------------------------------------

def test_register_pending_device_has_server_issued_id(db):
    device = _register(db, "Meu Desktop", device_type="desktop")
    assert device.status == "pending"
    assert device.id and isinstance(device.id, str) and len(device.id) >= 32
    creds = db.scalars(select(Credential).where(Credential.device_id == device.id)).all()
    assert len(creds) == 0


def test_register_normalizes_platform(db):
    for entry, expected in [
        ("windows", "desktop"),
        ("android", "mobile"),
        ("   IOS  ", "mobile"),
        ("", "web"),
    ]:
        device = _register(db, f"D-{entry}", platform=entry)
        assert device.platform == expected


def test_register_normalizes_capabilities_and_rejects_junk(db):
    from app.remote.devices import DeviceInvalid

    dev = _register(db, "Cap", capabilities=["Chat", " control ", "notifications"])
    assert dev.capabilities == ["chat", "control", "notifications"]

    with pytest.raises(DeviceInvalid):
        _register(db, "CapsBad", capabilities=[3, None], platform="desktop")
    with pytest.raises(DeviceInvalid):
        _register(db, "CapsBad2", capabilities=[{"mal": "icioso"}], platform="desktop")


def test_register_rejects_empty_name(db):
    from app.remote.devices import DeviceInvalid

    with pytest.raises(DeviceInvalid):
        _register(db, "   ")


def test_register_respects_max_pending(db, monkeypatch):
    from app.remote.devices import DeviceLimitExceeded

    monkeypatch.setattr(settings, "device_max_pending", 1)
    _register(db, "P1")
    with pytest.raises(DeviceLimitExceeded):
        _register(db, "P2")


def test_register_emits_device_registered_event(db):
    from app.remote.devices import register_device_bridge_events
    from app.remote.events import subscribe_events, unsubscribe_events

    device = _register(db, "Visível")
    queue, _ = subscribe_events()
    try:
        register_device_bridge_events(device)
        seen = queue.get(timeout=1)
        while True:
            if seen["type"] == "remote.device.registered":
                break
            seen = queue.get(timeout=1)
        assert seen["data"]["device_id"] == device.id
    finally:
        unsubscribe_events(queue)


def test_pending_device_never_authenticates(db):
    from app.remote.auth import RemoteAuthError, authenticate_bearer
    from app.remote.credentials import issue_credential

    pending = _register(db, "Pendente")
    _, token = issue_credential(db, pending)
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token)


def test_rename_device_updates_name_and_audits(db):
    from app.remote.devices import DeviceInvalid, rename_device

    device = _register(db, "Antes")
    renamed = rename_device(db, device.id, "Depois")
    assert renamed.name == "Depois"
    with pytest.raises(DeviceInvalid):
        rename_device(db, device.id, "  ")


# ---------------------------------------------------------------------------
# PAIRING — ancoragem do registro pendente + teto de confiáveis
# ---------------------------------------------------------------------------

def test_pairing_without_pending_creates_new_active_device(db):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name="Novo")
    assert device.status == "active"
    assert device.is_trusted
    assert token


def test_pairing_attaches_pending_device(db):
    from app.remote.pairing import create_pairing, submit_code

    pending = _register(db, "Meu Android", platform="android")
    _, code = create_pairing(db)
    device, token = submit_code(
        db, code=code, device_name="Meu Android", pending_device_id=pending.id
    )
    assert device.id == pending.id  # MESMO registro promovido (id do servidor)
    assert device.status == "active"
    assert device.platform == "mobile"  # plataforma do REGISTRO preservada
    assert token


def test_pairing_attach_accepts_explicit_caps_override(db):
    from app.remote.pairing import create_pairing, submit_code

    pending = _register(db, "Meu Android", platform="android")
    _, code = create_pairing(db)
    device, _ = submit_code(
        db,
        code=code,
        device_name="Meu Android",
        pending_device_id=pending.id,
        platform="windows",
        client_version="3.0",
        capabilities=["tts"],
    )
    assert device.platform == "desktop"
    assert device.client_version == "3.0"
    assert device.capabilities == ["tts"]


def test_pairing_attach_rejects_device_already_trusted(db):
    from app.remote.pairing import PairingInvalid, create_pairing, submit_code

    active = _register(db, "A")
    active.status = "active"
    db.commit()
    _, code = create_pairing(db)
    with pytest.raises(PairingInvalid):
        submit_code(db, code=code, device_name="A", pending_device_id=active.id)


def test_pairing_attach_rejects_unknown_device(db):
    from app.remote.pairing import PairingInvalid, create_pairing, submit_code

    _, code = create_pairing(db)
    with pytest.raises(PairingInvalid):
        submit_code(db, code=code, device_name="X", pending_device_id="nao-existe")


def test_pairing_respects_device_max(db, monkeypatch):
    from app.remote.pairing import PairingError, create_pairing, submit_code

    monkeypatch.setattr(settings, "device_max_devices", 1)
    _, code = create_pairing(db)
    submit_code(db, code=code, device_name="Pc")
    _, code2 = create_pairing(db)
    with pytest.raises(PairingError):
        submit_code(db, code=code2, device_name="Phone")


def test_pairing_stores_platform_version_capabilities(db):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, _ = submit_code(
        db,
        code=code,
        device_name="A",
        platform="android",
        client_version="1.2.3",
        capabilities=["chat", "notifications"],
    )
    assert device.platform == "mobile"
    assert device.client_version == "1.2.3"
    assert device.capabilities == ["chat", "notifications"]


def test_pairing_single_use_again(db):
    from app.remote.pairing import PairingInvalid, create_pairing, submit_code

    _, code = create_pairing(db)
    submit_code(db, code=code, device_name="A")
    with pytest.raises(PairingInvalid):
        submit_code(db, code=code, device_name="B")


def test_pairing_promotion_audits_device_trusted(db):
    from app.remote.events import subscribe_events, unsubscribe_events
    from app.remote.pairing import create_pairing, submit_code

    pending = _register(db, "P")
    _, code = create_pairing(db)
    dev, _ = submit_code(db, code=code, device_name="P", pending_device_id=pending.id)
    assert dev.status == "active"

    queue, history = subscribe_events()
    try:
        assert "remote.identity.device_trusted" in {e["type"] for e in history}
    finally:
        unsubscribe_events(queue)


# ---------------------------------------------------------------------------
# SESSION TRUST — PAIRED/ACTIVE autenticam; PENDING/REVOKED/EXPIRED não
# ---------------------------------------------------------------------------

def test_paired_device_authenticates(db):
    from app.remote.auth import authenticate_bearer
    from app.remote.credentials import issue_credential

    paired = _register(db, "Par")
    paired.status = "paired"
    db.commit()
    _, token = issue_credential(db, paired)
    authed = authenticate_bearer(db, token)
    assert authed.device.status == "paired"
    assert authed.session.status == "active"


def test_active_device_authenticates(db, monkeypatch):
    authed = _authed(db, monkeypatch)
    assert authed.device.status == "active"
    assert authed.session.status == "active"


def test_expired_status_fails_auth(db):
    from app.remote.auth import RemoteAuthError, authenticate_bearer
    from app.remote.credentials import issue_credential

    expired = _register(db, "Velho")
    expired.status = "expired"
    db.commit()
    _, token = issue_credential(db, expired)
    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, token)


def test_auth_returns_conversation_id(db, monkeypatch):
    authed = _authed(db, monkeypatch)
    from app.remote.jarvis_session import get_or_create_jarvis_session

    assert get_or_create_jarvis_session(db, authed.device)


# ---------------------------------------------------------------------------
# CONNECTION — heartbeat connect/reconnect/disconnect + eventos
# ---------------------------------------------------------------------------

def _drain(queue, n, timeout=1.0):
    out = []
    for _ in range(n):
        try:
            out.append(queue.get(timeout=timeout))
        except Exception:  # noqa: BLE001 — fila vazia em modo de teste
            break
    return out


def _flatten_keys(entries):
    keys = set()
    for e in entries:
        keys.update(e.keys())
        keys.update((e.get("data") or {}).keys())
    return keys


def test_heartbeat_connect_emits_device_connected(db, monkeypatch):
    from app.remote.devices import heartbeat
    from app.remote.events import subscribe_events, unsubscribe_events

    authed = _authed(db, monkeypatch)
    queue, _ = subscribe_events()
    try:
        heartbeat(db, authed.device, event="connect")
        entries = _drain(queue, 4)
        types = {e["type"] for e in entries}
        assert "device.connected" in types
        assert not {"token", "code", "Authorization"} & _flatten_keys(entries)
    finally:
        unsubscribe_events(queue)


def test_heartbeat_reconnect_emits_device_reconnected(db, monkeypatch):
    from app.remote.devices import heartbeat
    from app.remote.events import subscribe_events, unsubscribe_events

    authed = _authed(db, monkeypatch)
    queue, _ = subscribe_events()
    try:
        heartbeat(db, authed.device, event="reconnect")
        types = {e["type"] for e in _drain(queue, 4)}
        assert "device.reconnected" in types
    finally:
        unsubscribe_events(queue)


def test_heartbeat_disconnect_emits_device_disconnected(db, monkeypatch):
    from app.remote.devices import heartbeat
    from app.remote.events import subscribe_events, unsubscribe_events

    authed = _authed(db, monkeypatch)
    queue, _ = subscribe_events()
    try:
        heartbeat(db, authed.device, event="disconnect")
        types = {e["type"] for e in _drain(queue, 4)}
        assert "device.disconnected" in types
    finally:
        unsubscribe_events(queue)


def test_heartbeat_touches_last_seen(db, monkeypatch):
    from app.models.session import ensure_utc
    from app.remote.devices import heartbeat

    authed = _authed(db, monkeypatch)
    device = authed.device
    device.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()
    heartbeat(db, device, event="heartbeat")
    age = datetime.now(timezone.utc) - ensure_utc(device.last_seen_at)
    assert age.total_seconds() < 60


def test_heartbeat_reply_config_driven(db, monkeypatch):
    from app.remote.devices import heartbeat, device_reconnect_enabled

    authed = _authed(db, monkeypatch)
    assert device_reconnect_enabled() is True
    monkeypatch.setattr(settings, "device_reconnect_enabled", False)
    assert device_reconnect_enabled() is False


def test_heartbeat_requires_valid_auth(db):
    from app.remote.auth import RemoteAuthError, authenticate_bearer

    with pytest.raises(RemoteAuthError):
        authenticate_bearer(db, "token-invalido")


# ---------------------------------------------------------------------------
# SESSION SHARING — continuação entre dispositivos (MESMO Core / conversa)
# ---------------------------------------------------------------------------

def _pair_api_devices(client, names=("Pc", "Phone")):
    devices = []
    for name in names:
        created = client.post("/api/remote/pairings").json()
        validated = client.post(
            "/api/remote/pairings/validate",
            json={
                "code": created["code"],
                "device_name": name,
                "platform": "desktop" if name == "Pc" else "android",
                "capabilities": ["chat"],
            },
        ).json()
        devices.append(validated)
    return devices


def test_share_message_routes_to_sibling_device_conversation(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    pc, phone = _pair_api_devices(client)
    assert pc["device_id"] != phone["device_id"]

    msg_pc = client.post(
        "/api/remote/message",
        json={"token": pc["token"], "content": "começa no pc", "tools": False},
    ).json()
    assert msg_pc["status"] == "completed"
    pc_conversation = msg_pc["session_id"]

    from app.db.session import SessionLocal
    from app.models import Session as JarvisSession

    with SessionLocal() as check:
        assert check.get(JarvisSession, pc_conversation) is not None

    msg_phone = client.post(
        "/api/remote/message",
        json={
            "token": phone["token"],
            "content": "continuo no celular",
            "tools": False,
            "session_id": pc_conversation,
        },
    ).json()
    assert msg_phone["status"] == "completed"
    assert msg_phone["session_id"] == pc_conversation


def test_share_unknown_session_rejected(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    pc, _ = _pair_api_devices(client)
    res = client.post(
        "/api/remote/message",
        json={
            "token": pc["token"],
            "content": "oi",
            "tools": False,
            "session_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert res.status_code == 400
    assert res.json().get("code") == "INVALID_REQUEST"


def test_share_untrusted_owner_rejected(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    pc, phone = _pair_api_devices(client)
    msg = client.post(
        "/api/remote/message",
        json={"token": pc["token"], "content": "oi", "tools": False},
    ).json()
    client.post(f"/api/remote/devices/{pc['device_id']}/revoke")
    res = client.post(
        "/api/remote/message",
        json={
            "token": phone["token"],
            "content": "tentativa",
            "tools": False,
            "session_id": msg["session_id"],
        },
    )
    assert res.status_code == 400


def test_share_same_device_conversation_allowed(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    pc, _ = _pair_api_devices(client)
    msg = client.post(
        "/api/remote/message",
        json={"token": pc["token"], "content": "oi", "tools": False},
    ).json()
    again = client.post(
        "/api/remote/message",
        json={
            "token": pc["token"],
            "content": "oi de novo",
            "tools": False,
            "session_id": msg["session_id"],
        },
    ).json()
    assert again["status"] == "completed"
    assert again["session_id"] == msg["session_id"]


def test_share_emits_session_shared_event(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    from app.remote.events import subscribe_events, unsubscribe_events

    pc, phone = _pair_api_devices(client)
    msg = client.post(
        "/api/remote/message",
        json={"token": pc["token"], "content": "oi", "tools": False},
    ).json()
    queue, _ = subscribe_events()
    try:
        client.post(
            "/api/remote/message",
            json={
                "token": phone["token"],
                "content": "continua",
                "tools": False,
                "session_id": msg["session_id"],
            },
        )
        seen = None
        for _ in range(6):
            item = queue.get(timeout=0.5)
            if item["type"] == "remote.identity.session_shared":
                seen = item
                break
        assert seen is not None
    finally:
        unsubscribe_events(queue)


def test_message_without_session_id_uses_own_conversation(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    pc, phone = _pair_api_devices(client)
    a = client.post(
        "/api/remote/message", json={"token": pc["token"], "content": "a", "tools": False}
    ).json()
    b = client.post(
        "/api/remote/message", json={"token": phone["token"], "content": "b", "tools": False}
    ).json()
    assert a["session_id"] != b["session_id"]


# ---------------------------------------------------------------------------
# SECURITY — cliente fino é fino (sem permissões/autonomia/escalada)
# ---------------------------------------------------------------------------

def test_message_schema_ignores_escalation_fields():
    from app.schemas.remote import RemoteMessageIn

    body = RemoteMessageIn(
        token="t",
        content="oi",
        permission_level=3,
        autonomy="C4",
        tool_policy={"bypass": True},
        escalate=True,
    )
    assert not hasattr(body, "permission_level")
    assert not hasattr(body, "autonomy")
    assert not hasattr(body, "tool_policy")
    assert not hasattr(body, "escalate")
    assert body.tools is True  # tools autorizadas = Permission Engine, nada mais


def test_heartbeat_schema_has_no_security_fields():
    from app.schemas.remote import HeartbeatIn

    body = HeartbeatIn(token="t", permission_level=3, autonomy="C4", shell=True)
    assert not hasattr(body, "permission_level")
    assert not hasattr(body, "autonomy")
    assert not hasattr(body, "shell")


def test_revoked_device_cannot_message(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    pair = _pair_api_devices(client, names=("Pc",))
    client.post(f"/api/remote/devices/{pair[0]['device_id']}/revoke")
    res = client.post(
        "/api/remote/message",
        json={"token": pair[0]["token"], "content": "oi", "tools": False},
    )
    assert res.status_code == 401


def test_no_secrets_in_broker_events(db, monkeypatch):
    from app.remote.devices import register_device_bridge_events
    from app.remote.events import subscribe_events, unsubscribe_events
    from app.remote.pairing import create_pairing, submit_code

    pending = _register(db, "Seguro")
    register_device_bridge_events(pending)
    _, code = create_pairing(db)
    device, token = submit_code(
        db,
        code=code,
        device_name="Seguro",
        pending_device_id=pending.id,
        client_version="1.0",
    )
    assert token

    queue, _ = subscribe_events()
    try:
        seen = _drain(queue, 8, timeout=0.3)
    finally:
        unsubscribe_events(queue)
    raw = str(seen)
    assert token not in raw
    assert "token" not in raw.lower()
    assert code not in raw


def test_device_info_never_exposes_secrets(client, monkeypatch):
    _enable(monkeypatch)
    paired = _pair_via_api(client)
    info = client.get(f"/api/remote/devices/{paired['device_id']}/info").json()
    raw = str(info)
    assert "token" not in raw
    assert "sha256" not in raw


# ---------------------------------------------------------------------------
# SYNC — event_id/dedup do barramento (ClientEvent para os clientes finos)
# ---------------------------------------------------------------------------

def test_broker_entries_have_event_id(db):
    from app.remote.events import subscribe_events, unsubscribe_events

    queue, _ = subscribe_events()
    try:
        entries = _drain(queue, 3, timeout=0.3)
    finally:
        unsubscribe_events(queue)
    if not entries:
        pytest.skip("sem eventos publicados neste teste")
    entry = entries[0]
    assert isinstance(entry["event_id"], str) and entry["event_id"]
    assert "at" in entry
    assert isinstance(entry["data"], dict)


def test_dedupe_events_keeps_order_and_removes_duplicates():
    from app.remote.events import dedupe_events

    entries = [
        {"event_id": "2", "type": "a"},
        {"event_id": "1", "type": "b"},
        {"event_id": "1", "type": "b-dupe"},
        {"event_id": "3", "type": "c"},
    ]
    unique, seen = dedupe_events(entries)
    assert [e["event_id"] for e in unique] == ["2", "1", "3"]
    assert seen == {"2", "1", "3"}


def test_dedupe_respects_max_seen():
    from app.remote.events import dedupe_events

    entries = [{"event_id": str(i), "type": "t"} for i in range(10)]
    unique, seen = dedupe_events(entries, max_seen=3)
    assert len(unique) == 3
    assert len(seen) == 3


def test_consume_broker_replay_and_live_ordered():
    from app.remote.events import RemoteEventBroker, consume_broker

    broker = RemoteEventBroker()
    broker.publish("one", {"n": 1})
    broker.publish("two", {"n": 2})

    async def run():
        out = []
        async for entry in consume_broker(broker, limit=3, ready_event_type="ready"):
            out.append(entry["type"])
        return out

    types = asyncio.run(run())
    assert types[:2] == ["one", "two"]
    assert "ready" in types


# ---------------------------------------------------------------------------
# OPS — bloco `devices` da Central de Operações
# ---------------------------------------------------------------------------

def _pair_trusted(db, name):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, _ = submit_code(db, code=code, device_name=name)
    return device


def test_ops_overview_has_devices_block(db):
    from app.services.ops import build_overview

    overview = build_overview(db)
    assert isinstance(overview.devices, dict)
    assert "enabled" in overview.devices
    assert "total" in overview.devices


def test_ops_devices_counts_by_status_and_platform(db):
    from app.services.ops import build_overview

    _register(db, "Windows", platform="windows")
    _register(db, "Android", platform="android")
    trusted = _pair_trusted(db, "Linux")
    assert trusted.status == "active"

    overview = build_overview(db)
    assert overview.devices["trusted"] >= 1
    assert overview.devices["by_platform"]["desktop"] >= 1  # Windows normalizado
    assert overview.devices["by_platform"]["mobile"] >= 1  # Android normalizado
    assert overview.devices["by_platform"]["web"] >= 1  # Linux sem plataforma
    assert overview.devices["by_platform"]["mobile"] >= 1
    assert overview.devices["by_status"]["pending"] >= 2
    assert overview.devices["by_status"]["active"] >= 1
    assert overview.devices["heartbeat_seconds"] == settings.device_heartbeat_seconds


# ---------------------------------------------------------------------------
# API surface — registros/rename/caps/info/heartbeat/503
# ---------------------------------------------------------------------------

def test_fase21_endpoints_503_when_disabled(client):
    assert client.post("/api/remote/devices/register", json={"name": "X"}).status_code == 503
    assert client.post("/api/remote/heartbeat", json={"token": "t"}).status_code == 503
    assert client.get("/api/remote/devices/x/info").status_code == 503


def test_api_register_device(client, monkeypatch):
    _enable(monkeypatch)
    res = client.post(
        "/api/remote/devices/register",
        json={
            "name": "Meu Android",
            "platform": "android",
            "client_version": "0.9.1",
            "capabilities": ["chat", "notifications"],
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "pending"
    assert body["device"]["status"] == "pending"
    assert body["device"]["platform"] == "mobile"
    assert body["device"]["client_version"] == "0.9.1"
    assert body["device"]["capabilities"] == ["chat", "notifications"]


def test_api_rename_device(client, monkeypatch):
    _enable(monkeypatch)
    reg = client.post("/api/remote/devices/register", json={"name": "Antes"}).json()["device"]
    out = client.post(f"/api/remote/devices/{reg['id']}/rename", json={"name": "Depois"})
    assert out.status_code == 200
    assert out.json()["name"] == "Depois"


def test_api_capabilities_endpoint(client, monkeypatch):
    _enable(monkeypatch)
    reg = client.post("/api/remote/devices/register", json={"name": "C", "platform": "web"}).json()["device"]
    out = client.post(
        f"/api/remote/devices/{reg['id']}/capabilities",
        json={"platform": "windows", "client_version": "2.0", "capabilities": ["tts"]},
    )
    assert out.status_code == 200
    assert out.json()["platform"] == "desktop"
    assert out.json()["client_version"] == "2.0"
    assert out.json()["capabilities"] == ["tts"]


def test_api_info_endpoint(client, monkeypatch):
    _enable(monkeypatch)
    reg = client.post("/api/remote/devices/register", json={"name": "Info"}).json()["device"]
    info = client.get(f"/api/remote/devices/{reg['id']}/info").json()
    assert info["device"]["id"] == reg["id"]
    assert info["connected"] is False  # PENDING ainda não tem sessão


def test_api_heartbeat_flow(client, monkeypatch):
    _enable(monkeypatch)
    paired = _pair_via_api(client)
    res = client.post(
        "/api/remote/heartbeat",
        json={
            "token": paired["token"],
            "event": "connect",
            "client_version": "1.2.0",
            "capabilities": ["chat"],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["device_id"] == paired["device_id"]
    assert body["connected"] is True
    assert body["heartbeat_seconds"] == settings.device_heartbeat_seconds
    assert body["conversation_id"]
    info = client.get(f"/api/remote/devices/{paired['device_id']}/info").json()
    assert info["device"]["client_version"] == "1.2.0"
    assert info["device"]["status"] == "active"
    assert info["connected"] is True


def test_api_heartbeat_disconnect_sets_connected_false(client, monkeypatch):
    _enable(monkeypatch)
    paired = _pair_via_api(client)
    byebye = client.post(
        "/api/remote/heartbeat",
        json={"token": paired["token"], "event": "disconnect"},
    )
    assert byebye.json()["connected"] is False


def test_api_paired_device_heartbeat_requires_auth(client, monkeypatch):
    _enable(monkeypatch)
    res = client.post(
        "/api/remote/heartbeat", json={"token": "token-invalido", "event": "connect"}
    )
    assert res.status_code in (401, 429)


# ---------------------------------------------------------------------------
# DEVICE — lista com identity estendida (contrato DeviceInfo/DeviceRegistration)
# ---------------------------------------------------------------------------

def test_device_out_includes_bridge_fields(client, monkeypatch):
    _enable(monkeypatch)
    registered = client.post(
        "/api/remote/devices/register", json={"name": "B", "platform": "android"}
    ).json()["device"]
    devices = client.get("/api/remote/devices").json()
    match = next(d for d in devices if d["id"] == registered["id"])
    assert match["platform"] == "mobile"
    assert match["capabilities"] == []
    assert "conversation_id" in match
    assert "metadata" in match


def test_device_type_tablet_accepted(db):
    device = _register(db, "Tab", device_type="tablet", platform="tablet")
    assert device.platform == "tablet"
    assert device.device_type == "tablet"