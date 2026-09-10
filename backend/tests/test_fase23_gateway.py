"""Testes da Fase 23 - Core Link WAN (rele WebSocket + link do Core).

Parte A: RelayHub (roteamento do rele, uma hop movel -> core -> movel):
handshake `core` (peer_token), politica de mobile pendente/atrelado, trust
relay (device so e atrelado por auth_result ok:true do Core), roteamento por
target_device_id, anti-spoof de target e substituicao do core.

Parte B: CoreLink end-to-end em processo (hub + link + clientes sobre
MemoryPipe): handshake -> auth -> mensagem, SEM REDE. Toda autoridade de
auth/permissao/turno continua acontecendo EXCLUSIVAMENTE no Core.

Sem credenciais reais; observabilidade sanitizada e verificada.
"""

from __future__ import annotations

import asyncio

import pytest

from app.gateway.backpressure import Mailbox
from app.gateway.hub import RelayHub
from app.remote.connection import InMemoryConnection, create_duplex
from app.remote.protocol import MessageType, RemoteEnvelope, build_message

RELAY_TOKEN = "test-token"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_hub(monkeypatch, *, peer_token: str = RELAY_TOKEN) -> RelayHub:
    hub = RelayHub()
    hub.settings.peer_token = peer_token
    return hub


def _gateway_peer(hub) -> object:
    return hub.registry.register(Mailbox)


def _env(type_: MessageType, *, device_id=None, request_id=None, payload=None) -> RemoteEnvelope:
    return build_message(type_, device_id=device_id, request_id=request_id, payload=payload or {})


async def _run_peer(hub: RelayHub, pipe, peer) -> asyncio.Task:
    """Loop de servidor de um peer: RX (hub.handle) + TX (mailbox)."""

    async def rx():
        while True:
            envelope = await pipe.recv_from_peer()
            reply = await hub.handle(envelope, peer.peer_id)
            if reply is not None:
                await pipe.send_to_peer(reply)
            if envelope.type == MessageType.CLOSE:
                return

    async def tx():
        while True:
            item = peer.mailbox.get()
            if item is None:
                if peer.mailbox._closed:  # noqa: SLF001
                    return
                await asyncio.sleep(0.005)
                continue
            await pipe.send_to_peer(item)
            if item.type == MessageType.CLOSE:
                return

    async def runner():
        await asyncio.gather(rx(), tx())

    return asyncio.create_task(runner())


class _MobileClient:
    """Cliente fino sobre MemoryPipe: hello -> auth -> message (frames)."""

    def __init__(self, pipe) -> None:
        self.conn = InMemoryConnection(pipe)
        self.received: list[RemoteEnvelope] = []

    async def start(self) -> None:
        await self.conn.connect()

    async def send(self, type_: MessageType, *, payload=None, request_id=None) -> None:
        await self.conn.send(_env(type_, payload=payload, request_id=request_id))

    async def recv(self, timeout: float = 3.0) -> RemoteEnvelope:
        envelope = await asyncio.wait_for(self.conn.receive(), timeout=timeout)
        self.received.append(envelope)
        return envelope

    async def close(self) -> None:
        await self.conn.close()


def _pair_mobile_device():
    """Cria um device pareado de verdade no banco de teste -> (device, token)."""
    from app.db.session import SessionLocal
    from app.remote.pairing import create_pairing, submit_code

    db = SessionLocal()
    try:
        _, code = create_pairing(db)
        device, token = submit_code(db, code=code, device_name="Mobile WAN")
        return device, token
    finally:
        db.close()


async def _wait(predicate, timeout: float = 4.0, interval: float = 0.01) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condicao nao satisfeita no tempo limite")


# ---------------------------------------------------------------------------
# Parte A - hub do rele (RelayHub)
# ---------------------------------------------------------------------------


