"""Testes da Fase 28 — Remote Operations & Secure Control (backend).

Parte A — Tool Engine: `remote_operation` registrada (LEVEL_1/LOW) e catálogo
  canônico de ações disponível (sem capability/device_id no loop do LLM).

Parte B — Ciclo de vida local (PC): operações PC-only executam via Tool Registry
  + Permission Engine com controller fake; alvo padrão local quando explicito
  ~> computador; passo desconhecido/alvo ausente falham com mensagens seguras.

Parte C — Idempotência: reuso de operation_id não re-despacha; passo `running`
  num reprocessamento vira `failed` (sem dupla execução); operação duplicada em
  paralelo não executa duas vezes.

Parte D — Execução móvel: capacidades BATTERY/DEVICE_INFO etc. através do
  `mobile_agent` (target por alias/nome; dispatch WAN fake); ação sem suporte
  móvel falha com mensagem clara.

Parte E — Safety por passo: L2 cria ApprovalRequest VINCULADO (operation_id,
  step, action, target) e pausa a operação; aprovar retoma o passo exato
  (authorized) sem nova confirmação; negar vira denied + cancelados os seguintes;
  L2 sem sessão NÃO executa (denied). L3 é BLOQUEADO mesmo dentro de operação
  (nenhuma confirmação o torna aceitável) e cancela os passos seguintes.

Parte F — Cancelamento e observabilidade: cancel de operação pausada cancela os
  passos futuros; eventos publicados (created/started/completed); serialização
  sanitizada (sem segredos); listagem por sessão.

Parte G — API: POST/GET/list/cancel + 404 + idempotência; approvals/respond
  retoma a operação (decided→resume) e 409 já decidido.

Parte H — Intent determinística: padrão multi-dispositivo vira
  `remote_operation` (2 passos, confiança 0.97) sem roubar intenções
  single-device; troca de dispositivo na continuidade reaplica a última ação
  bem-sucedida da operação anterior.

Hermético: ENV=test, REMOTE_ENABLED=false, sem rede; controller do PC fake;
  nenhum secret/log token.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.ai.intent import detect_continuation, detect_intent
from app.computer import controller as computer_module
from app.models import RemoteOperation, Session
from app.models.remote import Device
from app.models.session import utcnow
from app.remote import mobile_agent, operations as ops
from app.remote import events as events_module
from app.schemas.ai import ToolCall
from app.tools.registry import get_tool_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_session(db, session_id="sess-28"):
    db.add(Session(id=session_id, title="Fase 28"))
    db.commit()


def _api_paired():
    """Pareia um device via serviço (HTTP pairings fica 503 com REMOTE_ENABLED=false)
    e devolve o token Bearer para os endpoints protegidos por device."""
    from app.db.session import SessionLocal
    from app.remote.pairing import create_pairing, submit_code

    db = SessionLocal()
    try:
        _, code = create_pairing(db)
        _, token = submit_code(db, code=code, device_name="Ops-Dev")
        return token
    finally:
        db.close()


def _api_headers(token):
    return {"Authorization": f"Bearer {token}"}


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


class FakePcController:
    """Controlador do PC determinístico — nunca toca no sistema real."""

    def __init__(self):
        self.opened_urls: list[str] = []
        self.opened_apps: list[str] = []
        self.closed_apps: list[str] = []
        self.locked = False
        self.shutdowns = 0
        self.restarts = 0
        self.sleeps = 0
        self.killed: list[int] = []

    def open_url(self, url):
        self.opened_urls.append(url)
        return f"URL aberta: {url}"

    def open_app(self, target):
        self.opened_apps.append(target)
        return f"App iniciado: {target}"

    def close_app(self, process_name):
        self.closed_apps.append(process_name)
        return f"App fechado: {process_name}"

    def system_stats(self):
        return {
            "cpu_percent": 12.0,
            "memory_total": 17179869184,
            "memory_used": 6442450944,
            "disk_total": 512110190592,
            "disk_free": 214748364800,
            "boot_time": "2026-09-01T08:00:00Z",
        }

    def system_status(self):
        return {**self.system_stats(), "process_count": 3, "hostname": "host-teste", "apps_running": []}

    def lock_computer(self):
        self.locked = True
        return "Computador bloqueado"

    def shutdown_computer(self):
        self.shutdowns += 1
        return "Desligando"

    def restart_computer(self):
        self.restarts += 1
        return "Reiniciando"

    def sleep_computer(self):
        self.sleeps += 1
        return "Suspenso"

    def kill_process(self, pid):
        self.killed.append(int(pid))
        return f"Processo {pid} encerrado"


class FakeWanLink:
    """CoreLink fake: dispositivo vinculado com resposta SUCCESS no poll."""

    def __init__(self, bound_device_ids=()):
        self._bound = set(bound_device_ids)
        self.commands: dict[str, dict] = {}

    def is_device_bound(self, device_id) -> bool:
        return device_id in self._bound

    def bound_device_ids(self) -> list[str]:
        return list(self._bound)

    async def dispatch_mobile_command(self, **kwargs):
        self.commands[kwargs["command_id"]] = kwargs
        return {
            "command_id": kwargs["command_id"],
            "status": "pending",
            "device_id": kwargs["device_id"],
            "capability": kwargs["capability"],
        }

    def pending_command_summary(self, command_id):
        return {
            "command_id": command_id,
            "status": "success",
            "result": {"level": 80, "charging": False},
        }


@pytest.fixture
def fake_pc(monkeypatch):
    controller = FakePcController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)
    return controller


def _create(
    db,
    *,
    session_id="sess-28",
    steps,
    operation=None,
    operation_id=None,
):
    _add_session(db, session_id=session_id) if session_id else None
    return ops.create_or_get_operation(
        db,
        session_id=session_id,
        requested_action=operation or "operação de teste",
        steps=steps,
        operation_id=operation_id,
    )


def _step(action, params, target=None, **extra):
    step = {"action": action, "params": params}
    if target is not None:
        step["target"] = target
    step.update(extra)
    return step


# ---------------------------------------------------------------------------
# Parte A — Tool Engine
# ---------------------------------------------------------------------------


def test_remote_operation_registered_level1_low():
    registry = get_tool_registry()
    tool = registry.get("remote_operation")
    assert tool is not None
    assert tool.permission_level.value == 1
    assert tool.risk.value == "low"
    params = tool.parameters
    assert "steps" in params["properties"]
    assert "timeout_ms" in params["properties"]["steps"]["items"]["properties"]


def test_available_actions_catalog_has_core_actions():
    catalog = {a["action"]: a for a in ops.available_actions()}
    for name, level in [
        ("OPEN_URL", 1),
        ("OPEN_APP", 1),
        ("BATTERY_STATUS", 0),
        ("GET_SYSTEM_STATS", 0),
        ("CLOSE_APP", 2),
        ("SHUTDOWN_COMPUTER", 3),
    ]:
        assert catalog[name]["level"] == level
    assert catalog["SHUTDOWN_COMPUTER"]["risk"] == "high"
    assert catalog["BATTERY_STATUS"]["mobile"] is True
    assert catalog["GET_SYSTEM_STATS"]["mobile"] is False


# ---------------------------------------------------------------------------
# Parte B — Ciclo de vida local (PC)
# ---------------------------------------------------------------------------


async def test_pc_operation_success(db_session, fake_pc):
    op = _create(
        db_session,
        steps=[
            _step("OPEN_URL", {"url": "https://youtube.com"}, "computador"),
            _step("OPEN_APP", {"target": "chrome"}, "pc"),
        ],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert outcome.ok is True
    assert outcome.status == "success"
    assert ops.to_out(op)["status"] == "success"
    assert fake_pc.opened_urls == ["https://youtube.com"]
    assert fake_pc.opened_apps == ["chrome"]
    statuses = [s["status"] for s in ops.to_out(op)["steps"]]
    assert statuses == ["success", "success"]


async def test_pc_action_without_target_defaults_pc(db_session, fake_pc):
    op = _create(db_session, steps=[_step("GET_SYSTEM_STATS", {}, None)])
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert outcome.ok is True
    assert [s["status"] for s in ops.to_out(op)["steps"]] == ["success"]


async def test_pc_open_app_with_unknown_package_fails(db_session, fake_pc):
    op = _create(
        db_session,
        steps=[_step("OPEN_APP", {"package_name": "com.unknown.fake.app"}, "computador")],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert outcome.status == "failed"
    assert fake_pc.opened_apps == []
    step = ops.to_out(op)["steps"][0]
    assert step["status"] == "failed"


async def test_unknown_action_fails_step(db_session, fake_pc):
    op = _create(db_session, steps=[_step("HACK_THE_PLANET", {}, "pc")])
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert outcome.status == "failed"
    step = ops.to_out(op)["steps"][0]
    assert step["status"] == "failed"
    assert "desconhecida" in (step["message"] or "")


async def test_pc_open_url_unknown_target_fails(db_session, fake_pc):
    op = _create(
        db_session,
        steps=[_step("OPEN_URL", {"url": "https://example.com"}, "alvo-inventado")],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert outcome.ok is False
    assert fake_pc.opened_urls == []


# ---------------------------------------------------------------------------
# Parte C — Idempotência
# ---------------------------------------------------------------------------


async def test_idempotent_rerun_no_redispatch(db_session, fake_pc):
    operation_id = "op-idemp-1"
    _create(
        db_session,
        session_id="sess-28",
        steps=[_step("OPEN_URL", {"url": "https://youtube.com"}, "pc")],
        operation_id=operation_id,
    )
    first = await ops.run_operation(
        db_session, operation=ops.load_by_id(db_session, operation_id), session_id="sess-28"
    )
    assert first.ok is True
    second = await ops.run_operation(
        db_session, operation=ops.load_by_id(db_session, operation_id), session_id="sess-28"
    )
    assert second.status == "success"
    assert second.ok is True
    assert len(fake_pc.opened_urls) == 1  # não re-executou


async def test_running_step_on_reprocess_marks_failed_without_dispatch(db_session, fake_pc):
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[_step("OPEN_URL", {"url": "https://youtube.com"}, "pc", status="running")],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert outcome.status == "failed"
    assert fake_pc.opened_urls == []  # nunca mandou abrir de novo
    assert ops.to_out(op)["steps"][0]["status"] == "failed"


async def test_parallel_same_operation_serialized(db_session, fake_pc):
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[_step("GET_SYSTEM_STATS", {}, None)],
        operation_id="op-par-1",
    )
    ops._running_operations.add(op.id)  # simula execução ativa concorrente
    try:
        outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
        assert outcome.ok is False
        assert outcome.error == "concurrency"
    finally:
        ops._running_operations.discard(op.id)


# ---------------------------------------------------------------------------
# Parte D — Execução móvel
# ---------------------------------------------------------------------------


async def test_mobile_operation_wan_success(db_session):
    _add_device(db_session)
    link = FakeWanLink(bound_device_ids={"dev-galaxy"})
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[_step("BATTERY_STATUS", {}, "celular")],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id, link=link)
    assert outcome.status == "success"
    step = ops.to_out(op)["steps"][0]
    assert step["status"] == "success"
    assert step["transport"] == "wan"
    assert step["device"] == "Galaxy A15"


async def test_mobile_operation_by_device_name(db_session):
    _add_device(db_session)
    link = FakeWanLink(bound_device_ids={"dev-galaxy"})
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[_step("BATTERY_STATUS", {}, "Galaxy A15")],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id, link=link)
    assert outcome.ok is True
    assert ops.to_out(op)["steps"][0]["device"] == "Galaxy A15"


async def test_mobile_action_without_mobile_support_fails(db_session):
    _add_device(db_session)
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[_step("GET_SYSTEM_STATS", {}, "celular")],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert outcome.status == "failed"
    step = ops.to_out(op)["steps"][0]
    assert step["status"] == "unsupported"
    assert "celular" in (step["message"] or "")


# ---------------------------------------------------------------------------
# Parte E — Safety por passo (L2/L3)
# ---------------------------------------------------------------------------


async def test_l2_step_pauses_awaiting_confirmation(db_session, fake_pc):
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[_step("CLOSE_APP", {"process_name": "chrome"}, "pc")],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert outcome.requires_confirmation is True
    assert outcome.status == "awaiting_confirmation"
    assert op.status == "awaiting_confirmation"
    assert len(outcome.pending_approvals) == 1
    approval = outcome.pending_approvals[0]
    assert approval.tool_name == "remote_operation"
    assert approval.arguments["operation_id"] == op.id
    assert approval.arguments["step"] == 0
    assert approval.arguments["action"] == "CLOSE_APP"
    assert fake_pc.closed_apps == []  # ainda não executou


async def test_l2_approve_resumes_exact_step(db_session, fake_pc):
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[
            _step("CLOSE_APP", {"process_name": "chrome"}, "pc"),
            _step("GET_SYSTEM_STATS", {}, None),
        ],
    )
    first = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert first.status == "awaiting_confirmation"
    approval = first.pending_approvals[0]
    resumed = await ops.resume_operation_from_approval(
        db_session, operation=op, approved=True, step=0, session_id=op.session_id
    )
    assert resumed.status == "success"
    assert resumed.ok is True
    assert fake_pc.closed_apps == ["chrome"]
    # passo aprovado foi executado; o seguinte rodou sem nova confirmação
    statuses = [s["status"] for s in ops.to_out(op)["steps"]]
    assert statuses == ["success", "success"]


async def test_l2_deny_marks_denied_and_cancels_following(db_session, fake_pc):
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[
            _step("CLOSE_APP", {"process_name": "chrome"}, "pc"),
            _step("OPEN_APP", {"target": "chrome"}, "pc"),
        ],
    )
    first = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    approval = first.pending_approvals[0]
    outcome = await ops.resume_operation_from_approval(
        db_session, operation=op, approved=False, step=0, session_id=op.session_id
    )
    assert outcome.status == "denied"
    statuses = [s["status"] for s in ops.to_out(op)["steps"]]
    assert statuses == ["denied", "cancelled"]
    assert fake_pc.closed_apps == [] and fake_pc.opened_apps == []


async def test_l2_without_session_is_denied(db_session, fake_pc):
    op = ops.create_or_get_operation(
        db_session,
        session_id=None,
        requested_action="fechar sem sessão",
        steps=[_step("CLOSE_APP", {"process_name": "chrome"}, "pc")],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=None)
    assert outcome.status == "denied"
    assert fake_pc.closed_apps == []
    assert op.error and "sessão" in op.error


async def test_l3_blocked_even_approved(db_session, fake_pc):
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[
            _step("SHUTDOWN_COMPUTER", {}, "pc"),
            _step("OPEN_APP", {"target": "chrome"}, "pc"),
        ],
    )
    outcome = await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert outcome.status == "denied"
    assert outcome.requires_confirmation is False
    assert fake_pc.shutdowns == 0
    statuses = [s["status"] for s in ops.to_out(op)["steps"]]
    assert statuses == ["denied", "cancelled"]
    # "aprovar" via resume de um approval imaginário NÃO muda o estado terminal
    late = await ops.resume_operation_from_approval(
        db_session, operation=op, approved=True, step=0, session_id=op.session_id
    )
    assert late.status == "denied"
    assert fake_pc.shutdowns == 0


# ---------------------------------------------------------------------------
# Parte F — Cancelamento e observabilidade
# ---------------------------------------------------------------------------


async def test_cancel_paused_operation(db_session, fake_pc):
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[_step("CLOSE_APP", {"process_name": "chrome"}, "pc"), _step("LOCK_COMPUTER", {}, "pc")],
    )
    await ops.run_operation(db_session, operation=op, session_id=op.session_id)
    assert op.status == "awaiting_confirmation"
    cancelled = ops.cancel_operation(db_session, op.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    statuses = [s["status"] for s in ops.to_out(cancelled)["steps"]]
    assert statuses == ["cancelled", "cancelled"]  # passo pausado + próximos cancelados


def test_events_published(db_session, fake_pc):
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[_step("OPEN_URL", {"url": "https://github.com"}, "pc")],
    )
    asyncio.run(ops.run_operation(db_session, operation=op, session_id=op.session_id))
    types = {e["type"] for e in events_module._broker._replay}
    assert "remote.operation.created" in types
    assert "remote.operation.started" in types
    assert "remote.operation.completed" in types


def test_to_out_sanitized_and_list_by_session(db_session, fake_pc):
    _create(
        db_session,
        session_id="sess-28",
        steps=[_step("OPEN_URL", {"url": "https://github.com"}, "pc")],
    )
    items = ops.list_operations(db_session, session_id="sess-28")
    assert len(items) == 1
    out = items[0]
    assert "operation_id" not in json.dumps(out)
    assert "token" not in json.dumps(out).lower()
    assert out["requested_action"] == "operação de teste"


# ---------------------------------------------------------------------------
# Parte G — API
# ---------------------------------------------------------------------------


def test_api_create_pc_operation(client, monkeypatch):
    controller = FakePcController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)
    token = _api_paired()
    res = client.post(
        "/api/remote/operations",
        json={
            "operation": "abrir teste",
            "steps": [{"action": "OPEN_APP", "target": "pc", "params": {"target": "chrome"}}],
        },
        headers=_api_headers(token),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["accepted"] is True
    assert data["requires_confirmation"] is False
    assert data["operation"]["status"] == "success"
    assert controller.opened_apps == ["chrome"]


def test_api_operations_without_token_unauthorized(client, monkeypatch):
    """Fase 28.1: endpoint strict — sem Bearer, 401 antes de tocar o controller."""
    controller = FakePcController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)
    res = client.post(
        "/api/remote/operations",
        json={"steps": [{"action": "OPEN_APP", "target": "pc", "params": {"target": "chrome"}}]},
    )
    assert res.status_code == 401
    assert controller.opened_apps == []


def test_api_create_l2_pauses_awaiting_confirmation(client, monkeypatch):
    """Fase 28.1: operação remota SEMPRE tem sessão (âncora JARVIS do device);
    L2 segura pausa aguardando confirmação (nunca 'denied por falta de sessão')."""
    controller = FakePcController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)
    token = _api_paired()
    res = client.post(
        "/api/remote/operations",
        json={
            "operation": "fechar chrome",
            "steps": [{"action": "CLOSE_APP", "target": "pc", "params": {"process_name": "chrome"}}],
        },
        headers=_api_headers(token),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["requires_confirmation"] is True
    assert data["operation"]["status"] == "awaiting_confirmation"
    assert len(data["approvals"]) == 1
    assert data["approvals"][0]["arguments"]["operation_id"] == data["operation"]["id"]
    assert controller.closed_apps == []


def test_api_get_list_cancel(client, monkeypatch):
    controller = FakePcController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)
    token = _api_paired()
    headers = _api_headers(token)
    created = client.post(
        "/api/remote/operations",
        json={"steps": [{"action": "OPEN_APP", "target": "pc", "params": {"target": "chrome"}}]},
        headers=headers,
    ).json()
    op_id = created["operation"]["id"]

    listed = client.get("/api/remote/operations", headers=headers).json()
    assert any(o["id"] == op_id for o in listed)

    got = client.get(f"/api/remote/operations/{op_id}", headers=headers).json()
    assert got["status"] == "success"

    assert client.get("/api/remote/operations/op_nao-existe", headers=headers).status_code == 404
    cancelled = client.post(f"/api/remote/operations/{op_id}/cancel", headers=headers).json()
    assert cancelled["status"] == "success"  # terminal permanece terminal

    # listar sem token não expõe operações remotas
    assert client.get("/api/remote/operations").status_code == 401


def test_api_approvals_respond_resumes_operation(client, fake_pc):
    from app.db.session import SessionLocal

    sid = client.post("/api/sessions", json={}).json()["id"]
    db = SessionLocal()
    try:
        op = ops.create_or_get_operation(
            db,
            session_id=sid,
            requested_action="fechar chrome",
            steps=[_step("CLOSE_APP", {"process_name": "chrome"}, "pc")],
        )
        outcome = asyncio.run(ops.run_operation(db, operation=op, session_id=sid))
        approval = outcome.pending_approvals[0]
        approval_id = approval.id
    finally:
        db.close()

    pending = client.get(f"/api/approvals/pending?session_id={sid}").json()
    assert len(pending) == 1
    assert pending[0]["arguments"]["operation_id"] == op.id
    assert pending[0]["arguments"]["step"] == 0

    res = client.post(f"/api/approvals/{approval_id}/respond", json={"approved": True})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "decided"
    assert data["operation_id"] == op.id
    assert data["result"]["status"] == "success"
    assert fake_pc.closed_apps == ["chrome"]

    # decisão já dada (aplicada) → novo respond retorna 409
    assert client.post(f"/api/approvals/{approval_id}/respond", json={"approved": True}).status_code == 409


def test_api_approvals_respond_deny_flips_operation(client, fake_pc):
    from app.db.session import SessionLocal

    sid = client.post("/api/sessions", json={}).json()["id"]
    db = SessionLocal()
    try:
        op = ops.create_or_get_operation(
            db,
            session_id=sid,
            requested_action="fechar chrome",
            steps=[_step("CLOSE_APP", {"process_name": "chrome"}, "pc")],
        )
        outcome = asyncio.run(ops.run_operation(db, operation=op, session_id=sid))
        approval_id = outcome.pending_approvals[0].id
    finally:
        db.close()

    res = client.post(f"/api/approvals/{approval_id}/respond", json={"approved": False})
    assert res.status_code == 200
    assert res.json()["result"]["status"] == "denied"
    assert fake_pc.closed_apps == []


# ---------------------------------------------------------------------------
# Parte H — Intent determinística
# ---------------------------------------------------------------------------


def test_intent_multi_device_pattern_creates_operation():
    match = detect_intent("abra o youtube no meu computador e depois o chrome no meu celular")
    assert match is not None
    assert match.tool_call.name == "remote_operation"
    assert match.confidence == 0.97
    assert match.tool_call.arguments["steps"] == [
        {"action": "OPEN_URL", "params": {"url": "https://youtube.com"}, "target": "computador"},
        {"action": "OPEN_APP", "params": {"package_name": "com.android.chrome"}, "target": "celular"},
    ]


def test_intent_multi_device_reuses_site():
    match = detect_intent("abra o youtube no meu computador e depois no meu celular")
    assert match is not None
    assert match.tool_call.name == "remote_operation"
    assert match.tool_call.arguments["steps"][1]["params"]["url"] == "https://youtube.com"


def test_intent_single_device_not_stolen():
    match = detect_intent("abra o youtube no meu computador")
    assert match is not None
    assert match.tool_call.name == "open_url"  # fluxo PC normal preservado
    assert match.confidence < 0.97


def test_continuation_device_switch_reuses_last_operation(db_session, fake_pc):
    op = _create(
        db_session,
        session_id="sess-28",
        steps=[
            _step("OPEN_URL", {"url": "https://youtube.com"}, "computador"),
            _step("OPEN_APP", {"target": "chrome"}, "pc"),
        ],
    )
    asyncio.run(ops.run_operation(db_session, operation=op, session_id=op.session_id))
    match = detect_continuation(
        "agora no celular", ctx=None, db=db_session, session_id="sess-28"
    )
    assert match is not None
    assert match.tool_call.name == "remote_operation"
    args = match.tool_call.arguments["steps"][0]
    assert args["action"] == "OPEN_APP"  # última ação bem-sucedida
    assert args["target"] == "celular"


# ---------------------------------------------------------------------------
# Integração Bônus — agente (via LLM fake) para L1 e L2
# ---------------------------------------------------------------------------


def create_session(client):
    res = client.post("/api/sessions", json={})
    assert res.status_code == 201
    return res.json()


def plan_call(fake_ai, *calls):
    fake_ai._planned_tool_calls = list(calls)


def send_agent(client, session_id, content):
    with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages",
        json={"content": content, "stream": True, "tools": True},
    ) as res:
        assert res.status_code == 200
        raw = b"".join(res.iter_bytes()).decode()
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    return [json.loads(ln[6:]) for ln in lines]


def test_agent_remote_operation_l1_returns_result(client, fake_ai, fake_pc):
    plan_call(
        fake_ai,
        ToolCall(
            name="remote_operation",
            arguments={
                "operation": "abrir chrome no pc",
                "steps": [{"action": "OPEN_APP", "target": "pc", "params": {"target": "chrome"}}],
            },
        ),
    )
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "execute a operação de abertura")
    done = [e for e in events if e["type"] == "tool_done" and e.get("name") == "remote_operation"]
    assert len(done) == 1
    payload = json.loads(done[0]["output"])
    assert payload["type"] == "remote.operation.result"
    assert payload["status"] == "success"
    assert fake_pc.opened_apps == ["chrome"]


def test_agent_remote_operation_l2_pauses_before_llm_continuation(client, fake_ai, fake_pc):
    plan_call(
        fake_ai,
        ToolCall(
            name="remote_operation",
            arguments={
                "operation": "fechar chrome no pc",
                "steps": [{"action": "CLOSE_APP", "target": "pc", "params": {"process_name": "chrome"}}],
            },
        ),
    )
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "execute a operação de fechamento")
    types = [e["type"] for e in events]
    assert "approval_request" in types
    assert "approval_pending" in types
    # não gerou resposta final: o turno pausou aguardando decisão
    assert "done" not in types
    assert fake_ai.generate_calls == 1