"""Testes da Fase 11 — AI Router, DeterministicProvider, AI Core, Central de Operações."""

import json

import pytest


def create_session(client, title=None):
    body = {} if title is None else {"title": title}
    res = client.post("/api/sessions", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def parse_sse(raw: str) -> list[dict]:
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    return [json.loads(ln[6:]) for ln in lines]


# ---------------------------------------------------------------------------
# DeterministicProvider (safety fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deterministic_provider_arithmetic():
    from app.ai.providers.deterministic import DeterministicProvider
    from app.schemas.ai import AIMessage

    p = DeterministicProvider()
    resp = await p.generate([AIMessage(role="user", content="2 + 3 * 4")])
    assert resp.text == "Resultado: 14"


@pytest.mark.asyncio
async def test_deterministic_provider_greeting():
    from app.ai.providers.deterministic import DeterministicProvider
    from app.schemas.ai import AIMessage

    p = DeterministicProvider()
    resp = await p.generate([AIMessage(role="user", content="oi")])
    assert "determinístico" in resp.text


@pytest.mark.asyncio
async def test_deterministic_provider_unknown_fallback():
    from app.ai.providers.deterministic import DeterministicProvider
    from app.schemas.ai import AIMessage

    p = DeterministicProvider()
    resp = await p.generate([AIMessage(role="user", content="explique física quântica")])
    assert "modo determinístico" in resp.text


@pytest.mark.asyncio
async def test_deterministic_provider_embed_raises():
    from app.ai.providers.base import AIProviderError
    from app.ai.providers.deterministic import DeterministicProvider

    p = DeterministicProvider()
    with pytest.raises(AIProviderError):
        await p.embed("texto")


@pytest.mark.asyncio
async def test_deterministic_provider_no_tools_capability():
    from app.ai.providers.deterministic import DeterministicProvider

    p = DeterministicProvider()
    assert "tools" not in p.capabilities
    assert "embed" not in p.capabilities


# ---------------------------------------------------------------------------
# AI Router
# ---------------------------------------------------------------------------

def test_router_deterministic_fallback_when_gemini_unconfigured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    # Desabilita o Local LLM para isolar o cenário "sem Gemini → determinístico"
    # (o Local LLM é o principal; com ele presente, `resolve` devolveria local).
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_local_llm_enabled", False)
    from app.ai.registry import reset_ai_router, get_ai_router

    reset_ai_router()
    router = get_ai_router()
    provider = router.resolve(task="generate")
    assert provider.name == "deterministic"
    assert provider.is_configured


def test_router_statuses_include_gemini_unconfigured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from app.ai.registry import reset_ai_router, get_ai_router

    reset_ai_router()
    statuses = get_ai_router().statuses()
    by_name = {s.provider: s for s in statuses}
    assert by_name["gemini"].status == "unconfigured"
    assert by_name["deterministic"].status == "ok"


def test_ops_overview_reports_providers(client):
    data = client.get("/api/ops/overview").json()
    names = {p["name"] for p in data["providers"]}
    assert "gemini" in names
    assert "deterministic" in names
    assert data["ai_core"]["router_ready"] is True


def test_ops_providers_endpoint(client):
    data = client.get("/api/ops/providers").json()
    by_name = {p["name"]: p for p in data}
    assert by_name["deterministic"]["configured"] is True


# ---------------------------------------------------------------------------
# Observabilidade (execution_events)
# ---------------------------------------------------------------------------

def test_chat_records_events(client, fake_ai):
    session = create_session(client)
    res = client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"content": "oi", "stream": False},
    )
    assert res.status_code == 200
    events = client.get("/api/ops/events").json()
    types = {e["event_type"] for e in events}
    assert "chat.started" in types
    assert "provider.selected" in types
    assert "chat.completed" in types


def test_events_are_sanitized(client, fake_ai):
    session = create_session(client)
    client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"content": "segredo-top-secret", "stream": False},
    )
    events = client.get("/api/ops/events?session_id=" + session["id"]).json()
    for e in events:
        raw = json.dumps(e)
        assert "segredo-top-secret" not in raw


def test_events_filter_by_type(client, fake_ai):
    session = create_session(client)
    client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"content": "oi", "stream": False},
    )
    events = client.get("/api/ops/events?event_type=provider.selected").json()
    assert events
    assert all(e["event_type"] == "provider.selected" for e in events)


# ---------------------------------------------------------------------------
# Central de Operações overview
# ---------------------------------------------------------------------------

def test_ops_overview_shape(client):
    data = client.get("/api/ops/overview").json()
    assert "ai_core" in data
    assert "memory" in data
    assert "tasks" in data
    assert "tools" in data
    assert "atlas" in data
    assert "system" in data
    assert data["system"]["db"] is True