async def test_gateway_hello_core_with_valid_token_promotes(monkeypatch):
    hub = _make_hub(monkeypatch)
    core = _gateway_peer(hub)
    reply = await hub.handle(
        _env(
            MessageType.HELLO,
            payload={
                "role": "core",
                "peer_token": RELAY_TOKEN,
                "device_id": "core-wan",
            },
        ),
        core.peer_id,
    )
    assert reply is not None and reply.type == MessageType.HELLO_ACK
    assert reply.payload.get("role") == "core"
    assert reply.payload.get("ok") is True
    promoted = hub.registry.core()
    assert promoted is not None and promoted.peer_id == core.peer_id
    assert promoted.device_id == "core-wan"


async def test_gateway_hello_core_rejects_wrong_token(monkeypatch):
    hub = _make_hub(monkeypatch)
    core = _gateway_peer(hub)
    reply = await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": "wrong"}),
        core.peer_id,
    )
    assert reply is not None and reply.type == MessageType.ERROR
    assert reply.payload.get("error") == "core_denied"
    assert hub.registry.core() is None


async def test_gateway_hello_core_denied_without_configured_token(monkeypatch):
    hub = _make_hub(monkeypatch, peer_token="")
    core = _gateway_peer(hub)
    reply = await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": "any"}),
        core.peer_id,
    )
    assert reply is not None and reply.type == MessageType.ERROR
    assert reply.payload.get("error") == "core_denied"


async def test_gateway_hello_mobile_stays_pending(monkeypatch):
    hub = _make_hub(monkeypatch)
    mobile = _gateway_peer(hub)
    reply = await hub.handle(
        _env(MessageType.HELLO, payload={"role": "mobile"}),
        mobile.peer_id,
    )
    assert reply is not None and reply.type == MessageType.HELLO_ACK
    assert reply.payload.get("role") == "mobile"
    assert mobile.role == "mobile"  # aprovado, ainda nao atrelado a device


async def test_gateway_pending_mobile_cannot_send_message(monkeypatch):
    hub = _make_hub(monkeypatch)
    mobile = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    reply = await hub.handle(
        _env(MessageType.MESSAGE, payload={"content": "oi"}),
        mobile.peer_id,
    )
    assert reply is not None and reply.type == MessageType.ERROR
    assert reply.payload.get("error") == "not_authenticated"


async def test_gateway_pending_mobile_auth_forwarded_to_core(monkeypatch):
    hub = _make_hub(monkeypatch)
    core = _gateway_peer(hub)
    await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    mobile = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)

    reply = await hub.handle(
        _env(
            MessageType.AUTH,
            request_id="r-auth",
            payload={"token": "abc", "target_device_id": "spoof"},
        ),
        mobile.peer_id,
    )
    assert reply is None  # roteado, resposta so vem do Core
    routed = core.mailbox.get()
    assert routed is not None and routed.type == MessageType.AUTH
    payload = routed.payload or {}
    assert "target_device_id" not in payload  # relé remove target escolhido pelo móvel
    assert "source_device_id" not in payload  # device ainda nao atrelado
    assert hub.registry.counters["auth_requests"] == 1
    assert hub.registry is not None
    assert "r-auth" in hub._pending_auths  # noqa: SLF001


async def test_gateway_auth_result_ok_binds_device(monkeypatch):
    hub = _make_hub(monkeypatch)
    core = _gateway_peer(hub)
    await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    mobile = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    await hub.handle(
        _env(MessageType.AUTH, request_id="r-ok", payload={"token": "abc"}),
        mobile.peer_id,
    )

    result = _env(
        MessageType.AUTH_RESULT,
        request_id="r-ok",
        payload={"ok": True, "target_device_id": "dev-1", "session_id": "s-1"},
    )
    assert await hub.handle(result, core.peer_id) is None
    assert hub.registry.mobile("dev-1") is mobile  # atrelado pelo Core
    assert mobile.device_id == "dev-1"
    delivered = mobile.mailbox.get()
    assert delivered is not None and delivered.type == MessageType.AUTH_RESULT
    assert hub.registry.counters["auth_results"] == 1
    assert "r-ok" not in hub._pending_auths  # noqa: SLF001


