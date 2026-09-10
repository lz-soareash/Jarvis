"""Testes da Fase 12.1 — transporte remoto (Gateway) e segurança.

Cobre: feature flags, protocolo versionado, estados de conexão, connection
manager, RemoteGateway (handshake/heartbeat/command), auditoria + ops,
segurança (sem portas inbound, sem execução de tools, token fora de logs) e
regressão do lifespan.
"""

from __future__ import annotations

import json
import logging
import sys
import types

import pytest

from app.remote import (
    InvalidEnvelopeError,
    MessageType,
    RemoteEnvelope,
    UnknownMessageTypeError,
    UnsupportedVersionError,
    build_message,
    decode_message,
    encode_message,
)
from app.remote.config import is_remote_configured, is_remote_enabled
from app.remote.connection import (
    ConnectionState,
    InMemoryConnection,
    WebSocketConnection,
    create_duplex,
)
from app.remote.gateway import (
    AUDIT_CONNECTED,
    AUDIT_CONNECTION_FAILED,
    AUDIT_DISCONNECTED,
    OPS_CONNECTED,
    OPS_CONNECTION_FAILED,
    OPS_DISCONNECTED,
    RemoteGateway,
)
from app.remote.manager import RemoteConnectionManager
from app.remote.ratelimit import RateLimiter
from app.remote.runtime import reset_remote_gateway, should_start
from tests.remote_fakes import (
    FailingConnection,
    FakeGateway,
    RecordingConnection,
    wait_for,
)

# ---------------------------------------------------------------------------
# Feature flag (REMOTE_ENABLED=false por padrão)
# ---------------------------------------------------------------------------


def test_remote_disabled_by_default_in_tests():
    assert is_remote_enabled() is False


def test_should_start_false_when_disabled():
    assert should_start() is False


def test_should_start_requires_config(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "remote_enabled", True)
    monkeypatch.setattr(settings, "remote_gateway_url", "")
    monkeypatch.setattr(settings, "remote_device_id", "")
    monkeypatch.setattr(settings, "remote_device_token", "")
    assert should_start() is False  # habilitado mas sem endpoint/identidade

    monkeypatch.setattr(settings, "remote_gateway_url", "wss://gw.example.com/ws")
    monkeypatch.setattr(settings, "remote_device_id", "jarvis-pc")
    assert should_start() is True


def test_is_remote_configured_helper(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "remote_gateway_url", "")
    monkeypatch.setattr(settings, "remote_device_id", "")
    assert is_remote_configured() is False
    monkeypatch.setattr(settings, "remote_gateway_url", "wss://gw.example.com/ws")
    monkeypatch.setattr(settings, "remote_device_id", "jarvis-pc")
    assert is_remote_configured() is True


# ---------------------------------------------------------------------------
# Protocolo versionado
# ---------------------------------------------------------------------------


def test_message_type_vocabulary_matches_spec():
    assert {member.value for member in MessageType} == {
        "hello",
        "hello_ack",
        "heartbeat",
        "heartbeat_ack",
        "command",
        "command_ack",
        "command_result",
        "error",
        # Fase 23 — WAN relay (tipos ADITIVOS; o protocolo v1 continua intacto).
        "auth",
        "auth_result",
        "message",
        "message_ack",
        "message_result",
        "agent_event",
        "computer_task",
        "computer_result",
        "approval_respond",
        "approval_result",
        "close",
    }


def test_build_message_defaults():
    env = build_message(MessageType.HELLO, device_id="jarvis-pc")
    assert env.version == 1
    assert env.type == MessageType.HELLO
    assert env.message_id
    assert env.timestamp
    assert env.device_id == "jarvis-pc"
    assert env.command_id is None
    assert env.payload == {}


def test_build_message_with_command_id_and_payload():
    env = build_message(
        MessageType.COMMAND_ACK,
        device_id="jarvis-pc",
        command_id="cmd-1",
        payload={"status": "received"},
    )
    assert env.command_id == "cmd-1"
    assert env.payload == {"status": "received"}


