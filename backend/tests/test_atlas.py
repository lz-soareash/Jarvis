"""Testes da integração JARVIS ↔ Atlas (Fase 10).

Cobre o AtlasClient (login JWT, chat, refresh em 401, timeouts, respostas
inválidas), o roteamento com fallback automático e os endpoints de status.
Nenhum teste toca a rede real (httpx.MockTransport).
"""

import json

import httpx
import pytest

from app.core.config import settings
from app.schemas.atlas import AtlasChatResponse
from app.services.atlas_client import (
    AtlasAuthError,
    AtlasClient,
    AtlasUnavailable,
    set_atlas_client,
)
from app.services.atlas_router import route as atlas_route
from app.services.atlas_router import should_route_to_atlas


@pytest.fixture(autouse=True)
def _ensure_db(client):
    """Garante as tabelas (dispara o lifespan/init_db via client) p/ unidade.

    O autouse `_clean_db` global exige que as tabelas existam; testes de
    unidade puros (que não usam `client` no corpo) precisam que o lifecycle do
    app rode. Declarar `client` aqui força isso antes de cada teste."""
    return client


# ---------------------------------------------------------------------------
# Mock transport helper
# ---------------------------------------------------------------------------
def make_handler(handler):
    """Empacota um handler plain por path em um MockTransport."""
    return httpx.MockTransport(lambda request: handler(request))


def login_response():
    return httpx.Response(
        200,
        json={"access": "access-token", "refresh": "refresh-token"},
    )


def chat_response(answer="Resposta do Atlas", sources=None, proposals=None):
    return httpx.Response(
        200,
        json={
            "answer": answer,
            "sources": sources or [],
            "provider": "gemini",
            "classification": {"kind": "fato", "label": "Fato", "source_based": True},
            "semantic_available": True,
            "proposals": proposals or [],
        },
    )


def make_client(handler):
    return AtlasClient(
        base_url="http://atlas.test",
        email="a@b.com",
        password="secret",
        transport=make_handler(handler),
    )


# ---------------------------------------------------------------------------
# AtlasClient
# ---------------------------------------------------------------------------
def test_client_logs_in_and_chats():
    def handler(request):
        if request.url.path.endswith("/api/auth/token/"):
            return login_response()
        if request.url.path.endswith("/api/assistant/chat/"):
            assert request.headers.get("Authorization") == "Bearer access-token"
            return chat_response()
        return httpx.Response(404)

    client = make_client(handler)
    resp = client.chat([{"role": "user", "content": "oi"}])
    assert isinstance(resp, AtlasChatResponse)
    assert resp.answer == "Resposta do Atlas"
    assert resp.provider == "gemini"
    assert resp.semantic_available is True


def test_client_refreshes_on_401_then_retries():
    calls = {"chat": 0}

    def handler(request):
        if request.url.path.endswith("/api/auth/token/"):
            return login_response()
        if request.url.path.endswith("/api/auth/token/refresh/"):
            assert json.loads(request.content)["refresh"] == "refresh-token"
            return httpx.Response(200, json={"access": "new-access", "refresh": "new-refresh"})
        if request.url.path.endswith("/api/assistant/chat/"):
            calls["chat"] += 1
            if calls["chat"] == 1:
                return httpx.Response(401, json={"code": "token_not_valid"})
            assert request.headers.get("Authorization") == "Bearer new-access"
            return chat_response("apos refresh")
        return httpx.Response(404)

    client = make_client(handler)
    resp = client.chat([{"role": "user", "content": "oi"}])
    assert resp.answer == "apos refresh"
    assert calls["chat"] == 2


def test_client_raises_on_429():
    def handler(request):
        return httpx.Response(429, json={"detail": "throttled"})

    client = make_client(handler)
    with pytest.raises((AtlasUnavailable, AtlasAuthError)):
        client.chat([{"role": "user", "content": "oi"}])


def test_client_raises_auth_error_on_401_login():
    def handler(request):
        if request.url.path.endswith("/api/auth/token/"):
            return httpx.Response(401, json={"code": "invalid_credentials"})
        return httpx.Response(404)

    client = make_client(handler)
    with pytest.raises(AtlasAuthError):
        client.chat([{"role": "user", "content": "oi"}])


def test_client_raises_on_invalid_json_response():
    def handler(request):
        return httpx.Response(200, content="not-json", headers={"Content-Type": "text/plain"})

    client = make_client(handler)
    with pytest.raises(AtlasUnavailable):
        client.chat([{"role": "user", "content": "oi"}])


def test_client_not_configured_raises():
    client = AtlasClient(
        base_url="http://atlas.test", email="", password="", transport=make_handler(lambda r: login_response())
    )
    from app.services.atlas_client import AtlasConfigError

    with pytest.raises(AtlasConfigError):
        client.chat([{"role": "user", "content": "oi"}])


# ---------------------------------------------------------------------------
# Roteamento / fallback
# ---------------------------------------------------------------------------
@pytest.fixture
def atlas_on(monkeypatch):
    monkeypatch.setattr(settings, "atlas_enabled", True)
    monkeypatch.setattr(settings, "atlas_email", "a@b.com")
    monkeypatch.setattr(settings, "atlas_password", "secret")
    return monkeypatch


def test_should_route_off_by_default():
    assert should_route_to_atlas() is False


