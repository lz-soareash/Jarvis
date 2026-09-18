"""Testes da Fase 16 — Remote Layer / acesso de agente remoto.

Cobrem as fronteiras de segurança introduzidas na Fase 16:
- contrato de erro estruturado (nunca stack trace / detalhes internos);
- rate limiting de autenticação (anti brute-force) e de mensagens;
- TTL/expiração/limpeza de sessões e revogação individual;
- teto de payload físico (413 antes do AI Core);
- mensagem conversacional (reply + SSE) REUTILIZANDO o AI Core, com request_id;
- CORS restritivo (helper nunca aceita `*`).

Tudo é hermético: REMOTE_ENABLED=false por padrão, sem rede, sem credenciais.
"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "remote_enabled", True)


def _pair(client):
    created = client.post("/api/remote/pairings").json()
    validated = client.post(
        "/api/remote/pairings/validate",
        json={"code": created["code"], "device_name": "Desk"},
    ).json()
    return validated


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _authed(db, monkeypatch):
    """Autentica um device (fluxo de serviço, sem rede) → AuthenticatedDevice."""
    from app.remote.pairing import create_pairing, submit_code

    _enable(monkeypatch)
    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name="Desk")

    from app.remote.auth import authenticate_bearer

    return authenticate_bearer(db, token)


# ---------------------------------------------------------------------------
# Desabilitada (padrão): os endpoints da Fase 16 também são 503
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/remote/message", {"token": "abc", "content": "oi"}),
        ("post", "/api/remote/sessions/xyz/revoke", None),
    ],
)
def test_fase16_endpoints_503_when_disabled(client, method, path, body):
    if body is not None:
        res = getattr(client, method)(path, json=body)
    else:
        res = getattr(client, method)(path)
    assert res.status_code == 503


# ---------------------------------------------------------------------------
# Contrato de erro estruturado
# ---------------------------------------------------------------------------

def test_unauthorized_is_structured_error(client, monkeypatch):
    _enable(monkeypatch)
    res = client.post("/api/remote/auth", json={"token": "token-invalido"})
    assert res.status_code == 401
    body = res.json()
    assert body.get("type") == "error"
    assert body.get("code") == "UNAUTHORIZED"
    assert "message" in body
    # Nunca vaza stack trace, paths internos ou detalhes de segurança.
    raw = res.text
    assert "Traceback" not in raw
    assert "detail" not in body
    assert "settings" not in raw.lower()
    assert "C:" not in raw and "D:\\\\" not in raw


def test_remote_error_response_echoes_request_id(client, monkeypatch):
    _enable(monkeypatch)
    req_id = "11111111-2222-3333-4444-555555555555"
    res = client.post(
        "/api/remote/auth",
        json={"token": "token-invalido"},
        headers={"X-Request-ID": req_id},
    )
    assert res.headers.get("X-Request-ID") == req_id
    assert res.json().get("request_id") == req_id


# ---------------------------------------------------------------------------
# Rate limiting de autenticação (anti brute-force)
# ---------------------------------------------------------------------------

def test_auth_rate_limit_blocks_brute_force(client, monkeypatch):
    _enable(monkeypatch)
    capacity = int(settings.remote_auth_rate_capacity)
    for _ in range(capacity):
        res = client.post("/api/remote/auth", json={"token": "tentativa"})
        assert res.status_code == 401
    res = client.post("/api/remote/auth", json={"token": "tentativa"})
    assert res.status_code == 429
    body = res.json()
    assert body.get("type") == "error"
    assert body.get("code") == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Payload guard (413 antes do AI Core)
# ---------------------------------------------------------------------------

def test_payload_too_large_rejected_before_processing(client, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "remote_max_payload_bytes", 16)
    res = client.post(
        "/api/remote/auth", json={"token": "x" * 64}, headers={"X-Request-ID": "11111111-2222-3333-4444-555555555555"}
    )
    assert res.status_code == 413
    body = res.json()
    assert body.get("code") == "PAYLOAD_TOO_LARGE"
    assert body.get("request_id") == "11111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------
# Mensagem conversacional: reply (não-stream) reutilizando o AI Core
# ---------------------------------------------------------------------------

def test_remote_message_reply_via_ai_core(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    paired = _pair(client)
    req_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    res = client.post(
        "/api/remote/message",
        json={
            "token": paired["token"],
            "content": "oi",
            "stream": False,
            "tools": False,
        },
        headers={"X-Request-ID": req_id},
    )
    assert res.status_code == 200, res.text
    assert res.headers.get("X-Request-ID") == req_id
    body = res.json()
    assert body["type"] == "response"
    assert body["request_id"] == req_id
    assert body["status"] == "completed"
    assert "content" in body
    # O provider usado foi o FakeProvider (override da dependency) — nunca rede.
    assert fake_ai.generate_calls >= 1


def test_remote_message_stream_reuses_sse(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    paired = _pair(client)
    req_id = "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"
    with client.stream(
        "POST",
        "/api/remote/message",
        json={
            "token": paired["token"],
            "content": "oi",
            "stream": True,
            "tools": False,
        },
        headers={"X-Request-ID": req_id},
    ) as res:
        assert res.status_code == 200
        assert res.headers.get("X-Request-ID") == req_id
        assert res.headers.get("content-type", "").startswith("text/event-stream")
        body = "".join(res.iter_text())
    assert "request_id" in body
    assert req_id in body


def _sse_payloads(body: str) -> list[dict]:
    """Extrai os payloads JSON dos eventos `data: {...}` de uma resposta SSE."""
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        raw = line[len("data:") :].strip()
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def test_remote_message_stream_done_session_id_is_conversation_anchor(
    client, monkeypatch, fake_ai
):
    """Regressão (Fase 16/21): o `session_id` injetado no SSE é a sessão JARVIS
    (âncora de conversa), nunca a sessão REMOTA de transporte.

    Antes da correção o stream injetava `ctx.session_id` (sessão remota); o
    cliente fino o guardava como âncora e o reenviava, e `resolve_conversation`
    o recusava ("sessão de conversa não disponível para continuação"),
    quebrando a segunda turn consecutiva via HTTP/SSE.
    """
    _enable(monkeypatch)
    paired = _pair(client)

    with client.stream(
        "POST",
        "/api/remote/message",
        json={"token": paired["token"], "content": "oi", "stream": True, "tools": False},
    ) as res:
        assert res.status_code == 200
        body = "".join(res.iter_text())

    payloads = _sse_payloads(body)
    done = next((p for p in payloads if p.get("type") == "done"), None)
    assert done is not None, body
    conversation_id = done["session_id"]

    from app.db.session import SessionLocal
    from app.models import Session as JarvisSession
    from app.models.remote import Device as RemoteDevice

    with SessionLocal() as check:
        # A âncora emitida tem de ser uma sessão JARVIS real (e não a remota).
        assert check.get(JarvisSession, conversation_id) is not None
        owner = check.scalar(
            select(RemoteDevice).where(
                RemoteDevice.jarvis_session_id == conversation_id
            )
        )
        assert owner is not None and owner.is_trusted

    # O follow-up usando a âncora do SSE é aceito (não cai no erro de sessão).
    follow = client.post(
        "/api/remote/message",
        json={
            "token": paired["token"],
            "content": "de novo",
            "stream": False,
            "tools": False,
            "session_id": conversation_id,
        },
    )
    assert follow.status_code == 200, follow.text
    assert follow.json()["status"] == "completed"
    assert follow.json()["session_id"] == conversation_id


def test_remote_message_invalid_content(client, monkeypatch, fake_ai):
    _enable(monkeypatch)
    paired = _pair(client)
    res = client.post(
        "/api/remote/message",
        json={"token": paired["token"], "content": "   ", "stream": False, "tools": False},
    )
    assert res.status_code == 400
    assert res.json().get("code") == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# Sessões: TTL / expiração / limpeza / revogação
# ---------------------------------------------------------------------------

def test_session_expires_after_ttl(db, monkeypatch):
    monkeypatch.setattr(settings, "remote_session_ttl_seconds", 60)
    from app.remote.sessions import is_expired
    from app.models.session import utcnow

    authed = _authed(db, monkeypatch)
    assert is_expired(authed.session) is False

    authed.session.last_seen_at = utcnow() - timedelta(minutes=10)
    db.commit()
    db.refresh(authed.session)
    assert is_expired(authed.session) is True


def test_ensure_session_valid_expired_raises_and_ends(db, monkeypatch):
    monkeypatch.setattr(settings, "remote_session_ttl_seconds", 60)
    from app.models.session import utcnow
    from app.remote.errors import RemoteError, RemoteErrorCode
    from app.remote.sessions import ensure_session_valid

    authed = _authed(db, monkeypatch)
    authed.session.last_seen_at = utcnow() - timedelta(minutes=10)
    db.commit()

    with pytest.raises(RemoteError) as exc_info:
        ensure_session_valid(db, authed.session)
    assert exc_info.value.code == RemoteErrorCode.SESSION_EXPIRED

    db.refresh(authed.session)
    assert authed.session.status == "ended"


def test_expire_stale_sessions_cleans_batch(db, monkeypatch):
    monkeypatch.setattr(settings, "remote_session_ttl_seconds", 60)
    from app.models.session import utcnow
    from app.remote.sessions import expire_stale_sessions

    authed_a = _authed(db, monkeypatch)
    authed_a.session.last_seen_at = utcnow() - timedelta(minutes=10)
    db.commit()

    authed_b = _authed(db, monkeypatch)
    assert expire_stale_sessions(db) >= 1

    db.refresh(authed_a.session)
    db.refresh(authed_b.session)
    assert authed_a.session.status == "ended"  # vencida → encerrada
    assert authed_b.session.status in ("active", "authenticated")  # válida → intacta


def test_revoke_session_endpoint_and_code(db, client, monkeypatch):
    _enable(monkeypatch)
    authed = _authed(db, monkeypatch)

    res = client.post(f"/api/remote/sessions/{authed.session_id}/revoke")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    from app.remote.errors import RemoteError, RemoteErrorCode
    from app.remote.sessions import ensure_session_valid, get_session

    db.expire_all()  # o commit da revogação veio de outra sessão (HTTP)
    session = get_session(db, authed.session_id)
    assert session.status == "revoked"
    with pytest.raises(RemoteError) as exc_info:
        ensure_session_valid(db, session)
    assert exc_info.value.code == RemoteErrorCode.SESSION_REVOKED


def test_revoke_session_endpoint_404(client, monkeypatch):
    _enable(monkeypatch)
    res = client.post("/api/remote/sessions/nao-existe/revoke")
    assert res.status_code == 404


def test_revocation_is_granular_per_session(db, client, monkeypatch):
    """Revogar uma sessão NÃO afeta as demais do mesmo device."""
    _enable(monkeypatch)
    authed_a = _authed(db, monkeypatch)
    authed_b = _authed(db, monkeypatch)
    assert authed_a.session_id != authed_b.session_id

    client.post(f"/api/remote/sessions/{authed_a.session_id}/revoke")

    from app.remote.sessions import ensure_session_valid

    db.refresh(authed_a.session)
    db.refresh(authed_b.session)
    with pytest.raises(Exception) as exc_info:
        ensure_session_valid(db, authed_a.session)
    from app.remote.errors import RemoteErrorCode

    assert exc_info.value.code == RemoteErrorCode.SESSION_REVOKED
    assert ensure_session_valid(db, authed_b.session).id == authed_b.session_id


# ---------------------------------------------------------------------------
# CORS restritivo (helper) — nunca `*`
# ---------------------------------------------------------------------------

def test_cors_origin_list_never_wildcard(monkeypatch):
    from app.remote.config import remote_cors_origin_list

    monkeypatch.setattr(
        settings,
        "remote_cors_origins",
        "https://app.example.com,   , *, https://panel.example.com",
    )
    assert remote_cors_origin_list() == [
        "https://app.example.com",
        "https://panel.example.com",
    ]

    monkeypatch.setattr(settings, "remote_cors_origins", "")
    assert remote_cors_origin_list() == []


# ---------------------------------------------------------------------------
# Sessões múltiplas isoladas (estado não vaza entre sessões)
# ---------------------------------------------------------------------------

def test_multiple_sessions_isolated_from_each_other(db, monkeypatch):
    monkeypatch.setattr(settings, "remote_session_ttl_seconds", 60)
    from app.models.session import utcnow
    from app.remote.sessions import is_expired

    authed_a = _authed(db, monkeypatch)
    authed_b = _authed(db, monkeypatch)
    authed_a.session.last_seen_at = utcnow() - timedelta(minutes=10)
    db.commit()

    assert is_expired(authed_a.session) is True
    assert is_expired(authed_b.session) is False


def test_session_event_logging_covers_expiry(db, monkeypatch):
    monkeypatch.setattr(settings, "remote_session_ttl_seconds", 60)
    from app.models.session import utcnow
    from app.remote.sessions import expire_stale_sessions

    authed = _authed(db, monkeypatch)
    authed.session.last_seen_at = utcnow() - timedelta(minutes=10)
    db.commit()
    expire_stale_sessions(db)
    assert authed.session.status == "ended"