def test_encode_decode_roundtrip():
    original = build_message(
        MessageType.COMMAND, device_id="jarvis-pc", command_id="cmd-9", payload={"tool": "x"}
    )
    decoded = decode_message(encode_message(original))
    assert decoded.model_dump() == original.model_dump()
    assert isinstance(decoded.type, MessageType)


def test_is_command_helper():
    asset_command = build_message(MessageType.COMMAND, command_id="c")
    assert asset_command.is_command
    ack = build_message(MessageType.COMMAND_ACK, command_id="c")
    assert not ack.is_command


def test_decode_rejects_unsupported_version():
    env = build_message(MessageType.HELLO)
    raw = json.loads(encode_message(env))
    raw["version"] = 999
    with pytest.raises(UnsupportedVersionError):
        decode_message(json.dumps(raw))


def test_decode_rejects_unknown_type():
    env = build_message(MessageType.HELLO)
    raw = json.loads(encode_message(env))
    raw["type"] = "teleport"
    with pytest.raises(UnknownMessageTypeError):
        decode_message(json.dumps(raw))


def test_decode_rejects_invalid_json():
    with pytest.raises(InvalidEnvelopeError):
        decode_message("não é json")


def test_decode_rejects_missing_required_fields():
    with pytest.raises(InvalidEnvelopeError):
        decode_message(json.dumps({"version": 1, "type": "hello"}))


def test_decode_rejects_extra_fields():
    env = build_message(MessageType.HELLO)
    raw = json.loads(encode_message(env))
    raw["payload_novo"] = "hack"
    with pytest.raises(InvalidEnvelopeError):
        decode_message(json.dumps(raw))


# ---------------------------------------------------------------------------
# Conexão / estados
# ---------------------------------------------------------------------------


async def test_connection_state_transitions():
    pipe_a, pipe_b = create_duplex()
    conn = InMemoryConnection(pipe_a)
    assert conn.state == ConnectionState.DISCONNECTED
    assert not conn.is_connected
    await conn.connect()
    assert conn.state == ConnectionState.CONNECTED
    assert conn.is_connected
    await conn.close()
    assert conn.state == ConnectionState.DISCONNECTED


async def test_shutdown_never_stuck_and_unblocks_receive():
    pipe_a, _pipe_b = create_duplex()
    conn = InMemoryConnection(pipe_a)
    await conn.connect()
    receive_task = __import__("asyncio").create_task(conn.receive())
    await __import__("asyncio").sleep(0)
    await conn.shutdown()
    assert conn.state == ConnectionState.DISCONNECTED
    with pytest.raises(ConnectionError):
        await receive_task


async def test_send_requires_connected():
    pipe_a, _pipe_b = create_duplex()
    conn = InMemoryConnection(pipe_a)
    with pytest.raises(ConnectionError):
        await conn.send(build_message(MessageType.HELLO))


async def test_failing_connection_raises_on_connect():
    conn = FailingConnection()
    with pytest.raises(ConnectionError):
        await conn.connect()
    # falhou no CONNECTING — nunca fica preso em CONNECTED
    assert conn.state == ConnectionState.CONNECTING


async def test_memory_pipe_duplex_forwards_messages():
    pipe_a, pipe_b = create_duplex()
    conn_a, conn_b = InMemoryConnection(pipe_a), InMemoryConnection(pipe_b)
    await conn_a.connect()
    await conn_b.connect()
    await conn_a.send(build_message(MessageType.HELLO, device_id="a"))
    received = await conn_b.receive()
    assert received.type == MessageType.HELLO