async def test_gateway_auth_result_negative_never_binds(monkeypatch):
    hub = _make_hub(monkeypatch)
    core = _gateway_peer(hub)
    await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    mobile = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    await hub.handle(
        _env(MessageType.AUTH, request_id="r-neg", payload={"token": "abc"}),
        mobile.peer_id,
    )

    result = _env(
        MessageType.AUTH_RESULT,
        request_id="r-neg",
        payload={"ok": False, "target_device_id": "blocked"},
    )
    assert await hub.handle(result, core.peer_id) is None
    assert hub.registry.mobile("blocked") is None
    sent = mobile.mailbox.get()
    assert sent is not None and sent.type == MessageType.AUTH_RESULT
    assert "r-neg" not in hub._pending_auths  # noqa: SLF001


async def test_gateway_bound_mobile_message_forwarded_with_device(monkeypatch):
    hub = _make_hub(monkeypatch)
    core = _gateway_peer(hub)
    await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    mobile = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    await hub.handle(
        _env(MessageType.AUTH, request_id="r-bind", payload={"token": "abc"}),
        mobile.peer_id,
    )
    await hub.handle(
        _env(
            MessageType.AUTH_RESULT,
            request_id="r-bind",
            payload={"ok": True, "target_device_id": "dev-1"},
        ),
        core.peer_id,
    )
    core.mailbox.drain()

    assert await hub.handle(
        _env(MessageType.MESSAGE, payload={"content": "oi", "target_device_id": "spoof"}),
        mobile.peer_id,
    ) is None
    routed = core.mailbox.get()
    assert routed is not None and routed.type == MessageType.MESSAGE
    assert routed.device_id == "dev-1"  # rotulado pelo relé, nunca pelo móvel
    assert "target_device_id" not in (routed.payload or {})  # spoof removido
    assert (routed.payload or {}).get("source_device_id") == "dev-1"


async def test_gateway_core_routes_result_to_mobile_by_target(monkeypatch):
    hub = _make_hub(monkeypatch)
    core = _gateway_peer(hub)
    await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    mobile = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    await hub.handle(
        _env(MessageType.AUTH, request_id="r-route", payload={"token": "abc"}),
        mobile.peer_id,
    )
    await hub.handle(
        _env(
            MessageType.AUTH_RESULT,
            request_id="r-route",
            payload={"ok": True, "target_device_id": "dev-1"},
        ),
        core.peer_id,
    )

    assert await hub.handle(
        _env(
            MessageType.MESSAGE_RESULT,
            device_id="core-wan",
            payload={"ok": True, "target_device_id": "dev-1"},
        ),
        core.peer_id,
    ) is None
    delivered = mobile.mailbox.get()
    while delivered is not None and delivered.type != MessageType.MESSAGE_RESULT:
        delivered = mobile.mailbox.get()
    assert delivered is not None and delivered.type == MessageType.MESSAGE_RESULT
    assert hub.registry.counters["relays"] >= 3


async def test_gateway_core_result_without_target_dropped(monkeypatch):
    hub = _make_hub(monkeypatch)
    core = _gateway_peer(hub)
    await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    mobile = _gateway_peer(hub)
    before = hub.registry.counters["relays_dropped"]
    assert await hub.handle(
        _env(MessageType.MESSAGE_RESULT, payload={"ok": True}),
        core.peer_id,
    ) is None
    assert hub.registry.counters["relays_dropped"] == before + 1


