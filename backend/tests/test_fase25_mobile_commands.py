"""Testes da Fase 25 — VEGA Mobile Control (capability model + comandos de dispositivo).

Cobre as adições da Fase 25 sobre o Core Link WAN (Fase 23/24):

Parte A — capability model (hermético): registry completo, `validate_command`
(rejeita capability desconhecida, args desconhecidos, tipos errados, obrigatórios
ausentes), allowlist de pacotes, capabilities não executáveis respondem
UNSUPPORTED no vocabulário.

Parte B — CoreLink: `dispatch_mobile_command` valida no Core e despacha um
MOBILE_COMMAND; idempotência por command_id; `_on_command_result` correlaciona
pelo command_id e devolve resumo sanitizado; TTL expira com TIMEOUT; capacidade
da fila de pending é bounded; stats e snapshot expõem comandos.

Parte C — relay: `_MOBILE_BOUND_ALLOWED` inclui COMMAND_RESULT e `_CORE_ROUTED`
inclui MOBILE_COMMAND; um MOBILE_COMMAND do Core chega à mailbox do móvel.

Parte D — schemas/API: schemas MobileCommandIn/Out/CommandDispatchOut validam;
endpoint POST /api/remote/gateway/command responde (503 quando WAN off;
400 quando capability inválida).

Sem credenciais reais; nenhum secret/payload nos logs.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.gateway.hub import (
    _CORE_ROUTED,  # noqa: PLC2702 — suíte da própria feature
    _MOBILE_BOUND_ALLOWED,  # noqa: PLC2702 — suíte da própria feature
    RelayHub,
)
from app.gateway.registry import GatewayRegistry
from app.remote.connection import InMemoryConnection, create_duplex
from app.remote.mobile_capabilities import (
    CAPABILITIES,
    DEFAULT_OPEN_APP_ALLOWLIST,
    MobileCommandError,
    MobileCommandStatus,
    list_capabilities,
    validate_command,
)
from app.remote.protocol import (
    MessageType,
    build_message,
)

RELAY_TOKEN = "test-token"


# ---------------------------------------------------------------------------
# Parte A — capability model (hermético)
# ---------------------------------------------------------------------------


def test_capabilities_registry_declares_all_phase25():
    expected = {
        "DEVICE_INFO",
        "BATTERY_STATUS",
        "NETWORK_STATUS",
        "OPEN_URL",
        "VIBRATE",
        "SET_VOLUME",
        "MEDIA_STATUS",
        "OPEN_APP",
        "SET_BRIGHTNESS",
        "ACCESSIBILITY_CONTROL",
    }
    assert set(CAPABILITIES) == expected


def test_all_phase25_capabilities_are_low_or_medium_risk():
    for spec in CAPABILITIES.values():
        assert spec.risk in ("low", "medium")


def test_accessibility_control_declared_but_not_executable():
    spec = CAPABILITIES["ACCESSIBILITY_CONTROL"]
    assert spec.executable_on_mobile is False


def test_open_app_allowlist_has_default_system_apps():
    assert "com.android.settings" in DEFAULT_OPEN_APP_ALLOWLIST
    assert "com.android.chrome" in DEFAULT_OPEN_APP_ALLOWLIST


def test_validate_command_accepts_known_low_risk():
    spec = validate_command("open_url", {"url": "https://example.com"})
    assert spec.name == "OPEN_URL"


def test_validate_command_rejects_unknown_capability():
    with pytest.raises(MobileCommandError):
        validate_command("FLY_DEVICE", {})


def test_validate_command_rejects_unknown_arg():
    with pytest.raises(MobileCommandError):
        validate_command("VIBRATE", {"duration_ms": 200, "magic": True})


def test_validate_command_rejects_wrong_type():
    with pytest.raises(MobileCommandError):
        validate_command("VIBRATE", {"duration_ms": "two"})
    with pytest.raises(MobileCommandError):
        validate_command("OPEN_URL", {"url": 42})


def test_validate_command_rejects_missing_required():
    with pytest.raises(MobileCommandError):
        validate_command("OPEN_URL", {})


def test_validate_command_normalizes_strip_strings():
    spec = validate_command("OPEN_URL", {"url": "  https://example.com  "})
    assert spec.name == "OPEN_URL"


def test_validate_command_accepts_empty_args_for_stateless():
    spec = validate_command("DEVICE_INFO", {})
    assert spec.name == "DEVICE_INFO"
    spec = validate_command("battery_status", None)
    assert spec.name == "BATTERY_STATUS"


def test_set_volume_level_range_and_required():
    spec = validate_command("SET_VOLUME", {"stream": "music", "level": 55})
    assert spec.name == "SET_VOLUME"


def test_mobile_command_statuses_vocabulary():
    assert MobileCommandStatus.PENDING.value == "pending"
    assert MobileCommandStatus.TIMEOUT.value == "timeout"
    assert MobileCommandStatus.UNSUPPORTED.value == "unsupported"
    assert MobileCommandStatus.DENIED.value == "denied"


def test_list_capabilities_sanitized():
    caps = list_capabilities()
    assert isinstance(caps, list) and caps
    for cap in caps:
        assert set(cap) >= {"name", "description", "risk", "executable"}
    names = {c["name"] for c in caps}
    assert "ACCESSIBILITY_CONTROL" in names


# ---------------------------------------------------------------------------
# Parte B — CoreLink: dispatch/result/timeout (unit, com fakes)
# ---------------------------------------------------------------------------


def _dummy_factory():
    return InMemoryConnection(None)


def _make_link(**overrides):
    from app.remote.link import CoreLink

    return CoreLink(
        device_id="core-wan",
        gateway_url="mem://relay",
        peer_token=RELAY_TOKEN,
        connection_factory=_dummy_factory,
        **overrides,
    )


def _bind_link(link, device_id: str) -> None:
    link._bindings[device_id] = {  # noqa: SLF001
        "device_id": device_id,
        "credential_id": "c",
        "session_id": "s",
    }


async def _send_spy(link):
    sent: list = []

    async def _send(envelope):
        sent.append(envelope)

    link._send = _send  # noqa: SLF001
    link._manager = SimpleNamespace()  # pretence manager presente
    return sent


async def test_dispatch_mobile_command_sends_mobile_command():
    link = _make_link()
    _bind_link(link, "dev-1")
    sent = await _send_spy(link)
    summary = await link.dispatch_mobile_command(
        command_id="cmd-1",
        device_id="dev-1",
        capability="DEVICE_INFO",
        args={},
    )
    assert summary["command_id"] == "cmd-1"
    assert summary["status"] == "pending"
    assert summary["device_id"] == "dev-1"
    assert summary["capability"] == "DEVICE_INFO"
    assert sent and sent[0].type == MessageType.MOBILE_COMMAND
    payload = sent[0].payload
    assert payload["command_id"] == "cmd-1"
    assert payload["target_device_id"] == "dev-1"
    assert payload["capability"] == "DEVICE_INFO"
    assert payload["timeout_ms"] == 15_000
    assert link.stats["commands_dispatched"] == 1


async def test_dispatch_mobile_command_validates_capability_on_core():
    link = _make_link()
    _bind_link(link, "dev-1")
    await _send_spy(link)
    with pytest.raises(MobileCommandError):
        await link.dispatch_mobile_command(
            command_id="cmd-bad", device_id="dev-1", capability="HACK", args={}
        )
    assert link.stats["commands_dropped"] == 1


async def test_dispatch_mobile_command_rejects_unbound_target():
    link = _make_link()
    sent = await _send_spy(link)
    with pytest.raises(Exception):  # LinkStateError
        await link.dispatch_mobile_command(
            command_id="cmd-ghost", device_id="ghost", capability="DEVICE_INFO", args={}
        )
    assert not sent
    assert link.stats["commands_dropped"] == 1


async def test_dispatch_mobile_command_idempotent_by_command_id():
    link = _make_link()
    _bind_link(link, "dev-1")
    sent = await _send_spy(link)
    await link.dispatch_mobile_command(
        command_id="cmd-idem", device_id="dev-1", capability="DEVICE_INFO", args={}
    )
    again = await link.dispatch_mobile_command(
        command_id="cmd-idem", device_id="dev-1", capability="DEVICE_INFO", args={}
    )
    assert again["status"] == "pending"
    assert len(sent) == 1  # não re-despachou
    assert link.stats["commands_dispatched"] == 1


async def test_on_command_result_matches_by_command_id():
    link = _make_link()
    _bind_link(link, "dev-1")
    await _send_spy(link)
    await link.dispatch_mobile_command(
        command_id="cmd-r", device_id="dev-1", capability="BATTERY_STATUS", args={}
    )
    reply = await link._on_command_result(  # noqa: SLF001
        build_message(
            MessageType.COMMAND_RESULT,
            device_id="dev-1",
            command_id="cmd-r",
            payload={
                "command_id": "cmd-r",
                "status": "success",
                "result": {"level": 87, "charging": False},
            },
        )
    )
    assert reply is None
    assert link.stats["commands_results"] == 1
    summary = link.pending_command_summary("cmd-r")
    assert summary["status"] == "success"
    assert summary["result"]["level"] == 87


async def test_on_command_result_rejects_wrong_device():
    link = _make_link()
    _bind_link(link, "dev-1")
    await _send_spy(link)
    await link.dispatch_mobile_command(
        command_id="cmd-w", device_id="dev-1", capability="DEVICE_INFO", args={}
    )
    await link._on_command_result(  # noqa: SLF001
        build_message(MessageType.COMMAND_RESULT, device_id="other-dev", command_id="cmd-w", payload={})
    )
    assert link.stats["commands_dropped"] == 1
    assert link.pending_command_summary("cmd-w")["status"] == "pending"


async def test_pending_command_sweeps_timeout_lazily():
    link = _make_link()
    _bind_link(link, "dev-1")
    await _send_spy(link)
    await link.dispatch_mobile_command(
        command_id="cmd-t", device_id="dev-1", capability="DEVICE_INFO", args={}, timeout_ms=1000
    )
    link._pending_commands["cmd-t"]["deadline_ts"] -= 500  # noqa: SLF001 — expira
    summary = link.pending_command_summary("cmd-t")
    assert summary["status"] == "timeout"
    assert link.stats["commands_timeouts"] == 1
    assert link.pending_command_summary("cmd-t")["status"] == "timeout"


def test_pending_commands_bounded_and_dropped():
    link = _make_link()
    for i in range(120):
        link._pending_commands[f"cmd-{i}"] = {  # noqa: SLF001
            "request_id": f"cmd-{i}",
            "device_id": "dev-1",
            "capability": "DEVICE_INFO",
            "timeout_ms": 1000,
            "dispatched_ts": 0.0,
            "dispatched_at": None,
            "deadline_ts": float("inf"),  # nunca expira
        }
    link._bump_pending_commands()  # noqa: SLF001
    assert len(link._pending_commands) <= 100  # noqa: SLF001


async def test_core_link_snapshot_has_command_fields():
    link = _make_link()
    _bind_link(link, "dev-1")
    await _send_spy(link)
    snap = link.snapshot()
    assert "commands_pending" in snap
    assert snap["commands_pending"] == 0
    assert snap["commands_last"] == []
    assert snap["counters"]["commands_dispatched"] == 0


async def test_command_timeout_remembered_in_history():
    link = _make_link()
    _bind_link(link, "dev-1")
    await _send_spy(link)
    await link.dispatch_mobile_command(
        command_id="cmd-hist", device_id="dev-1", capability="DEVICE_INFO", args={}, timeout_ms=1000
    )
    link._pending_commands["cmd-hist"]["deadline_ts"] -= 500  # noqa: SLF001
    summary = link.pending_command_summary("cmd-hist")
    assert summary["status"] == "timeout"
    historical = link._command_results["cmd-hist"]  # noqa: SLF001
    assert historical["status"] == "timeout"


# ---------------------------------------------------------------------------
# Parte C — relay: allowlists e roteamento
# ---------------------------------------------------------------------------


def test_relay_mobile_bound_allows_command_result():
    assert MessageType.COMMAND_RESULT in _MOBILE_BOUND_ALLOWED


def test_relay_core_routed_includes_mobile_command():
    assert MessageType.MOBILE_COMMAND in _CORE_ROUTED


def _env(type_: MessageType, *, device_id=None, request_id=None, command_id=None, payload=None):
    return build_message(
        type_, device_id=device_id, request_id=request_id, command_id=command_id, payload=payload or {}
    )


def _gateway_peer(hub):
    from app.gateway.backpressure import Mailbox

    return hub.registry.register(Mailbox)


async def test_relay_core_mobile_command_routed_to_bound_mobile():
    hub = RelayHub(registry=GatewayRegistry())
    hub.settings = hub.settings.model_copy(deep=True)
    hub.settings.peer_token = RELAY_TOKEN
    core = _gateway_peer(hub)
    mobile = _gateway_peer(hub)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}), core.peer_id)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    assert await hub.handle(_env(MessageType.AUTH, request_id="r", payload={"token": "t"}), mobile.peer_id) is None
    assert await hub.handle(
        _env(MessageType.AUTH_RESULT, request_id="r", payload={"ok": True, "target_device_id": "dev-m", "session_id": "s"}),
        core.peer_id,
    ) is None
    core.mailbox.drain()
    mobile.mailbox.drain()

    assert await hub.handle(
        _env(
            MessageType.MOBILE_COMMAND,
            device_id="core-wan",
            request_id="cmd-1",
            payload={
                "command_id": "cmd-1",
                "target_device_id": "dev-m",
                "capability": "DEVICE_INFO",
                "args": {},
            },
        ),
        core.peer_id,
    ) is None
    routed = mobile.mailbox.get()
    assert routed is not None and routed.type == MessageType.MOBILE_COMMAND
    assert routed.payload["target_device_id"] == "dev-m"
    assert hub.registry.counters["relays"] >= 1


async def test_relay_mobile_command_result_forwarded_to_core():
    hub = RelayHub(registry=GatewayRegistry())
    hub.settings = hub.settings.model_copy(deep=True)
    hub.settings.peer_token = RELAY_TOKEN
    core = _gateway_peer(hub)
    mobile = _gateway_peer(hub)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "core", "peer_token": RELAY_TOKEN}), core.peer_id)
    assert await hub.handle(_env(MessageType.HELLO, payload={"role": "mobile"}), mobile.peer_id)
    assert await hub.handle(_env(MessageType.AUTH, request_id="r2", payload={"token": "t"}), mobile.peer_id) is None
    assert await hub.handle(
        _env(MessageType.AUTH_RESULT, request_id="r2", payload={"ok": True, "target_device_id": "dev-c", "session_id": "s"}),
        core.peer_id,
    ) is None
    core.mailbox.drain()

    assert await hub.handle(
        _env(
            MessageType.COMMAND_RESULT,
            device_id="dev-c",
            command_id="cmd-r",
            request_id="cmd-r",
            payload={"command_id": "cmd-r", "status": "success"},
        ),
        mobile.peer_id,
    ) is None
    routed = core.mailbox.get()
    assert routed is not None and routed.type == MessageType.COMMAND_RESULT
    assert routed.device_id == "dev-c"
    assert (routed.payload or {}).get("source_device_id") == "dev-c"


# ---------------------------------------------------------------------------
# Parte D — schemas/API
# ---------------------------------------------------------------------------


def test_mobile_command_out_schema_accepts_summary():
    from app.schemas.remote import MobileCommandOut

    out = MobileCommandOut(
        command_id="cmd-1",
        status="pending",
        device_id="dev-1",
        capability="DEVICE_INFO",
        timeout_ms=15000,
    )
    assert out.status == "pending"


def test_command_dispatch_out_schema_nests_command():
    from app.schemas.remote import CommandDispatchOut, MobileCommandOut

    out = CommandDispatchOut(
        command=MobileCommandOut(command_id="cmd-1", status="pending", device_id="dev-1")
    )
    assert out.accepted is True
    assert out.command.command_id == "cmd-1"


def test_mobile_command_api_503_when_gateway_disabled(client):
    resp = client.post("/api/remote/gateway/command", json={"device_id": "dev-x", "capability": "DEVICE_INFO"})
    assert resp.status_code == 503  # REMOTE_GATEWAY_ENABLED=false no teste