@pytest.mark.parametrize("enabled,email,pw,expected", [
    (False, "a@b.com", "pw", False),
    (True, "", "pw", False),
    (True, "a@b.com", "", False),
    (True, "a@b.com", "pw", True),
])
def test_should_route_matrix(monkeypatch, enabled, email, pw, expected):
    monkeypatch.setattr(settings, "atlas_enabled", enabled)
    monkeypatch.setattr(settings, "atlas_email", email)
    monkeypatch.setattr(settings, "atlas_password", pw)
    assert should_route_to_atlas() is expected


def test_route_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "atlas_enabled", False)
    assert atlas_route(None, "sess", "oi") is None


def test_route_falls_back_when_unavailable(monkeypatch, caplog):
    import app.services.atlas_router as router_mod

    monkeypatch.setattr(settings, "atlas_enabled", True)
    monkeypatch.setattr(settings, "atlas_email", "a@b.com")
    monkeypatch.setattr(settings, "atlas_password", "secret")
    monkeypatch.setattr(router_mod, "build_atlas_messages", lambda db, sid, q: [])

    def handler(request):
        raise httpx.ConnectError("connection refused")

    set_atlas_client(make_client(handler))
    try:
        result = atlas_route(None, "sess", "oi")
    finally:
        from app.services.atlas_client import reset_atlas_client

        reset_atlas_client()
    assert result is None
    assert any("Atlas" in r.message and "fallback" in r.message for r in caplog.records)


def test_route_returns_atlas_response(monkeypatch):
    import app.services.atlas_router as router_mod

    monkeypatch.setattr(settings, "atlas_enabled", True)
    monkeypatch.setattr(settings, "atlas_email", "a@b.com")
    monkeypatch.setattr(settings, "atlas_password", "secret")
    monkeypatch.setattr(router_mod, "build_atlas_messages", lambda db, sid, q: [])

    def handler(request):
        if request.url.path.endswith("/api/auth/token/"):
            return login_response()
        return chat_response("resposta via atlas", sources=[{"id": "1", "entity": "knowledge", "title": "X"}])

    set_atlas_client(make_client(handler))
    try:
        result = atlas_route(None, "sess", "oi")
    finally:
        from app.services.atlas_client import reset_atlas_client

        reset_atlas_client()
    assert result is not None
    assert result.answer == "resposta via atlas"
    assert len(result.sources) == 1


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def test_atlas_status_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "atlas_enabled", False)
    res = client.get("/api/atlas/status")
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is False
    assert body["base_url"] is None


def test_atlas_status_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "atlas_enabled", True)
    monkeypatch.setattr(settings, "atlas_email", "a@b.com")
    monkeypatch.setattr(settings, "atlas_password", "secret")
    res = client.get("/api/atlas/status")
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True
    assert body["configured"] is True
    assert body["base_url"] == settings.atlas_base_url


def test_atlas_health_unconfigured_503(client, monkeypatch):
    monkeypatch.setattr(settings, "atlas_enabled", False)
    res = client.get("/api/atlas/health")
    assert res.status_code == 503


def test_atlas_health_ok(client, monkeypatch):
    monkeypatch.setattr(settings, "atlas_enabled", True)
    monkeypatch.setattr(settings, "atlas_email", "a@b.com")
    monkeypatch.setattr(settings, "atlas_password", "secret")

    def handler(request):
        if request.url.path.endswith("/api/auth/token/"):
            return login_response()
        return httpx.Response(404)

    set_atlas_client(make_client(handler))
    try:
        res = client.get("/api/atlas/health")
    finally:
        from app.services.atlas_client import reset_atlas_client

        reset_atlas_client()
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Integração no chat (SSE + persistência com source=atlas)
# ---------------------------------------------------------------------------
def test_chat_delegates_to_atlas_persists_source(client, monkeypatch):
    # Cria sessão pela API e envia mensagem com Atlas de pé.
    monkeypatch.setattr(settings, "atlas_enabled", True)
    monkeypatch.setattr(settings, "atlas_email", "a@b.com")
    monkeypatch.setattr(settings, "atlas_password", "secret")

    def handler(request):
        if request.url.path.endswith("/api/auth/token/"):
            return login_response()
        if request.url.path.endswith("/api/assistant/chat/"):
            return chat_response("O Atlas respondeu", sources=[{"id": "s1", "entity": "knowledge", "title": "Cafe"}])
        return httpx.Response(404)

    set_atlas_client(make_client(handler))
    try:
        sid = client.post("/api/sessions", json={"title": None}).json()["id"]
        nav = client.post(f"/api/sessions/{sid}/messages", json={"content": "oi", "stream": True, "tools": True})
        assert nav.status_code == 200
        body = nav.text
        assert "O Atlas respondeu" in body
        assert "atlas" in body

        # Mensagens persistidas: última tem metadata com source=atlas
        msgs = client.get(f"/api/sessions/{sid}/messages").json()
        last = msgs[-1]
        assert last["role"] == "assistant"
        assert last["metadata"]["source"] == "atlas"
        assert last["metadata"]["sources"][0]["title"] == "Cafe"
    finally:
        from app.services.atlas_client import reset_atlas_client

        reset_atlas_client()
        monkeypatch.setattr(settings, "atlas_enabled", False)