async def test_gateway_mobile_cannot_relay_to_another_mobile(monkeypatch):
    hub = _make_hub(monkeypatch)
    core = _gateway_peer(hub)
    await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    mob_a = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mob_a.peer_id)
    await hub.handle(
        _env(MessageType.AUTH, request_id="r-a", payload={"token": "abc"}),
        mob_a.peer_id,
    )
    await hub.handle(
        _env(
            MessageType.AUTH_RESULT,
            request_id="r-a",
            payload={"ok": True, "target_device_id": "dev-a"},
        ),
        core.peer_id,
    )
    mob_b = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mob_b.peer_id)
    core.mailbox.drain()  # esvazia o AUTH prévio (não a mensagem)

    # mob_a tenta empurrar mensagem para mob_b: nunca roteia mobile->mobile.
    # Mas mobile->core é permitido (bound): MESSAGE segue ao Core, NUNCA ao mob_b.
    reply = await hub.handle(
        _env(MessageType.MESSAGE, payload={"content": "oi"}),
        mob_a.peer_id,
    )
    assert reply is None
    assert mob_b.mailbox.get() is None
    routed = core.mailbox.get()
    assert routed is not None and routed.type == MessageType.MESSAGE
    assert (routed.payload or {}).get("source_device_id") == "dev-a"


async def test_gateway_replaced_core_closes_previous_gracefully(monkeypatch):
    hub = _make_hub(monkeypatch)
    first = _gateway_peer(hub)
    await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        first.peer_id,
    )
    second = _gateway_peer(hub)
    reply = await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN, "device_id": "core-2"}),
        second.peer_id,
    )
    assert reply is not None and reply.type == MessageType.HELLO_ACK
    assert hub.registry.core().peer_id == second.peer_id
    closed = first.mailbox.get()
    assert closed is not None and closed.type == MessageType.CLOSE
    assert (closed.payload or {}).get("reason") == "core_replaced"


# ---------------------------------------------------------------------------
# Parte B - CoreLink end-to-end em processo (hub + link + clientes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_link_end_to_end_auth_and_message(monkeypatch, fake_ai):
    from app.remote.link import CoreLink

    monkeypatch.setattr("app.remote.link.get_default_provider", lambda: fake_ai)
    hub = _make_hub(monkeypatch)
    core_peer = _gateway_peer(hub)
    mobile_peer = _gateway_peer(hub)

    core_local, core_serv = create_duplex()
    mob_local, mob_serv = create_duplex()

    tasks = [
        await _run_peer(hub, core_serv, core_peer),
        await _run_peer(hub, mob_serv, mobile_peer),
    ]

    # Cliente real (token emitido pelo banco de teste).
    device, token = _pair_mobile_device()

    # CoreLink WAN: conexão do Core ao relé.
    link = CoreLink(
        device_id="core-wan",
        gateway_url="mem://relay",
        peer_token=RELAY_TOKEN,
        connection_factory=lambda: InMemoryConnection(core_local),
        connect_timeout=2.0,
    )
    await link.start()
    try:
        await _wait(lambda: link._connected)

        # AUTH_RESULT do _on_auth atrela o móvel no relé (trust relay).
        mobile = _MobileClient(mob_local)
        await mobile.start()
        await mobile.send(
            MessageType.HELLO,
            payload={"role": "mobile"},
        )
        ack = await mobile.recv()
        assert ack.type == MessageType.HELLO_ACK

        await mobile.send(MessageType.AUTH, payload={"token": token}, request_id="auth-1")
        auth_result = await mobile.recv()
        assert auth_result.type == MessageType.AUTH_RESULT
        assert auth_result.payload.get("ok") is True
        assert auth_result.payload.get("device_id") == device.id
        assert auth_result.request_id == "auth-1"  # correlação preservada pelo relé
        await _wait(lambda: hub.registry.mobile(device.id) is not None)

        # Mensagem conversacional: turno EXCLUSIVO no Core (MESMO AI Core).
        await mobile.send(
            MessageType.MESSAGE,
            payload={"content": "oi", "stream": False, "tools": False},
            request_id="msg-1",
        )
        seen = 0
        result = None
        while seen < 8:
            frame = await mobile.recv()
            if frame.type == MessageType.MESSAGE_RESULT:
                result = frame
                break
            seen += 1
        assert result is not None, "MESSAGE_RESULT não chegou ao móvel"
        assert result.payload.get("ok") is True
        assert result.payload.get("status") == "completed"
        assert link.stats["messages_total"] >= 1
    finally:
        await link.stop()
        for t in tasks:
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        core_serv.close()
        mob_serv.close()


