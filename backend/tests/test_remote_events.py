"""Testes da Fase 12.5 — barramento de eventos remotos + endpoint SSE.

Cobre: publish/subscribe, replay ao assinar, sanitização (nunca secrets),
emissão por fonte de evento (identidade, conexão do agente, comando) e o
endpoint `GET /api/remote/events` (erro 503 quando desabilitado; stream via
`remote_event_stream` em modo limitado).
"""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _reset_events():
    from app.remote import events as events_bus

    events_bus.reset_events()
    yield
    events_bus.reset_events()


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _paired_device(db, name="Events"):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, _t = submit_code(db, code=code, device_name=name)
    return device.id, device, _t


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------

def test_publish_subscribe_live_event():
    from app.remote.events import subscribe_events, publish_event, unsubscribe_events

    q, _history = subscribe_events()
    try:
        publish_event("remote.test", {"device_id": "d1", "nested": {"secret": True}})
        item = q.get_nowait()
        assert item["type"] == "remote.test"
        assert item["data"]["device_id"] == "d1"
        assert "nested" not in item["data"]  # sanitizado: aninhado descartado
        assert item["at"]
    finally:
        unsubscribe_events(q)


def test_replay_includes_recent_history():
    from app.remote.events import publish_event, subscribe_events, unsubscribe_events

    publish_event("remote.test.a", {"n": 1})
    publish_event("remote.test.b", {"n": 2})

    q, history = subscribe_events()
    try:
        types = [e["type"] for e in history]
        assert types == ["remote.test.a", "remote.test.b"]
    finally:
        unsubscribe_events(q)


def test_publish_never_raises_for_bad_data():
    from app.remote.events import publish_event, subscribe_events, unsubscribe_events

    publish_event("remote.test", {"ok": True, "bad": [1, 2, {"x": 1}]})
    q, history = subscribe_events()
    try:
        assert history[-1]["data"] == {"ok": True}
    finally:
        unsubscribe_events(q)


# ---------------------------------------------------------------------------
# Emissão por fonte (comandos, identidade, conexão do agente)
# ---------------------------------------------------------------------------

def test_register_and_mark_executed_publish_events(db):
    from app.remote import events as bus
    from app.remote import remote_commands as cmd_service

    dev_id, _device, _t = _paired_device(db)
    cmd = cmd_service.register_command(
        db, device_id=dev_id, command_id="cq-1", cmd_type="tool_call"
    )
    cmd_service.mark_executed(db, cmd, result="ok")

    q, history = bus.subscribe_events()
    try:
        types = [e["type"] for e in history]
        assert "remote.command.registered" in types
        assert "remote.command.executed" in types
    finally:
        bus.unsubscribe_events(q)


def test_mark_pending_approval_publishes(db):
    from app.remote import events as bus
    from app.remote import remote_commands as cmd_service

    dev_id, _device, _t = _paired_device(db)
    cmd = cmd_service.register_command(
        db, device_id=dev_id, command_id="cq-2", cmd_type="tool_call"
    )
    cmd_service.mark_pending_approval(db, cmd)

    q, history = bus.subscribe_events()
    try:
        assert any(
            e["type"] == "remote.command.pending_approval" for e in history
        )
    finally:
        bus.unsubscribe_events(q)


def test_identity_event_publishes(db):
    from app.remote import identity_events as ident

    from app.remote import events as bus

    ident.log_identity_event(
        audit_action="device_created",
        ops_event="remote.device.created",
        meta={"device_id": "d9"},
    )
    q, history = bus.subscribe_events()
    try:
        assert any(e["type"] == "remote.identity.device_created" for e in history)
    finally:
        bus.unsubscribe_events(q)


async def test_agent_connected_publishes_connection_event(db):
    from app.remote import events as bus, runtime
    from app.remote.agent import RemoteAgent
    from app.remote.connection import InMemoryConnection, create_duplex
    from tests.remote_fakes import FakeGateway, wait_for

    dev_id, _device, token = _paired_device(db)
    pipe_a, pipe_b = create_duplex()
    server = FakeGateway(pipe_a)
    await server.start()
    agent = RemoteAgent(
        device_id=dev_id, device_token=token,
        connection_factory=lambda: InMemoryConnection(pipe_b),
        heartbeat_interval=0.2, min_reconnect_delay=0.05, max_reconnect_delay=0.2,
    )
    runtime.reset_remote_gateway()
    runtime._agent = agent
    await agent.start()
    try:
        await wait_for(lambda: agent.snapshot()["authenticated"])
        await asyncio.sleep(0.05)
        _q, history = bus.subscribe_events()
        try:
            assert any(
                e["type"] == "remote.connection.connected"
                and e["data"]["device_id"] == dev_id
                for e in history
            )
        finally:
            bus.unsubscribe_events(_q)
    finally:
        await agent.stop()
        await server.stop()
        runtime.reset_remote_gateway()


# ---------------------------------------------------------------------------
# Endpoint / stream
# ---------------------------------------------------------------------------

def test_sse_endpoint_503_when_disabled():
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app

    original = settings.remote_enabled
    settings.remote_enabled = False
    try:
        client = TestClient(app)
        res = client.get("/api/remote/events")
        assert res.status_code == 503
    finally:
        settings.remote_enabled = original


async def test_sse_event_stream_is_bounded_and_replays():
    import asyncio

    from app.remote.events import remote_event_stream, publish_event

    publish_event("remote.test.seeded", {"n": 1})

    collected = []
    async for entry in remote_event_stream(limit=2):
        collected.append(entry)
    types = [e["type"] for e in collected]
    assert "remote.test.seeded" in types  # replay
    assert "remote.connected" in types  # marcador de prontidão
