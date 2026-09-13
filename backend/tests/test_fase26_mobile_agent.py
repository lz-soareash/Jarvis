"""Testes da Fase 26 — VEGA Mobile Agent Orchestration + Mobile UX (backend).

Cobre as adições da Fase 26 sobre a Fase 25 (capabilities/especificação):

Parte A — Tool Engine: os 9 `mobile_*` estão registrados no registry com
permissões corretas (leituras LEVEL_0; ajustes LEVEL_1) e risco LOW;
`ACCESSIBILITY_CONTROL` NÃO vira tool (permanece apenas declarada, Fase 29).

Parte B — canal LAN (`mobile_inbox`, hermético): enqueue idempotente por
`command_id`, `pending_for` devolve o que o móvel deve executar, `submit_result`
resolve/descarta desconhecido/rejeita status fora do vocabulário, tetos bounded.

Parte C — `mobile_agent` (orquestrador): seleção de dispositivo (hint por nome,
único entregável, vínculo de sessão, ambiguidade, offline, sem dispositivos),
transporte WAN (CoreLink fake) e LAN (inbox), idempotência, TIMEOUT, capability
inválida/unsupported e resultado estruturado sanitizado.

Parte D — Intent determinística: padrões `mobile_*` exigem menção ao dispositivo
e não roubam intenções de PC.

Sem credenciais reais; nenhum secret nos logs; testes herméticos (sem rede).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.ai.intent import detect_intent
from app.models.remote import Device
from app.models.session import utcnow
from app.remote import mobile_agent, mobile_inbox
from app.tools.registry import get_tool_registry

MOBILE_TOOLS = {
    "mobile_device_info": 0,
    "mobile_battery_status": 0,
    "mobile_network_status": 0,
    "mobile_media_status": 0,
    "mobile_open_url": 1,
    "mobile_vibrate": 1,
    "mobile_set_volume": 1,
    "mobile_set_brightness": 1,
    "mobile_open_app": 1,
}


def _add_device(db, **overrides) -> Device:
    defaults: dict = {
        "id": "dev-galaxy",
        "name": "Galaxy A15",
        "device_type": "mobile",
        "status": "active",
        "platform": "android",
        "last_seen_at": utcnow(),
    }
    defaults.update(overrides)
    device = Device(**defaults)
    db.add(device)
    db.commit()
    return device


# ---------------------------------------------------------------------------
# Parte A — Tool Engine
# ---------------------------------------------------------------------------


def test_mobile_tools_registered_with_permission_levels():
    registry = get_tool_registry()
    names = {t.name for t in registry.all()}
    assert set(MOBILE_TOOLS) <= names
    for name, level in MOBILE_TOOLS.items():
        tool = registry.get(name)
        assert tool is not None
        assert tool.permission_level.value == level
        assert tool.risk.value == "low"


def test_accessibility_control_not_exposed_as_tool():
    names = {t.name for t in get_tool_registry().all()}
    assert "accessibility_control" not in names
    assert "mobile_accessibility_control" not in names


def test_mobile_open_url_declared_url_required():
    tool = get_tool_registry().get("mobile_open_url")
    assert tool.parameters["required"] == ["url"]
    assert tool.parameters["properties"]["url"]["type"] == "string"


# ---------------------------------------------------------------------------
# Parte B — canal LAN (mobile_inbox)
# ---------------------------------------------------------------------------


def test_inbox_enqueue_pending_and_idempotent():
    inbox = mobile_inbox.get_mobile_inbox()
    first = inbox.enqueue(
        device_id="dev-galaxy", capability="BATTERY_STATUS", args={}, timeout_ms=15000
    )
    assert first["status"] == "pending"
    again = inbox.enqueue(
        device_id="dev-galaxy", command_id=first["command_id"], capability="BATTERY_STATUS", args={}
    )
    assert again["command_id"] == first["command_id"]
    assert len(inbox.pending_for("dev-galaxy")) == 1  # não duplicou


def test_inbox_enqueue_same_command_after_resolve_returns_final():
    inbox = mobile_inbox.get_mobile_inbox()
    cid = inbox.enqueue(device_id="dev-galaxy", capability="DEVICE_INFO", args={})["command_id"]
    inbox.submit_result(device_id="dev-galaxy", command_id=cid, status="success", result={"model": "A15"})
    dup = inbox.enqueue(device_id="dev-galaxy", capability="DEVICE_INFO", command_id=cid)
    assert dup["status"] == "success"  # não re-executa
    assert len(inbox.pending_for("dev-galaxy")) == 0


def test_inbox_submit_result_resolves_and_idempotent():
    inbox = mobile_inbox.get_mobile_inbox()
    cid = inbox.enqueue(device_id="dev-galaxy", capability="BATTERY_STATUS", args={})["command_id"]
    summary = inbox.submit_result(
        device_id="dev-galaxy", command_id=cid, status="success", result={"level": 87, "charging": False}
    )
    assert summary["status"] == "success"
    assert summary["result"]["level"] == 87
    assert inbox.pending_for("dev-galaxy") == []
    again = inbox.submit_result(device_id="dev-galaxy", command_id=cid, status="success")
    assert again["status"] == "success"  # idempotente
    assert inbox.counts().dropped == 0


def test_inbox_submit_result_unknown_command_is_dropped():
    inbox = mobile_inbox.get_mobile_inbox()
    out = inbox.submit_result(device_id="dev-galaxy", command_id="forasteiro", status="success")
    assert out["dropped"] is True
    assert inbox.counts().dropped == 1


def test_inbox_submit_result_rejects_invalid_status():
    inbox = mobile_inbox.get_mobile_inbox()
    cid = inbox.enqueue(device_id="dev-galaxy", capability="VIBRATE", args={"duration_ms": 200})["command_id"]
    with pytest.raises(mobile_inbox.MobileInboxError):
        inbox.submit_result(device_id="dev-galaxy", command_id=cid, status="teleported")


def test_inbox_pending_bounded_per_device():
    inbox = mobile_inbox.get_mobile_inbox()
    for i in range(25):
        inbox.enqueue(device_id="dev-galaxy", capability="DEVICE_INFO", args={})
    assert len(inbox.pending_for("dev-galaxy")) <= mobile_inbox._MAX_PENDING_PER_DEVICE  # noqa: SLF001


def test_inbox_generates_command_id_when_absent():
    inbox = mobile_inbox.get_mobile_inbox()
    summary = inbox.enqueue(device_id="dev-x", capability="DEVICE_INFO", args={})
    assert summary["command_id"].startswith("mc_")

# ---------------------------------------------------------------------------
# Parte C — mobile_agent (orquestrador)
# ---------------------------------------------------------------------------


def test_resolve_no_devices(db_session):
    res = mobile_agent.resolve_target(db_session)
    assert res.ok is False
    assert "Nenhum dispositivo móvel" in res.message


def test_resolve_single_deliverable(db_session):
    _add_device(db_session)
    res = mobile_agent.resolve_target(db_session)
    assert res.ok is True
    assert res.device.name == "Galaxy A15"
    assert res.view.deliverable is True


def test_resolve_hint_exact_and_fuzzy(db_session):
    _add_device(db_session, id="dev-a", name="Galaxy A15")
    _add_device(db_session, id="dev-b", name="Moto G84")
    exact = mobile_agent.resolve_target(db_session, hint="Galaxy A15")
    assert exact.device.id == "dev-a"
    fuzzy = mobile_agent.resolve_target(db_session, hint="galaxy")
    assert fuzzy.device.id == "dev-a"


def test_resolve_hint_unknown_lists_available(db_session):
    _add_device(db_session, id="dev-a", name="Galaxy A15")
    res = mobile_agent.resolve_target(db_session, hint="iPhone")
    assert res.ok is False
    assert "Galaxy A15" in res.message


def test_resolve_ambiguity_without_session_link(db_session):
    _add_device(db_session, id="dev-a", name="Galaxy A15")
    _add_device(db_session, id="dev-b", name="Moto G84")
    res = mobile_agent.resolve_target(db_session, session_id="sess-1")
    assert res.ok is False  # ambiguidade → pede confirmação com os nomes
    assert "Moto G84" in res.message


def test_resolve_prefers_session_linked_device(db_session):
    _add_device(db_session, id="dev-a", name="Galaxy A15")
    _add_device(db_session, id="dev-b", name="Moto G84", jarvis_session_id="sess-1")
    res = mobile_agent.resolve_target(db_session, session_id="sess-1")
    assert res.ok is True
    assert res.device.id == "dev-b"


def test_resolve_offline_hinted_device_flagged(db_session):
    _add_device(db_session, last_seen_at=utcnow() - timedelta(minutes=10))
    res = mobile_agent.resolve_target(db_session, hint="Galaxy A15")
    assert res.ok is False
    assert "não está conectado" in res.message


def test_dispatch_unsupported_capability():
    # ACCESSIBILITY_CONTROL é declarada mas não executável → UNSUPPORTED.
    async def _run():
        return await mobile_agent.dispatch_mobile(
            None, capability="ACCESSIBILITY_CONTROL", args={}
        )

    outcome = asyncio.run(_run())
    assert outcome.ok is False
    assert outcome.status == "unsupported"


class FakeWanLink:
    """CoreLink fake: dispositivo vinculado; comando responde SUCCESS no 2º poll."""

    def __init__(self, bound_device_ids=()):
        self._bound = set(bound_device_ids)
        self.commands: dict[str, dict] = {}
        self._polls: dict[str, int] = {}

    def is_device_bound(self, device_id) -> bool:
        return device_id in self._bound

    def bound_device_ids(self) -> list[str]:
        return list(self._bound)

    async def dispatch_mobile_command(self, **kwargs):
        cid = kwargs["command_id"]
        self.commands[cid] = kwargs
        self._polls[cid] = 0
        return {
            "command_id": cid,
            "status": "pending",
            "device_id": kwargs["device_id"],
            "capability": kwargs["capability"],
        }

    def pending_command_summary(self, command_id):
        if command_id not in self._polls:
            raise KeyError(f"comando desconhecido: {command_id}")
        self._polls[command_id] += 1
        if self._polls[command_id] >= 2:
            return {
                "command_id": command_id,
                "status": "success",
                "result": {"level": 80, "charging": False},
            }
        return {
            "command_id": command_id,
            "status": "pending",
            "device_id": "dev-galaxy",
            "capability": "BATTERY_STATUS",
        }


async def _auto_resolve_lan(device_id, *, status="success", result=None):
    inbox = mobile_inbox.get_mobile_inbox()
    for _ in range(250):
        pending = inbox.pending_for(device_id)
        if pending:
            inbox.submit_result(
                device_id=device_id,
                command_id=pending[0]["command_id"],
                status=status,
                result=result,
            )
            return pending[0]["command_id"]
        await asyncio.sleep(0.01)
    return None


async def test_dispatch_wan_success(db_session):
    _add_device(db_session)
    link = FakeWanLink(bound_device_ids={"dev-galaxy"})
    outcome = await mobile_agent.dispatch_mobile(
        db_session,
        capability="BATTERY_STATUS",
        args={},
        hint="Galaxy A15",
        link=link,
        timeout_ms=3000,
    )
    assert outcome.ok is True
    assert outcome.transport == "wan"
    assert outcome.device_name == "Galaxy A15"
    assert outcome.result["level"] == 80
    assert outcome.status == "success"
    assert outcome.command_id in link.commands


async def test_dispatch_lan_success(db_session):
    _add_device(db_session)
    feeder = asyncio.create_task(
        _auto_resolve_lan("dev-galaxy", result={"level": 42, "charging": True})
    )
    outcome = await mobile_agent.dispatch_mobile(
        db_session,
        capability="BATTERY_STATUS",
        args={},
        timeout_ms=3000,
    )
    command_id = await feeder
    assert outcome.ok is True
    assert outcome.transport == "lan"
    assert outcome.result["level"] == 42
    assert outcome.command_id == command_id


async def test_dispatch_lan_denied_no_fake_success(db_session):
    _add_device(db_session)
    feeder = asyncio.create_task(_auto_resolve_lan("dev-galaxy", status="denied"))
    outcome = await mobile_agent.dispatch_mobile(
        db_session,
        capability="SET_VOLUME",
        args={"level": 30},
        timeout_ms=3000,
    )
    await feeder
    assert outcome.ok is False
    assert outcome.status == "denied"
    assert "negado" in outcome.summary


async def test_dispatch_lan_timeout(db_session):
    _add_device(db_session)
    outcome = await mobile_agent.dispatch_mobile(
        db_session,
        capability="DEVICE_INFO",
        args={},
        timeout_ms=1000,
    )
    assert outcome.ok is False
    assert outcome.status == "timeout"
    assert "TIMEOUT" in outcome.summary


async def test_dispatch_no_mobile_devices(db_session):
    outcome = await mobile_agent.dispatch_mobile(
        db_session, capability="BATTERY_STATUS", args={}
    )
    assert outcome.ok is False
    assert "Nenhum dispositivo móvel" in outcome.summary


def test_mobile_agent_rejects_desktop_devices(db_session):
    _add_device(db_session, id="dev-pc", name="Meu PC", device_type="desktop")
    res = mobile_agent.resolve_target(db_session)
    assert res.ok is False  # desktop não é alvo de mobile

# ---------------------------------------------------------------------------
# Parte D — Intent determinística (mobile_*)
# ---------------------------------------------------------------------------


def test_intent_mobile_battery():
    match = detect_intent("quanto está a bateria do meu celular?")
    assert match is not None
    assert match.tool_call.name == "mobile_battery_status"
    assert match.confidence >= 0.85


def test_intent_mobile_device_info():
    match = detect_intent("quais as informações do meu telefone")
    assert match is not None
    assert match.tool_call.name == "mobile_device_info"


def test_intent_mobile_open_url_on_device():
    match = detect_intent("abra o youtube no meu celular")
    assert match is not None
    assert match.tool_call.name == "mobile_open_url"
    assert match.tool_call.arguments["url"] == "https://youtube.com"


def test_intent_mobile_open_app_beats_pc():
    match = detect_intent("abra o chrome no meu celular")
    assert match is not None
    assert match.tool_call.name == "mobile_open_app"
    assert match.tool_call.arguments["package_name"] == "com.android.chrome"


def test_intent_pc_open_app_unaffected():
    match = detect_intent("abra o chrome")
    assert match is not None
    assert match.tool_call.name == "open_application"
    assert match.tool_call.arguments["target"] == "chrome"


def test_intent_mobile_vibrate():
    match = detect_intent("faça meu celular vibrar")
    assert match is not None
    assert match.tool_call.name == "mobile_vibrate"
    assert match.tool_call.arguments["duration_ms"] == 500


def test_intent_plain_text_returns_none():
    assert detect_intent("qual é a capital do Brasil?") is None


# ---------------------------------------------------------------------------
# Parte E — API LAN (contrato; gate REMOTE off nos testes → 503)
# ---------------------------------------------------------------------------


def test_lan_poll_endpoint_503_when_remote_disabled(client):
    resp = client.post(
        "/api/remote/devices/dev-galaxy/commands/poll",
        json={"token": "x"},
    )
    assert resp.status_code == 503


def test_lan_result_endpoint_503_when_remote_disabled(client):
    resp = client.post(
        "/api/remote/devices/dev-galaxy/commands/result",
        json={
            "token": "x",
            "command_id": "mc_1",
            "status": "success",
            "result": {"level": 80},
        },
    )
    assert resp.status_code == 503