@pytest.mark.asyncio
async def test_core_link_ends_when_peer_token_rejected(monkeypatch):
    from app.remote.link import CoreLink

    hub = _make_hub(monkeypatch, peer_token="real-remote-token")
    core_peer = _gateway_peer(hub)
    core_local, core_serv = create_duplex()
    task = await _run_peer(hub, core_serv, core_peer)

    link = CoreLink(
        device_id="core-wan",
        gateway_url="mem://relay",
        peer_token="wrong-token",
        connection_factory=lambda: InMemoryConnection(core_local),
        connect_timeout=2.0,
    )
    await link.start()
    try:
        await _wait(lambda: link._terminal_reason is not None, timeout=4.0)
        assert link._terminal_reason == "peer_token_rejeitado"
        assert link._connected is False
    finally:
        await link.stop()
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        core_serv.close()


@pytest.mark.asyncio
async def test_core_link_pending_mobile_never_reaches_core(monkeypatch, fake_ai):
    from app.remote.link import CoreLink

    hub = _make_hub(monkeypatch)
    core_peer = _gateway_peer(hub)
    mobile_peer = _gateway_peer(hub)
    core_local, core_serv = create_duplex()
    mob_local, mob_serv = create_duplex()
    tasks = [
        await _run_peer(hub, core_serv, core_peer),
        await _run_peer(hub, mob_serv, mobile_peer),
    ]

    link = CoreLink(
        device_id="core-wan",
        gateway_url="mem://relay",
        peer_token=RELAY_TOKEN,
        connection_factory=lambda: InMemoryConnection(core_local),
        connect_timeout=2.0,
    )
    await link.start()
    try:
        await _wait(lambda: link._connected)
        mobile = _MobileClient(mob_local)
        await mobile.start()
        await mobile.send(MessageType.HELLO, payload={"role": "mobile"})
        ack = await mobile.recv()
        assert ack.type == MessageType.HELLO_ACK

        # Sem auth, o móvel é pendente: o relé NUNCA encaminha a mensagem ao Core.
        await mobile.send(MessageType.MESSAGE, payload={"content": "oi"})
        reply = await mobile.recv()
        assert reply.type == MessageType.ERROR
        assert reply.payload.get("error") == "not_authenticated"
        assert link.stats["messages_total"] == 0
        assert link.stats["messages_failed"] == 0
    finally:
        await link.stop()
        for t in tasks:
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        core_serv.close()
        mob_serv.close()


# ---------------------------------------------------------------------------
# Runtime + observabilidade
# ---------------------------------------------------------------------------


async def test_link_runtime_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(settings_holder(), "remote_gateway_enabled", False)
    from app.remote.link_runtime import start_remote_link, stop_remote_link

    assert await start_remote_link() is None
    await stop_remote_link()


async def test_link_runtime_status_sanitized():
    from app.remote.link_runtime import get_remote_link_status, reset_remote_link

    reset_remote_link()
    status = await get_remote_link_status()
    assert status["enabled"] is False
    assert status["running"] is False
    assert status["transport"] == "gateway_wan"
    assert "peer_token" not in str(status)


def settings_holder():
    from app.core.config import settings

    return settings


def test_ops_overview_has_remote_gateway_block(db_session):
    from app.services.ops import build_overview

    overview = build_overview(db_session)
    payload = overview.model_dump()
    assert "remote_gateway" in payload
    block = payload["remote_gateway"]
    assert block["transport"] == "gateway_wan"
    assert "counters" in block


def test_gateway_api_disabled_returns_status_and_503(client):
    status = client.get("/api/remote/gateway")
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["running"] is False
    disconnected = client.post("/api/remote/gateway/disconnect")
    assert disconnected.status_code == 200
    connect = client.post("/api/remote/gateway/connect", json={"url": "wss://relay/ws"})
    assert connect.status_code == 503