async def test_websocket_connection_is_outbound_client(monkeypatch):
    """O transporte usa apenas `websockets.connect` (cliente) — jamais `serve`."""

    class _FakeWS:
        async def send(self, data):  # noqa: ARG002
            pass

        async def recv(self):
            await __import__("asyncio").sleep(3600)

        async def close(self):
            pass

    state: dict = {}

    async def _fake_connect(url, **kwargs):
        state["url"] = url
        state["kwargs"] = kwargs
        return _FakeWS()

    fake_module = types.SimpleNamespace(connect=_fake_connect)
    assert not hasattr(fake_module, "serve")  # o fake nem oferece lado servidor
    monkeypatch.setitem(sys.modules, "websockets", fake_module)

    conn = WebSocketConnection(
        "wss://gw.example.com:443/ws", headers={"Authorization": "Bearer tok"}
    )
    await conn.connect()
    assert conn.state == ConnectionState.CONNECTED
    assert state["url"] == "wss://gw.example.com:443/ws"
    assert state["kwargs"]["additional_headers"] == {"Authorization": "Bearer tok"}
    await conn.close()
    assert conn.state == ConnectionState.DISCONNECTED


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------


async def test_manager_dispatches_registered_handler():
    pipe_a, pipe_b = create_duplex()
    server = FakeGateway(pipe_a)
    await server.start()
    conn = RecordingConnection(pipe_b)
    manager = RemoteConnectionManager(conn, device_id="jarvis-pc")
    calls: list[RemoteEnvelope] = []

    async def handler(env):
        calls.append(env)
        return build_message(MessageType.HELLO_ACK, device_id="jarvis-pc")

    manager.register(MessageType.HELLO, handler)
    await manager.start()
    await server.push_message(build_message(MessageType.HELLO, device_id="x"))
    await wait_for(lambda: any(e.type == MessageType.HELLO_ACK for e in server.received))
    assert len(calls) == 1
    await manager.stop()
    await server.stop()


async def test_manager_unknown_type_replies_error():
    pipe_a, pipe_b = create_duplex()
    server = FakeGateway(pipe_a)
    await server.start()
    manager = RemoteConnectionManager(RecordingConnection(pipe_b), device_id="jarvis-pc")
    await manager.start()
    await server.push_message(
        build_message(MessageType.COMMAND_RESULT, device_id="x", command_id="c9")
    )
    await wait_for(lambda: any(e.type == MessageType.ERROR for e in server.received))
    error = next(e for e in server.received if e.type == MessageType.ERROR)
    assert error.payload["error"] == "unsupported_type"
    await manager.stop()
    await server.stop()


async def test_manager_handler_failure_replies_error():
    pipe_a, pipe_b = create_duplex()
    server = FakeGateway(pipe_a)
    await server.start()
    manager = RemoteConnectionManager(RecordingConnection(pipe_b), device_id="jarvis-pc")

    async def boom(_env):
        raise RuntimeError("simulação")

    manager.register(MessageType.HELLO, boom)
    await manager.start()
    await server.push_message(build_message(MessageType.HELLO))
    await wait_for(lambda: any(e.type == MessageType.ERROR for e in server.received))
    error = next(e for e in server.received if e.type == MessageType.ERROR)
    assert error.payload["error"] == "handler_error"
    await manager.stop()
    await server.stop()


async def test_manager_heartbeat_loop_sends_heartbeats():
    pipe_a, pipe_b = create_duplex()
    conn = RecordingConnection(pipe_b)
    manager = RemoteConnectionManager(conn, device_id="jarvis-pc", heartbeat_interval=0.01)
    await manager.start()
    await wait_for(lambda: any(e.type == MessageType.HEARTBEAT for e in conn.sent))
    await manager.stop()
    assert conn.state == ConnectionState.DISCONNECTED


async def test_manager_stop_is_graceful():
    pipe_a, _pipe_b = create_duplex()
    conn = InMemoryConnection(pipe_a)
    manager = RemoteConnectionManager(conn, device_id="jarvis-pc", heartbeat_interval=0.01)
    await manager.start()
    assert conn.is_connected
    await __import__("asyncio").sleep(0.02)
    await manager.stop()
    assert conn.state == ConnectionState.DISCONNECTED
    assert manager._running is False


# ---------------------------------------------------------------------------
# RemoteGateway (cliente) + FakeGateway (servidor)
# ---------------------------------------------------------------------------


def _audit_actions():
    from app.db.session import SessionLocal
    from app.models import AuditLog

    with SessionLocal() as db:
        return [row.action for row in db.query(AuditLog).all()]


