"""Testes da Fase 24 — secure WAN gateway & remote connectivity.

Cobre as adições da Fase 24 sobre a Fase 23 (relé) e o Core Link WAN:

Parte A — relay: heartbeat de móvel PENDENTE respondido INLINE (nunca perturba
o Core), heartbeat de móvel ATRELADO encaminhado ao Core, e `heartbeat_ack` do
Core ROTEÁVEL de volta ao móvel (`_CORE_ROUTED`); TTL de AUTH em vôo
(`_sweep_pending_auths`); rate limiting local por peer; wake-event da mailbox.

Parte B — CoreLink: snapshot rico (WanLinkState, latência, fila de proativas,
heartbeats WAN), `_on_heartbeat` devolve ACK roteável com `target_device_id`,
backoff capado, eventos sanitizados, fila off-line de proativas (bounded+TTL).

Parte C — runtime/ops/schemas/API: tunables repassados ao CoreLink, vocabulário
rico no status desligado, schema `RemoteGatewayOut` estendido, endpoint WAN.

Parte D — end-to-end em processo (hub + CoreLink real + banco): heartbeat WAN e
bootstrap por código de pareamento com emissão ÚNICA de token + turno do AI Core.

Sem credenciais reais; nenhum secret/payload nos logs.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.gateway.backpressure import Mailbox
from app.gateway.hub import (
    _CORE_ROUTED,  # noqa: PLC2702 — suíte da própria feature
    RelayHub,
)
from app.gateway.limits import GatewayLimits
from app.gateway.registry import GatewayRegistry
from app.remote.connection import InMemoryConnection, create_duplex
from app.remote.protocol import (
    MessageType,
    RemoteEnvelope,
    build_message,
    decode_message,
    encode_message,
)

RELAY_TOKEN = "test-token"


# ---------------------------------------------------------------------------
# helpers (espelham test_fase23_gateway.py)
# ---------------------------------------------------------------------------


def _make_hub(*, peer_token: str = RELAY_TOKEN, limits: GatewayLimits | None = None) -> RelayHub:
    hub = RelayHub(registry=GatewayRegistry(), limits=limits)
    # get_settings() retorna um singleton global compartilhado — isola uma
    # cópia por hub para os ajustes de TTL/limites não vazarem entre testes.
    hub.settings = hub.settings.model_copy(deep=True)
    hub.settings.peer_token = peer_token
    return hub


def _gateway_peer(hub) -> object:
    return hub.registry.register(Mailbox)


def _env(
    type_: MessageType, *, device_id=None, request_id=None, payload=None
) -> RemoteEnvelope:
    return build_message(type_, device_id=device_id, request_id=request_id, payload=payload or {})


async def _run_peer(hub: RelayHub, pipe, peer) -> asyncio.Task:
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
    def __init__(self, pipe) -> None:
        self.conn = InMemoryConnection(pipe)
        self.received: list[RemoteEnvelope] = []

    async def start(self) -> None:
        await self.conn.connect()

    async def send(self, type_: MessageType, *, payload=None, request_id=None) -> None:
        await self.conn.send(_env(type_, payload=payload, request_id=request_id))

    async def recv(self, timeout: float = 4.0) -> RemoteEnvelope:
        envelope = await asyncio.wait_for(self.conn.receive(), timeout=timeout)
        self.received.append(envelope)
        return envelope

    async def close(self) -> None:
        await self.conn.close()


def _pair_mobile_device(name: str = "Mobile WAN") -> tuple[object, str]:
    from app.db.session import SessionLocal
    from app.remote.pairing import create_pairing, submit_code

    db = SessionLocal()
    try:
        _, code = create_pairing(db)
        device, token = submit_code(db, code=code, device_name=name)
        return device, token
    finally:
        db.close()


async def _wait(predicate, timeout: float = 5.0, interval: float = 0.01) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condicao nao satisfeita no tempo limite")


async def _bind_mobile(hub, device_id: str, request_id: str = "r-bind") -> tuple[object, object]:
    """Registra core + mobile e atrela o mobile ao device via auth_result ok."""
    core = _gateway_peer(hub)
    result = await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    assert result is not None and result.type == MessageType.HELLO_ACK
    mobile = _gateway_peer(hub)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id) is not None
    assert await hub.handle(_env(MessageType.AUTH, request_id=request_id, payload={"token": "abc"}), mobile.peer_id) is None
    assert await hub.handle(
        _env(
            MessageType.AUTH_RESULT,
            request_id=request_id,
            payload={"ok": True, "target_device_id": device_id, "session_id": "sx"},
        ),
        core.peer_id,
    ) is None
    core.mailbox.drain()
    return core, mobile


def _limited_hub(*, capacity: float = 2.0):
    limits = GatewayLimits(
        settings=SimpleNamespace(
            connect_rate_capacity=10.0,
            connect_rate_refill_per_sec=1.0,
            max_peers_per_ip=8,
            peer_message_rate_capacity=capacity,
            peer_message_rate_refill_per_sec=1e-9,
            max_payload_bytes=1000,
        )
    )
    return _make_hub(limits=limits), limits


# ---------------------------------------------------------------------------
# Parte A — relay: heartbeat WAN (fixes P1/P5)
# ---------------------------------------------------------------------------


async def test_relay_pending_mobile_heartbeat_answered_inline():
    hub = _make_hub()
    core = _gateway_peer(hub)
    assert await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    mobile = _gateway_peer(hub)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    reply = await hub.handle(
        _env(MessageType.HEARTBEAT, request_id="hb-pending", payload={"sent_at": 1}),
        mobile.peer_id,
    )
    assert reply is not None and reply.type == MessageType.HEARTBEAT_ACK
    assert reply.payload.get("ok") is True
    assert reply.payload.get("pending") is True
    assert reply.request_id == "hb-pending"


async def test_relay_pending_mobile_heartbeat_never_reaches_core():
    hub = _make_hub()
    core = _gateway_peer(hub)
    assert await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    mobile = _gateway_peer(hub)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    await hub.handle(_env(MessageType.HEARTBEAT), mobile.peer_id)
    assert core.mailbox.get() is None
    assert hub.registry.counters["relays"] == 0


async def test_relay_pending_mobile_cannot_forward_heartbeat_without_hello():
    hub = _make_hub()
    mobile = _gateway_peer(hub)
    reply = await hub.handle(_env(MessageType.HEARTBEAT), mobile.peer_id)
    assert reply is not None and reply.type == MessageType.ERROR
    assert reply.payload.get("error") == "handshake_pending"


async def test_relay_bound_mobile_heartbeat_forwarded_to_core():
    hub = _make_hub()
    core, mobile = await _bind_mobile(hub, "dev-heart")
    relays_before = hub.registry.counters["relays"]
    assert await hub.handle(
        _env(MessageType.HEARTBEAT, request_id="hb-1", payload={"sent_at": 123}),
        mobile.peer_id,
    ) is None
    routed = core.mailbox.get()
    assert routed is not None and routed.type == MessageType.HEARTBEAT
    assert routed.device_id == "dev-heart"
    assert (routed.payload or {}).get("source_device_id") == "dev-heart"
    assert hub.registry.counters["relays"] == relays_before + 1


async def test_relay_core_heartbeat_answered_inline_same_connection():
    hub = _make_hub()
    core = _gateway_peer(hub)
    assert await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    reply = await hub.handle(
        _env(MessageType.HEARTBEAT, device_id="core-wan", request_id="core-hb", payload={"sent_at": 1}),
        core.peer_id,
    )
    assert reply is not None and reply.type == MessageType.HEARTBEAT_ACK
    assert reply.payload.get("ok") is True
    assert reply.request_id == "core-hb"


async def test_relay_core_heartbeat_ack_routed_to_mobile_by_target():
    hub = _make_hub()
    core, mobile = await _bind_mobile(hub, "dev-ack")
    mobile.mailbox.drain()
    assert await hub.handle(
        _env(
            MessageType.HEARTBEAT_ACK,
            device_id="core-wan",
            request_id="hb-ack-1",
            payload={"ok": True, "target_device_id": "dev-ack", "heartbeat_seconds": 30},
        ),
        core.peer_id,
    ) is None
    delivered = mobile.mailbox.get()
    assert delivered is not None and delivered.type == MessageType.HEARTBEAT_ACK
    assert delivered.request_id == "hb-ack-1"
    assert delivered.payload.get("ok") is True


async def test_relay_core_heartbeat_ack_without_target_dropped():
    hub = _make_hub()
    core = _gateway_peer(hub)
    assert await hub.handle(
        _env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}),
        core.peer_id,
    )
    before = hub.registry.counters["relays_dropped"]
    assert await hub.handle(
        _env(MessageType.HEARTBEAT_ACK, payload={"ok": True}),
        core.peer_id,
    ) is None
    assert hub.registry.counters["relays_dropped"] == before + 1


def test_relay_heartbeat_ack_is_core_routed_type():
    assert MessageType.HEARTBEAT_ACK in _CORE_ROUTED


# ---------------------------------------------------------------------------
# Parte A2 — relay: TTL de AUTH em vôo
# ---------------------------------------------------------------------------


async def test_auth_pending_swept_after_ttl():
    hub = _make_hub()
    hub.settings.auth_timeout_seconds = 60.0
    hub._pending_auths["r-old"] = ("peer-x", time.monotonic() - 600)  # noqa: SLF001
    mobile = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    assert "r-old" not in hub._pending_auths  # noqa: SLF001
    assert hub.registry.counters["auth_timeouts"] == 1


async def test_auth_pending_kept_within_ttl():
    hub = _make_hub()
    hub.settings.auth_timeout_seconds = 600.0
    hub._pending_auths["r-new"] = ("peer-x", time.monotonic())  # noqa: SLF001
    mobile = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    assert "r-new" in hub._pending_auths  # noqa: SLF001
    assert hub.registry.counters["auth_timeouts"] == 0


async def test_auth_pending_counted_in_batch():
    hub = _make_hub()
    hub.settings.auth_timeout_seconds = 60.0
    hub._pending_auths["r1"] = ("p1", time.monotonic() - 120)  # noqa: SLF001
    hub._pending_auths["r2"] = ("p2", time.monotonic() - 120)  # noqa: SLF001
    hub._pending_auths["r3"] = ("p3", time.monotonic())  # noqa: SLF001
    mobile = _gateway_peer(hub)
    await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    assert hub.registry.counters["auth_timeouts"] == 2
    assert len(hub._pending_auths) == 1  # noqa: SLF001


async def test_auth_pending_dropped_on_mobile_close():
    hub = _make_hub()
    core = _gateway_peer(hub)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}), core.peer_id)
    mobile = _gateway_peer(hub)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    await hub.handle(_env(MessageType.AUTH, request_id="r-close", payload={"token": "x"}), mobile.peer_id)
    assert "r-close" in hub._pending_auths  # noqa: SLF001
    await hub.handle(_env(MessageType.CLOSE), mobile.peer_id)
    assert "r-close" not in hub._pending_auths  # noqa: SLF001
    assert hub.registry.peer(mobile.peer_id) is None


async def test_auth_pending_parallel_auths_survive_distinct_requests():
    hub = _make_hub()
    core = _gateway_peer(hub)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}), core.peer_id)
    mobile = _gateway_peer(hub)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    await hub.handle(_env(MessageType.AUTH, request_id="ra", payload={}), mobile.peer_id)
    await hub.handle(_env(MessageType.AUTH, request_id="rb", payload={}), mobile.peer_id)
    assert set(hub._pending_auths) == {"ra", "rb"}  # noqa: SLF001


# ---------------------------------------------------------------------------
# Parte A3 — relay: rate limiting local por peer
# ---------------------------------------------------------------------------


async def test_relay_bound_message_beyond_capacity_rate_limited():
    hub, limits = _limited_hub(capacity=2.0)
    core, mobile = await _bind_mobile(hub, "dev-rl")
    first = await hub.handle(_env(MessageType.MESSAGE, payload={"content": "a"}), mobile.peer_id)
    second = await hub.handle(_env(MessageType.MESSAGE, payload={"content": "b"}), mobile.peer_id)
    third = await hub.handle(_env(MessageType.MESSAGE, payload={"content": "c"}), mobile.peer_id)
    assert first is None and second is None
    assert third is not None and third.type == MessageType.ERROR
    assert third.payload.get("error") == "rate_limited"
    assert hub.registry.counters["peer_messages_rate_limited"] == 1
    assert limits.peer_messages_rate_limited == 1
    core.mailbox.drain()


async def test_relay_heartbeat_not_rate_limited():
    hub, _limits = _limited_hub(capacity=1.0)
    core, mobile = await _bind_mobile(hub, "dev-notrl")
    await hub.handle(_env(MessageType.HEARTBEAT), mobile.peer_id)
    await hub.handle(_env(MessageType.HEARTBEAT), mobile.peer_id)
    await hub.handle(_env(MessageType.HEARTBEAT), mobile.peer_id)
    assert hub.registry.counters["peer_messages_rate_limited"] == 0


async def test_relay_limits_reset_releases_peer_bucket():
    hub, limits = _limited_hub(capacity=1.0)
    core, mobile = await _bind_mobile(hub, "dev-reset")
    assert await hub.handle(_env(MessageType.MESSAGE, payload={"content": "x"}), mobile.peer_id) is None
    denied = await hub.handle(_env(MessageType.MESSAGE, payload={"content": "y"}), mobile.peer_id)
    assert denied is not None and denied.payload.get("error") == "rate_limited"
    limits.reset()
    again = await hub.handle(_env(MessageType.MESSAGE, payload={"content": "z"}), mobile.peer_id)
    assert again is None
    core.mailbox.drain()


async def test_relay_bound_disallowed_type_rejected_and_counted():
    hub = _make_hub()
    core, mobile = await _bind_mobile(hub, "dev-forb")
    before = hub.registry.counters["peer_messages_rejected"]
    reply = await hub.handle(_env(MessageType.COMMAND, payload={"content": "x"}), mobile.peer_id)
    assert reply is not None and reply.type == MessageType.ERROR
    assert reply.payload.get("error") == "forbidden"
    assert hub.registry.counters["peer_messages_rejected"] == before + 1


async def test_relay_bound_message_rate_limited_counter_not_typed_as_rejected():
    hub, _limits = _limited_hub(capacity=1.0)
    core, mobile = await _bind_mobile(hub, "dev-rlc")
    await hub.handle(_env(MessageType.MESSAGE, payload={"content": "a"}), mobile.peer_id)
    await hub.handle(_env(MessageType.MESSAGE, payload={"content": "b"}), mobile.peer_id)
    assert hub.registry.counters["peer_messages_rate_limited"] == 1
    assert hub.registry.counters["peer_messages_rejected"] == 0
    core.mailbox.drain()


async def test_relay_computer_task_rate_limited_separately_after_refill():
    hub, limits = _limited_hub(capacity=1.0)
    limits._peer_buckets.clear()
    core, mobile = await _bind_mobile(hub, "dev-ct")
    assert await hub.handle(_env(MessageType.COMPUTER_TASK, payload={"content": "a"}), mobile.peer_id) is None
    denied = await hub.handle(_env(MessageType.COMPUTER_TASK, payload={"content": "b"}), mobile.peer_id)
    assert denied is not None and denied.payload.get("error") == "rate_limited"
    core.mailbox.drain()


# ---------------------------------------------------------------------------
# Parte A4 — mailbox/backpressure (wake event)
# ---------------------------------------------------------------------------


async def test_mailbox_wake_event_set_on_put():
    mailbox = Mailbox()
    wake = mailbox.wake_event()
    assert not wake.is_set()
    assert mailbox.put("item") is True
    assert wake.is_set()


async def test_mailbox_wake_event_set_on_close():
    mailbox = Mailbox()
    wake = mailbox.wake_event()
    mailbox.close()
    assert wake.is_set()


async def test_mailbox_put_after_close_false():
    mailbox = Mailbox()
    mailbox.close()
    assert mailbox.put("item") is False


async def test_mailbox_full_put_dropped_and_counted():
    mailbox = Mailbox(maxsize=1)
    assert mailbox.put("a") is True
    assert mailbox.put("b") is False
    assert mailbox.dropped == 1
    assert mailbox.get() == "a"


async def test_mailbox_wake_not_created_until_requested():
    mailbox = Mailbox()
    assert mailbox._wake is None  # noqa: SLF001
    assert mailbox.wake_event() is not None


# ---------------------------------------------------------------------------
# Parte A5 — limites de conexão/payload
# ---------------------------------------------------------------------------


def test_limits_connect_capped_per_ip():
    limits = GatewayLimits(
        settings=SimpleNamespace(
            connect_rate_capacity=10.0,
            connect_rate_refill_per_sec=1.0,
            max_peers_per_ip=2,
            max_payload_bytes=1000,
            peer_message_rate_capacity=20.0,
            peer_message_rate_refill_per_sec=1.0,
        )
    )
    assert limits.allow_connect("1.2.3.4") is True
    assert limits.allow_connect("1.2.3.4") is True
    assert limits.allow_connect("1.2.3.4") is False
    assert limits.rejected_connects == 1


def test_limits_release_connect_frees_per_ip_slot():
    limits = GatewayLimits(
        settings=SimpleNamespace(
            connect_rate_capacity=10.0,
            connect_rate_refill_per_sec=1.0,
            max_peers_per_ip=1,
            max_payload_bytes=1000,
            peer_message_rate_capacity=20.0,
            peer_message_rate_refill_per_sec=1.0,
        )
    )
    assert limits.allow_connect("9.9.9.9") is True
    limits.release_connect("9.9.9.9")
    assert limits.allow_connect("9.9.9.9") is True


def test_limits_oversized_payload_rejected_and_counted():
    limits = GatewayLimits(
        settings=SimpleNamespace(
            connect_rate_capacity=10.0,
            connect_rate_refill_per_sec=1.0,
            max_peers_per_ip=8,
            max_payload_bytes=10,
            peer_message_rate_capacity=20.0,
            peer_message_rate_refill_per_sec=1.0,
        )
    )
    assert limits.allow_payload(9) is True
    assert limits.allow_payload(11) is False
    assert limits.oversized_envelopes == 1


def test_limiter_allow_consumes_tokens_and_recovers():
    from app.remote.ratelimit import RateLimiter

    limiter = RateLimiter(capacity=2.0, refill_per_second=1e-9)
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False


# ---------------------------------------------------------------------------
# Parte A6 — config do gateway (Fase 24 defaults)
# ---------------------------------------------------------------------------


def test_gateway_settings_auth_timeout_default():
    from app.gateway.config import get_settings

    settings = get_settings()
    assert settings.auth_timeout_seconds == 60.0


def test_gateway_settings_peer_message_rate_defaults():
    from app.gateway.config import get_settings

    settings = get_settings()
    assert settings.peer_message_rate_capacity == 20.0
    assert settings.peer_message_rate_refill_per_sec == 1.0


# ---------------------------------------------------------------------------
# Parte B — CoreLink (Fase 24)
# ---------------------------------------------------------------------------


def _dummy_factory():
    from app.remote.connection import InMemoryConnection

    return InMemoryConnection(None)


def _make_link(**overrides) -> object:
    from app.remote.link import CoreLink

    return CoreLink(
        device_id="core-wan",
        gateway_url="mem://relay",
        peer_token=RELAY_TOKEN,
        connection_factory=_dummy_factory,
        **overrides,
    )


def test_core_link_snapshot_initial_vocabulary():
    link = _make_link()
    snap = link.snapshot()
    assert snap["connection_state"] == "disconnected"
    assert snap["connection"] == "disconnected"
    assert snap["authenticated"] is False
    assert snap["healthy"] is False
    assert snap["last_error_code"] is None
    assert snap["latency"] == {"relay_rtt_ms": None, "message_latency_ms": None}
    assert snap["mobile_heartbeats"] == {}
    assert snap["queued_proactive"] == 0
    assert snap["counters"] == link.stats


def test_wan_link_state_enums():
    from app.remote.link import WanLinkState

    assert {s.value for s in WanLinkState} == {
        "disabled",
        "disconnected",
        "connecting",
        "connected",
        "degraded",
        "reconnecting",
        "auth_failed",
        "revoked",
    }


def test_link_state_error_is_value_error():
    from app.remote.link import LinkStateError

    assert issubclass(LinkStateError, ValueError)


def test_core_link_backoff_capped():
    link = _make_link(min_reconnect_delay=0.5, max_reconnect_delay=30.0)
    values = [link._backoff(max(0.5, v)) for v in [31, 500, 0.5, 8, 1000]]
    for value in values:
        assert 0.0 <= value <= 30.0


def test_core_link_set_state_publishes_once_per_transition():
    from app.remote.link import CoreLink, WanLinkState

    link = _make_link()
    events: list[tuple] = []
    link._publish_gateway_event = lambda *a, **k: events.append(a)  # noqa: SLF001
    link._set_state(WanLinkState.CONNECTING, reason="start")  # noqa: SLF001
    link._set_state(WanLinkState.CONNECTING, reason="dup")  # idempotente — sem evento
    assert len(events) == 1
    assert events[0][0] == "remote.gateway.connecting"


def test_core_link_heartbeat_ack_records_rtt():
    link = _make_link()
    link._heartbeat_sent_at = time.monotonic() - 0.05  # noqa: SLF001
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            link._on_heartbeat_ack(_env(MessageType.HEARTBEAT_ACK, payload={"ok": True}))
        )
    finally:
        loop.close()
    assert link._last_heartbeat is not None  # noqa: SLF001
    assert link._rtt_history  # noqa: SLF001
    assert link._heartbeat_sent_at is None  # noqa: SLF001


def test_core_link_heartbeat_sent_hook_marks_instant():
    link = _make_link()
    link._on_heartbeat_sent()
    assert link._heartbeat_sent_at is not None  # noqa: SLF001


# -- heartbeat WAN de móvel atrelado (unit, com fakes) -----------------------


class _FakeSessionCtx:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _bind_link(link, device_id: str) -> None:
    link._bindings[device_id] = {  # noqa: SLF001
        "device_id": device_id,
        "credential_id": "c",
        "session_id": "s",
    }


@pytest.mark.asyncio
async def test_core_link_on_heartbeat_bound_returns_target_ack(monkeypatch):
    from app.remote.link import CoreLink

    link = _make_link()
    _bind_link(link, "dev-heart")
    monkeypatch.setattr("app.remote.link.SessionLocal", lambda: _FakeSessionCtx())
    monkeypatch.setattr("app.remote.devices.heartbeat", lambda *_a, **_k: None)
    device = SimpleNamespace(id="dev-heart", name="M", device_type="mobile", status="active")
    link._load_bound = lambda db, bound: (device, SimpleNamespace(id="c", is_active=True), SimpleNamespace(id="s"))

    reply = await link._on_heartbeat(
        _env(MessageType.HEARTBEAT, device_id="dev-heart", request_id="hb-1", payload={"sent_at": 55})
    )
    assert reply is not None and reply.type == MessageType.HEARTBEAT_ACK
    assert (reply.payload or {}).get("target_device_id") == "dev-heart"
    assert (reply.payload or {}).get("echo_sent_at") == 55
    assert (reply.payload or {}).get("ok") is True
    assert link.stats["heartbeats"] == 1
    assert "dev-heart" in link._last_mobile_heartbeat  # noqa: SLF001


@pytest.mark.asyncio
async def test_core_link_on_heartbeat_unbound_returns_none():
    link = _make_link()
    reply = await link._on_heartbeat(_env(MessageType.HEARTBEAT, device_id="ghost"))
    assert reply is None
    assert link.stats["heartbeats"] == 0


@pytest.mark.asyncio
async def test_core_link_on_heartbeat_auth_failure_returns_none(monkeypatch):
    link = _make_link()
    _bind_link(link, "dev-bad")

    def _boom_db():
        raise RuntimeError("db falhou")

    monkeypatch.setattr("app.remote.link.SessionLocal", _boom_db)
    reply = await link._on_heartbeat(_env(MessageType.HEARTBEAT, device_id="dev-bad"))
    assert reply is None
    assert link.stats["heartbeats"] == 0


@pytest.mark.asyncio
async def test_core_link_on_heartbeat_without_device_id_returns_none():
    link = _make_link()
    reply = await link._on_heartbeat(_env(MessageType.HEARTBEAT))
    assert reply is None


# -- Fase 24.1 — diagnóstico honesto: revogado/sessão expirada no heartbeat -----


class _StubDb:
    def __init__(self, device, credential):
        self.device = device
        self.credential = credential

    def get(self, model, _pk):
        if model.__name__ == "Device":
            return self.device
        return self.credential


class _StubSession:
    def __init__(self, device, credential):
        self.db = _StubDb(device, credential)

    def __enter__(self):
        return self.db

    def __exit__(self, *_args):
        return False


def _heartbeat_reject_fixture(monkeypatch, link, device_active: bool, credential_active: bool):
    from app.remote.link import LinkStateError

    _bind_link(link, "dev-rev")

    def _load_bound_reject(db, bound):
        raise LinkStateError("device não autorizado")

    link._load_bound = _load_bound_reject
    device = SimpleNamespace(id="dev-rev", name="M", device_type="mobile", status="active")
    credential = SimpleNamespace(id="c")
    device.is_active = device_active
    credential.is_active = credential_active
    monkeypatch.setattr("app.remote.link.SessionLocal", lambda: _StubSession(device, credential))


@pytest.mark.asyncio
async def test_core_link_on_heartbeat_revoked_device_returns_revoked_ack(monkeypatch):
    link = _make_link()
    _heartbeat_reject_fixture(monkeypatch, link, device_active=False, credential_active=True)
    reply = await link._on_heartbeat(
        _env(MessageType.HEARTBEAT, device_id="dev-rev", request_id="hb-rev")
    )
    assert reply is not None and reply.type == MessageType.HEARTBEAT_ACK
    assert reply.payload.get("ok") is False
    assert reply.payload.get("error") == "revoked"
    assert reply.payload.get("target_device_id") == "dev-rev"
    assert link.stats["heartbeats"] == 0
    assert "dev-rev" not in link._last_mobile_heartbeat  # noqa: SLF001


@pytest.mark.asyncio
async def test_core_link_on_heartbeat_expired_session_returns_not_authenticated_ack(monkeypatch):
    link = _make_link()
    _heartbeat_reject_fixture(monkeypatch, link, device_active=True, credential_active=True)
    reply = await link._on_heartbeat(
        _env(MessageType.HEARTBEAT, device_id="dev-rev", request_id="hb-exp")
    )
    assert reply is not None and reply.type == MessageType.HEARTBEAT_ACK
    assert reply.payload.get("ok") is False
    assert reply.payload.get("error") == "not_authenticated"
    assert reply.payload.get("target_device_id") == "dev-rev"
    assert link.stats["heartbeats"] == 0


@pytest.mark.asyncio
async def test_core_link_on_heartbeat_db_error_still_returns_none(monkeypatch):
    """Falha interna continua do VERDADEIRO None (não finge revogação)."""

    def _boom_db():
        raise RuntimeError("db falhou")

    link = _make_link()
    _bind_link(link, "dev-bad")
    monkeypatch.setattr("app.remote.link.SessionLocal", _boom_db)
    reply = await link._on_heartbeat(_env(MessageType.HEARTBEAT, device_id="dev-bad"))
    assert reply is None
    assert link.stats["heartbeats"] == 0


# -- fila de proativas (bounded + TTL + dedup) ------------------------------


def test_proactive_enqueue_bounded_and_dedup():
    link = _make_link()
    entry = {"type": "proactive.foo", "event_id": "e1", "data": {"message_id": "m1"}}
    link._enqueue_proactive("dev-p", entry)  # noqa: SLF001
    link._enqueue_proactive("dev-p", entry)  # dedup — ignorado
    assert len(link._proactive_queue["dev-p"]) == 1  # noqa: SLF001
    assert link.stats["proactive_queued"] == 1


def test_proactive_enqueue_caps_per_device():
    link = _make_link()
    for i in range(55):
        link._enqueue_proactive(  # noqa: SLF001
            "dev-cap", {"type": "proactive.x", "event_id": f"e{i}", "data": {"message_id": f"m{i}"}}
        )
    assert len(link._proactive_queue["dev-cap"]) == 50  # noqa: SLF001


@pytest.mark.asyncio
async def test_proactive_flush_applies_ttl_expiry():
    from app.remote.link import CoreLink

    link = _make_link(queue_ttl=0.001)
    link._proactive_queue["dev-ttl"] = [  # noqa: SLF001
        (time.monotonic() - 5.0, {"type": "proactive.x", "event_id": "e", "data": {}})
    ]
    sent: list[str] = []
    link._send_proactive = lambda device_id, entry: sent.append(device_id)  # noqa: SLF001
    await link._flush_proactive("dev-ttl")
    assert sent == []
    assert link.stats["proactive_expired"] == 1


@pytest.mark.asyncio
async def test_proactive_flush_delivers_within_ttl():
    link = _make_link(queue_ttl=60.0)
    link._proactive_queue["dev-fresh"] = [  # noqa: SLF001
        (time.monotonic(), {"type": "proactive.x", "event_id": "e", "data": {"message_id": "m1"}})
    ]
    sent: list[str] = []

    async def _send_proactive(device_id, entry):
        sent.append(device_id)

    link._send_proactive = _send_proactive  # noqa: SLF001
    await link._flush_proactive("dev-fresh")  # noqa: SLF001
    assert sent == ["dev-fresh"]
    assert link.stats["proactive_delivered"] == 0  # pros que não passam por _flush


def test_proactive_remember_mobile_bounded():
    link = _make_link()
    for i in range(60):
        link._remember_mobile(f"dev-{i}")  # noqa: SLF001
    assert len(link._known_mobiles) == 50  # noqa: SLF001


def test_sweep_stale_heartbeats_removes_and_emits():
    from datetime import datetime, timedelta, timezone

    link = _make_link(heartbeat_interval=10.0)
    link._last_mobile_heartbeat["dev-stale"] = datetime.now(timezone.utc) - timedelta(seconds=500)  # noqa: SLF001
    link._last_mobile_heartbeat["dev-fresh"] = datetime.now(timezone.utc)  # noqa: SLF001
    events: list[tuple] = []
    link._publish_gateway_event = lambda *a, **k: events.append(a)  # noqa: SLF001
    link._sweep_stale_heartbeats()  # noqa: SLF001
    assert "dev-stale" not in link._last_mobile_heartbeat  # noqa: SLF001
    assert "dev-fresh" in link._last_mobile_heartbeat  # noqa: SLF001
    assert any(e[0] == "remote.gateway.device_disconnected" for e in events)


# ---------------------------------------------------------------------------
# Parte C — runtime/ops/schemas/API
# ---------------------------------------------------------------------------


async def _run_remote_ping(settings_holder):
    pass


async def test_link_runtime_start_passes_tunables_to_corelink(monkeypatch):
    from app.core.config import settings
    from app.remote import link_runtime

    captured: dict = {}

    class _FakeLink:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def start(self):
            self.started = True

        async def stop(self):
            pass

    monkeypatch.setattr(link_runtime, "CoreLink", _FakeLink)
    monkeypatch.setattr("app.remote.gateway_prefs.GatewayPrefs.save", lambda self, url: None)
    monkeypatch.setattr(settings, "remote_gateway_enabled", True)
    monkeypatch.setattr(settings, "remote_gateway_peer_token", "tok")
    monkeypatch.setattr(settings, "remote_device_id", "core-t")
    monkeypatch.setattr(settings, "remote_gateway_connect_timeout_seconds", 7.5)

    link_runtime.reset_remote_link()
    try:
        result = await link_runtime.start_remote_link(url="wss://relay/ws")
        assert result is not None
        assert captured["heartbeat_interval"] == settings.remote_gateway_heartbeat_seconds
        assert captured["connect_timeout"] == settings.remote_gateway_connect_timeout_seconds
        assert captured["max_reconnect_delay"] == settings.remote_gateway_max_backoff_seconds
        assert captured["reconnect_enabled"] is settings.remote_gateway_reconnect_enabled
        assert captured["message_timeout"] == settings.remote_gateway_message_timeout_seconds
        assert captured["queue_ttl"] == settings.remote_gateway_queue_ttl_seconds
        factory = captured["connection_factory"]
        assert callable(factory)
        conn = factory()
        assert conn.timeout == 7.5
        assert conn.url == "wss://relay/ws"
    finally:
        await link_runtime.stop_remote_link()
    assert link_runtime.get_remote_link() is None


async def test_link_runtime_status_rich_vocabulary_when_disabled(monkeypatch):
    from app.remote import link_runtime

    link_runtime.reset_remote_link()
    monkeypatch.setattr(link_runtime, "_link", None)
    status = await link_runtime.get_remote_link_status()
    assert status["connection_state"] == "disabled"
    assert status["connection"] == "disconnected"
    assert status["last_error_code"] is None
    assert status["mobile_heartbeats"] == {}
    assert status["queued_proactive"] == 0
    assert status["latency"] == {"relay_rtt_ms": None, "message_latency_ms": None}
    assert status["counters"] == {}
    assert "peer_token" not in str(status)


def test_ops_remote_gateway_stats_else_branch(monkeypatch, db_session):
    from app.services.ops import _remote_gateway_stats

    def _no_link():
        return None

    monkeypatch.setattr("app.remote.link_runtime.get_remote_link", _no_link)
    stats = _remote_gateway_stats()
    assert stats["connection_state"] == "disabled"
    assert stats["last_error_code"] is None
    assert stats["mobile_heartbeats"] == {}
    assert stats["latency"] == {"relay_rtt_ms": None, "message_latency_ms": None}
    assert stats["queued_proactive"] == 0
    assert stats["counters"] == {}


def test_ops_remote_gateway_stats_merges_snapshot(monkeypatch, db_session):
    from app.remote.link import WanLinkState
    from app.services.ops import _remote_gateway_stats

    class _FakeLink:
        def snapshot(self):
            return {
                "connection_state": WanLinkState.CONNECTED.value,
                "connection": WanLinkState.CONNECTED.value,
                "healthy": True,
                "device_id": "core-t",
                "last_error_code": None,
                "mobile_heartbeats": {"dev-1": "2026-01-01T00:00:00+00:00"},
                "queued_proactive": 2,
                "counters": {"heartbeats": 5},
                "latency": {"relay_rtt_ms": 12.0, "message_latency_ms": None},
            }

    monkeypatch.setattr("app.remote.link_runtime.get_remote_link", lambda: _FakeLink())
    stats = _remote_gateway_stats()
    assert stats["connection_state"] == "connected"
    assert stats["queued_proactive"] == 2
    assert stats["mobile_heartbeats"]["dev-1"]
    assert stats["counters"]["heartbeats"] == 5


def test_remote_gateway_out_schema_accepts_rich_payload():
    from app.schemas.remote import RemoteGatewayOut

    payload = {
        "enabled": True,
        "configured": True,
        "running": True,
        "transport": "gateway_wan",
        "url": "wss://relay/ws",
        "connection": "connected",
        "connection_state": "connected",
        "healthy": True,
        "device_id": "core-wan",
        "last_state_change": "2026-01-01T00:00:00Z",
        "last_error_code": None,
        "mobile_heartbeats": {"dev-1": "2026-01-01T00:00:00Z"},
        "latency": {"relay_rtt_ms": 12.0, "message_latency_ms": None},
        "queued_proactive": 3,
        "counters": {"heartbeats": 9},
    }
    out = RemoteGatewayOut(**payload)
    dumped = out.model_dump()
    assert dumped["connection_state"] == "connected"
    assert dumped["mobile_heartbeats"]["dev-1"]
    assert dumped["latency"]["relay_rtt_ms"] == 12.0
    assert dumped["queued_proactive"] == 3


def test_gateway_api_status_exposes_rich_fields(client):
    status = client.get("/api/remote/gateway")
    body = status.json()
    assert body["connection_state"] == "disabled"
    assert body["last_error_code"] is None
    assert body["mobile_heartbeats"] == {}
    assert body["queued_proactive"] == 0
    assert body["counters"] == {}


def test_remote_status_gateway_block_absent_when_disabled(client):
    status = client.get("/api/remote/status")
    body = status.json()
    assert body["gateway"] is None


# ---------------------------------------------------------------------------
# Parte D — end-to-end em processo (hub + CoreLink real + banco)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_bound_mobile_heartbeat_roundtrip_via_relay(monkeypatch, fake_ai):
    """móvel → HEARTBEAT → relé → Core → HEARTBEAT_ACK → relé → móvel."""
    from app.remote.link import CoreLink

    monkeypatch.setattr("app.remote.link.get_default_provider", lambda: fake_ai)
    hub = _make_hub()
    core_peer = _gateway_peer(hub)
    mobile_peer = _gateway_peer(hub)
    core_local, core_serv = create_duplex()
    mob_local, mob_serv = create_duplex()
    tasks = [
        await _run_peer(hub, core_serv, core_peer),
        await _run_peer(hub, mob_serv, mobile_peer),
    ]

    device, token = _pair_mobile_device()

    link = CoreLink(
        device_id="core-wan",
        gateway_url="mem://relay",
        peer_token=RELAY_TOKEN,
        connection_factory=lambda: InMemoryConnection(core_local),
        connect_timeout=2.0,
    )
    await link.start()
    try:
        await _wait(lambda: link._connected)  # noqa: SLF001
        mobile = _MobileClient(mob_local)
        await mobile.start()
        await mobile.send(MessageType.HELLO, payload={"role": "mobile"})
        ack = await mobile.recv()
        assert ack.type == MessageType.HELLO_ACK

        await mobile.send(MessageType.AUTH, payload={"token": token}, request_id="auth-1")
        auth_result = await mobile.recv()
        assert auth_result.type == MessageType.AUTH_RESULT
        assert auth_result.payload.get("ok") is True
        assert auth_result.payload.get("device_id") == device.id
        assert "heartbeat_seconds" in auth_result.payload
        assert "reconnect_enabled" in auth_result.payload
        assert "message_timeout" in auth_result.payload
        assert "queue_ttl" in auth_result.payload

        # Coração da correção: mobile atrelado HEARTBEAT → Core responde ACK
        # roteável de volta (target_device_id), nunca mais `unsupported_type`.
        await mobile.send(
            MessageType.HEARTBEAT, request_id="hb-1", payload={"sent_at": 1234}
        )
        hb_ack = await mobile.recv()
        assert hb_ack.type == MessageType.HEARTBEAT_ACK
        assert hb_ack.request_id == "hb-1"
        assert hb_ack.payload.get("ok") is True
        assert hb_ack.payload.get("target_device_id") == device.id
        assert hb_ack.payload.get("echo_sent_at") == 1234
        assert link.stats["heartbeats"] >= 1
        await _wait(lambda: device.id in link._last_mobile_heartbeat)  # noqa: SLF001
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
async def test_e2e_pairing_bootstrap_single_token_and_message(monkeypatch, fake_ai):
    """Bootstrap por código de pareamento: token emitido UMA vez + turno do AI Core."""
    from app.db.session import SessionLocal
    from app.remote.link import CoreLink
    from app.remote.pairing import create_pairing

    monkeypatch.setattr("app.remote.link.get_default_provider", lambda: fake_ai)
    hub = _make_hub()
    core_peer = _gateway_peer(hub)
    mobile_peer = _gateway_peer(hub)
    core_local, core_serv = create_duplex()
    mob_local, mob_serv = create_duplex()
    tasks = [
        await _run_peer(hub, core_serv, core_peer),
        await _run_peer(hub, mob_serv, mobile_peer),
    ]

    db = SessionLocal()
    try:
        _, code = create_pairing(db)
    finally:
        db.close()

    link = CoreLink(
        device_id="core-wan",
        gateway_url="mem://relay",
        peer_token=RELAY_TOKEN,
        connection_factory=lambda: InMemoryConnection(core_local),
        connect_timeout=2.0,
    )
    await link.start()
    try:
        await _wait(lambda: link._connected)  # noqa: SLF001
        mobile = _MobileClient(mob_local)
        await mobile.start()
        await mobile.send(MessageType.HELLO, payload={"role": "mobile"})
        ack = await mobile.recv()
        assert ack.type == MessageType.HELLO_ACK

        # Sem token prévio: AUTH vetoriza para o bootstrap por código (Fase 24).
        await mobile.send(
            MessageType.AUTH,
            request_id="auth-pair",
            payload={
                "pairing_code": code,
                "device_name": "WAN Mobile",
                "device_type": "mobile",
                "platform": "android",
                "client_version": "0.0.0",
                "capabilities": ["chat", "tts", "notifications"],
                "transport_meta": {"app": "mobile", "transport": "wss"},
            },
        )
        auth_result = await mobile.recv()
        assert auth_result.type == MessageType.AUTH_RESULT
        assert auth_result.payload.get("ok") is True
        assert auth_result.payload.get("token")  # única emissão do token
        device_id = auth_result.payload.get("device_id")
        assert device_id
        assert auth_result.payload.get("conversation_id")
        assert link.stats["pairings"] >= 1
        await _wait(lambda: hub.registry.mobile(device_id) is not None)

        # Conversação no MESMO AI Core (sem segundo Core no cliente).
        await mobile.send(
            MessageType.MESSAGE,
            payload={"content": "oi wan", "stream": False, "tools": False},
            request_id="msg-1",
        )
        result = None
        for _ in range(10):
            frame = await mobile.recv()
            if frame.type == MessageType.MESSAGE_RESULT:
                result = frame
                break
        assert result is not None
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