def _ops_event_types():
    from app.db.session import SessionLocal
    from app.models import ExecutionEvent

    with SessionLocal() as db:
        return [row.event_type for row in db.query(ExecutionEvent).all()]


async def _new_pair(*, heartbeat_interval: float = 30.0, device_token: str = "fake-token"):
    pipe_a, pipe_b = create_duplex()
    server = FakeGateway(pipe_a)
    await server.start()
    client = RemoteGateway(
        RecordingConnection(pipe_b),
        device_id="jarvis-pc",
        device_token=device_token,
        heartbeat_interval=heartbeat_interval,
    )
    return server, client


async def test_gateway_hello_handshake():
    server, client = await _new_pair()
    await client.start()
    await wait_for(lambda: any(e.type == MessageType.HELLO for e in server.received))
    await wait_for(
        lambda: any(e.type == MessageType.HELLO_ACK for e in client.manager.connection.received)
    )
    assert client.connected
    assert AUDIT_CONNECTED in _audit_actions()
    assert OPS_CONNECTED in _ops_event_types()
    await client.stop()
    await server.stop()


async def test_gateway_command_ack_not_implemented_and_no_tools_executed():
    server, client = await _new_pair()
    await client.start()
    await server.push_command("cmd-123", {"tool": "computer_poweroff"})
    # O cliente envia o COMMAND_ACK de volta para a Gateway (servidor fake).
    await wait_for(
        lambda: any(
            e.type == MessageType.COMMAND_ACK and e.command_id == "cmd-123"
            for e in server.received
        )
    )
    ack = next(e for e in server.received if e.type == MessageType.COMMAND_ACK)
    assert ack.command_id == "cmd-123"
    assert ack.payload["status"] == "received"
    assert ack.payload["execution"] == "not_implemented"
    # Confirmação no lado do cliente (enviado pelo próprio transporte).
    assert any(
        e.type == MessageType.COMMAND_ACK and e.command_id == "cmd-123"
        for e in client.manager.connection.sent
    )

    from app.db.session import SessionLocal
    from app.models import AuditLog

    with SessionLocal() as db:
        tools = {row.tool for row in db.query(AuditLog).all()}
    # Transporte NUNCA executa tools: auditoria contém apenas eventos de transporte.
    assert tools == {"transport"}
    await client.stop()
    await server.stop()


async def test_gateway_disconnect_logs_audit():
    server, client = await _new_pair()
    await client.start()
    await wait_for(lambda: any(e.type == MessageType.HELLO for e in server.received))
    await client.stop()
    assert AUDIT_DISCONNECTED in _audit_actions()
    assert OPS_DISCONNECTED in _ops_event_types()
    assert not client.connected
    await server.stop()


async def test_gateway_connection_failed_logs_audit_and_ops():
    conn = FailingConnection()
    gw = RemoteGateway(conn, device_id="jarvis-pc", device_token="t")
    with pytest.raises(ConnectionError):
        await gw.start()
    assert not gw.connected
    assert AUDIT_CONNECTION_FAILED in _audit_actions()
    assert OPS_CONNECTION_FAILED in _ops_event_types()


async def test_gateway_audits_messages_sent_and_received():
    server, client = await _new_pair()
    await client.start()
    await wait_for(lambda: any(e.type == MessageType.HELLO for e in server.received))
    await wait_for(
        lambda: any(e.type == MessageType.HELLO_ACK for e in client.manager.connection.received)
    )
    actions = _audit_actions()
    assert "remote_message_sent" in actions  # hello
    assert "remote_message_received" in actions  # hello_ack
    event_types = _ops_event_types()
    assert "remote.message_sent" in event_types
    assert "remote.message_received" in event_types
    await client.stop()
    await server.stop()


async def test_gateway_heartbeat_ack_received():
    server, client = await _new_pair(heartbeat_interval=0.01)
    await client.start()
    await wait_for(
        lambda: any(e.type == MessageType.HEARTBEAT_ACK for e in client.manager.connection.received)
    )
    assert client.connected
    await client.stop()
    await server.stop()


# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------


async def test_token_never_in_logs_or_audit(caplog):
    caplog.set_level(logging.INFO, logger="jarvis.remote")
    secret = "SUPER-SECRET-TRANSPORT-TOKEN-42"
    server, client = await _new_pair(device_token=secret)
    await client.start()
    await server.push_command("cmd-sec")
    await wait_for(
        lambda: any(
            e.type == MessageType.COMMAND_ACK and e.command_id == "cmd-sec"
            for e in server.received
        )
    )
    await client.stop()
    await server.stop()

    assert secret not in caplog.text
    from app.db.session import SessionLocal
    from app.models import AuditLog, ExecutionEvent

    with SessionLocal() as db:
        details = [row.detail or "" for row in db.query(AuditLog).all()]
        metas = [row.meta_json or "" for row in db.query(ExecutionEvent).all()]
    assert all(secret not in d for d in details)
    assert all(secret not in m for m in metas)


async def test_auth_header_used_on_transport(monkeypatch):
    """O token é transportado apenas no cabeçalho de auth (nunca no corpo/log)."""
    pipe_a, pipe_b = create_duplex()
    server = FakeGateway(pipe_a)
    await server.start()
    client = RemoteGateway(
        RecordingConnection(pipe_b), device_id="jarvis-pc", device_token="algo-secreto"
    )
    monkeypatch.setattr(
        client, "_token", "algo-secreto"
    )  # apenas p/ explicitar; envio ocorre no runtime via headers
    assert client._token == "algo-secreto"
    await client.start()
    await wait_for(lambda: any(e.type == MessageType.HELLO for e in server.received))
    # Nenhuma mensagem do protocolo carrega o token
    all_payloads = [e.model_dump() for e in server.received]
    assert all("algo-secreto" not in json.dumps(p) for p in all_payloads)
    await client.stop()
    await server.stop()


# ---------------------------------------------------------------------------
# Rate limiting (foundation)
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_burst_up_to_capacity():
    limiter = RateLimiter(capacity=3, refill_per_second=1000.0)
    assert [limiter.allow() for _ in range(3)] == [True, True, True]
    assert limiter.allow() is False  # rajada esgotada antes do refill


def test_rate_limiter_rejects_non_positive_values():
    with pytest.raises(ValueError):
        RateLimiter(capacity=0, refill_per_second=1)
    with pytest.raises(ValueError):
        RateLimiter(capacity=1, refill_per_second=0)


async def test_rate_limiter_acquire_waits_and_recovers():
    limiter = RateLimiter(capacity=1, refill_per_second=1000.0)
    assert limiter.allow() is True
    started = __import__("time").monotonic()
    await limiter.acquire()  # refill quase instantâneo (1000 tokens/s)
    assert __import__("time").monotonic() - started < 1.0


async def test_manager_with_send_limiter_throttles_outbound():
    import time

    pipe_a, pipe_b = create_duplex()
    server = FakeGateway(pipe_a)
    await server.start()
    conn = RecordingConnection(pipe_b)
    # capacidade 2, refill lento (2/s): 3º envio precisa esperar ~0.5s
    limiter = RateLimiter(capacity=2, refill_per_second=2.0)
    manager = RemoteConnectionManager(
        conn, device_id="jarvis-pc", heartbeat_interval=30.0, send_limiter=limiter
    )
    await manager.start()
    t0 = time.monotonic()
    await manager.send(build_message(MessageType.HELLO))
    await manager.send(build_message(MessageType.HELLO))
    await manager.send(build_message(MessageType.HELLO))  # throttled
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.4  # precisou esperar o refill
    assert len(conn.sent) == 3
    await manager.stop()
    await server.stop()


# ---------------------------------------------------------------------------
# Regressão / runtime
# ---------------------------------------------------------------------------


def test_lifespan_does_not_start_gateway_when_disabled():
    from app.main import app

    assert not hasattr(app.state, "remote_gateway")


def test_runtime_reset_helper():
    reset_remote_gateway()
    assert should_start() is False