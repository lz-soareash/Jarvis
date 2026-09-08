"""Fase 20 — VEGA V1: integração Computer Agent ↔ chat (single agent).

Cobre (hermético, fakes — nunca hardware/sistema real):
- PRESENÇA fine-grained: estados reais do Computer Agent expostos
  (perceiving/planning/executing/observing/verifying/recovering) em vez do
  colapso "working", derivados de sinal real do store.
- `computer_use` IN-TURN (Fase 20): executa o loop do agente dentro do turno
  (não só cria tarefa), emite eventos reais via sink, retorna resultado rico,
  pausa por confirmação, retoma por `task_id`, obedece limites/segurança.
- SSE do turno: payloads `agent_event` encaminhados (sink→SSE).
"""

import asyncio
import json

import pytest

from app.core.config import settings
from app.computer_agent.models import (
    ComputerStatus,
    IntendedAction,
)
from app.computer_agent.planner import PlannerOutcome
from app.computer_agent.tool import ComputerUseTool
from app.tools.base import ToolContext

from tests.test_computer_agent_fase19 import (
    FakePerceive,
    FakePlanner,
    _obs,
    run_async,
)
from tests.test_computer_action_fase18 import FakeComputerAdapter


@pytest.fixture(autouse=True)
def _presence_cache_reset():
    from app.services import presence

    presence._transitions_cached["state"] = None
    presence._transitions_cached["recorded_at"] = 0.0
    yield


@pytest.fixture
def enable_agent(monkeypatch):
    monkeypatch.setattr(settings, "computer_agent_enabled", True)
    monkeypatch.setattr(settings, "computer_agent_require_confirmation_l2", True)
    monkeypatch.setattr(settings, "computer_agent_max_steps", 12)
    monkeypatch.setattr(settings, "computer_agent_max_actions", 24)
    monkeypatch.setattr(settings, "computer_agent_max_retries", 2)
    monkeypatch.setattr(settings, "computer_agent_max_plan_retries", 3)
    monkeypatch.setattr(settings, "computer_agent_timeout_seconds", 180.0)
    yield


def _active_agent_task(goal, *, status: ComputerStatus, session_id="s20"):
    """Registra tarefa com estado ATIVO (fonte real de sinal de presença)."""
    from app.computer_agent import store as ca_store

    task = ca_store.create_task(
        goal=goal, session_id=session_id, requested_by="agent", device_id=None
    )
    task = task.with_(status=status)
    ca_store.get_store().save(task)
    return task


def _agent_with(monkeypatch, *, steps=None, obs=None, permission_level=1):
    """Mocks a classe ComputerAgent usada pela tool, reusando o agent REAL.

    O FakeAgent usa o `RealAgent` do MESMO módulo com planner/perceive/executor
    fakes injetados — testa a máquina de estados real, herméticamente.
    `permission_level` controla `effective_level(db, "computer_action")`.
    """
    import app.services.permissions as perms

    monkeypatch.setattr(perms, "effective_level", lambda db, tool: permission_level)

    from app.action.executor import ActionExecutor
    from app.computer_agent import store as ca_store_ref
    from app.computer_agent import tool as tool_module

    planner = FakePlanner(steps or [])
    perceive = FakePerceive(obs or _obs())
    adapter = FakeComputerAdapter()

    class _FakeAgent:
        def __init__(self, *a, **k):
            self._executor = ActionExecutor(adapter=adapter, override_enabled=True)
            self._planner = planner
            self._perceive = perceive

        def is_enabled(self):
            return True

        async def advance(self, db, provider, task, *, sink=None):
            from app.computer_agent.agent import ComputerAgent as RealAgent

            real = RealAgent(planner=self._planner, executor=self._executor,
                             perceive=self._perceive, store=ca_store_ref.get_store())
            return await real.advance(db, provider, task, sink=sink)

    monkeypatch.setattr(tool_module, "ComputerAgent", _FakeAgent)
    return _FakeAgent


class DummyProvider:
    name = "dummy"


def _ctx(db, provider=None, *, session_id="s20", emit=None):
    return ToolContext(
        db=db,
        provider=provider or DummyProvider(),
        session_id=session_id,
        extras={"emit": emit} if emit else {},
    )


def _result_output(result):
    """computer_use devolve output como JSON string (contrato ToolResult)."""
    if isinstance(result.output, dict):
        return result.output
    import json as _json

    try:
        return _json.loads(result.output)
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# PRESENÇA — estados fine-grained reais do Computer Agent (Fase 20)
# ---------------------------------------------------------------------------

class TestPresenceFineGrained:
    @pytest.mark.parametrize(
        "status,expected_state",
        [
            (ComputerStatus.PERCEIVING, "perceiving"),
            (ComputerStatus.PLANNING, "planning"),
            (ComputerStatus.EXECUTING, "executing"),
            (ComputerStatus.OBSERVING, "observing"),
            (ComputerStatus.VERIFYING, "verifying"),
            (ComputerStatus.RECOVERING, "recovering"),
        ],
    )
    def test_active_computer_state_exposed(self, db_session, status, expected_state):
        from app.services import presence

        _active_agent_task("go", status=status)
        body = presence.compute_presence(db_session)
        assert body["state"] == expected_state
        assert body["label"]

    def test_completed_task_does_not_force_state(self, db_session):
        from app.services import presence

        task = _active_agent_task("go", status=ComputerStatus.COMPLETED)
        body = presence.compute_presence(db_session)
        assert body["state"] not in ("perceiving", "planning", "executing",
                                     "observing", "verifying", "recovering", "working")

    def test_waiting_confirmation_still_respected(self, db_session):
        from app.services import presence

        from datetime import datetime, timedelta, timezone

        from app.models import ApprovalRequest
        from app.services.chat import create_session

        sid = create_session(db_session).id
        req = ApprovalRequest(
            session_id=sid, tool_name="computer_action", permission_level=2,
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        db_session.add(req)
        db_session.commit()
        assert presence.compute_presence(db_session)["state"] == "waiting_confirmation"

    def test_label_endpoint_includes_fine_grained(self, client):
        res = client.get("/api/vega/state/labels")
        assert res.status_code == 200
        states = res.json()["states"]
        for s in ("perceiving", "planning", "executing", "observing",
                  "verifying", "recovering"):
            assert s in states


# ---------------------------------------------------------------------------
# computer_use IN-TURN — executa o loop dentro do turno (Fase 20)
# ---------------------------------------------------------------------------

class TestComputerUseInTurn:
    def test_runs_task_to_completion_with_result(self, enable_agent, db_session, monkeypatch):
        _agent_with(monkeypatch, steps=[IntendedAction(action="none", note="observar")])
        emit_hook = []
        ctx = _ctx(db_session, emit=lambda p: emit_hook.append(p))
        result = run_async(ComputerUseTool().run(ctx, goal="abrir o bloco de notas"))
        assert result.ok
        body = _result_output(result)
        assert body["status"] == "completed"
        assert body["task_id"]
        types = [p.get("type") for p in emit_hook]
        assert "computer.task.started" in types
        assert "computer.task.completed" in types

    def test_resumes_by_task_id_after_waiting_confirmation(
        self, enable_agent, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "computer_agent_autonomy", "C2")
        step = IntendedAction(
            action="click", params={"x": 100, "y": 100},
            expected={"window_title_contains": "Bloco de Notas"},
        )
        _agent_with(monkeypatch, steps=[step], permission_level=2)
        emit_hook = []
        ctx = _ctx(db_session, emit=lambda p: emit_hook.append(p))

        first = run_async(ComputerUseTool().run(ctx, goal="clicar"))
        assert first.ok
        body = _result_output(first)
        assert body["status"] == "waiting_confirmation"
        task_id = body["task_id"]
        assert body["_approvals"]

        from app.services import approvals as approval_service

        approval = approval_service.get_approval(db_session, body["_approvals"][0]["id"])
        assert approval is not None
        approval_service.mark_decided(db_session, approval, True)

        second = run_async(
            ComputerUseTool().run(ctx, goal="ignorado", task_id=task_id)
        )
        assert second.ok
        body2 = _result_output(second)
        assert body2["status"] == "completed"
        assert body2["task_id"] == task_id

    def test_goal_missing_fails_without_task(self, enable_agent, db_session, monkeypatch):
        _agent_with(monkeypatch, steps=[])
        ctx = _ctx(db_session)
        result = run_async(ComputerUseTool().run(ctx))
        assert not result.ok
        assert "goal" in result.output

    def test_unknown_resume_task_fails(self, enable_agent, db_session, monkeypatch):
        _agent_with(monkeypatch, steps=[])
        ctx = _ctx(db_session)
        result = run_async(
            ComputerUseTool().run(ctx, goal="qualquer", task_id="nao_existe")
        )
        assert not result.ok
        assert "não encontrada" in result.output

    def test_disabled_agent_never_runs(self, db_session, monkeypatch):
        from app.computer_agent import tool as tool_module

        monkeypatch.setattr(tool_module, "ComputerAgent", lambda *a, **k: None)
        ctx = _ctx(db_session)
        result = run_async(
            ComputerUseTool().run(ctx, goal="x")
        )
        assert not result.ok
        assert "desabilitado" in result.output

    def test_task_failure_returns_rich_tool_result(
        self, enable_agent, db_session, monkeypatch
    ):
        from app.action.executor import ActionExecutor
        from app.computer_agent import store as ca_store_ref
        from app.computer_agent import tool as tool_module

        planner = FakePlanner(
            [IntendedAction(action="type", params={"text": "x"},
                            expected={"window_title_contains": "nada"})]
        )
        perceive = FakePerceive(_obs())
        adapter = FakeComputerAdapter(fail="type_text")

        class _FakeAgent:
            def __init__(self, *a, **k):
                self._executor = ActionExecutor(adapter=adapter, override_enabled=True)
                self._planner = planner
                self._perceive = perceive

            def is_enabled(self):
                return True

            async def advance(self, db, provider, task, *, sink=None):
                from app.computer_agent.agent import ComputerAgent as RealAgent

                real = RealAgent(planner=planner, executor=self._executor,
                                 perceive=perceive, store=ca_store_ref.get_store())
                return await real.advance(db, provider, task, sink=sink)

        monkeypatch.setattr(tool_module, "ComputerAgent", _FakeAgent)
        ctx = _ctx(db_session)
        result = run_async(ComputerUseTool().run(ctx, goal="digita x"))
        assert not result.ok
        assert result.output.startswith("ERRO")


# ---------------------------------------------------------------------------
# SSE — encaminhamento agent_event (sink → turno)
# ---------------------------------------------------------------------------

class TestAgentEmitChannel:
    def test_emit_hook_is_injected_and_forwarded_to_sse(
        self, client, fake_ai, monkeypatch, enable_agent
    ):
        """Sink real do agente chega ao turno SSE como `agent_event`."""
        import app.services.permissions as perms
        from app.computer_agent import store as ca_store_ref
        from app.computer_agent import tool as tool_module
        from app.schemas.ai import ToolCall

        monkeypatch.setattr(settings, "local_first", False)
        # computer_use (LEVEL_2) roda in-turn no teste via permissão L1.
        monkeypatch.setattr(perms, "effective_level", lambda db, tool: 1)

        seen = {}

        class _CapturingAgent:
            def __init__(self, *a, **k):
                pass

            def is_enabled(self):
                return True

            async def advance(self, db, provider, task, *, sink=None):
                seen["sink_got"] = sink is not None
                if sink:
                    sink({"type": "computer.task.started", "task_id": task.task_id})
                    sink({"type": "computer.activity", "task_id": task.task_id})
                return task.with_(status=ComputerStatus.COMPLETED,
                                  completed_at="2026-01-01T00:00:00+00:00")

        monkeypatch.setattr(tool_module, "ComputerAgent", _CapturingAgent)

        fake_ai._planned_tool_calls = [
            ToolCall(name="computer_use", arguments={"goal": "observar a área de trabalho"})
        ]
        res = client.post("/api/sessions", json={})
        assert res.status_code == 201
        sid = res.json()["id"]

        with client.stream(
            "POST",
            f"/api/sessions/{sid}/messages",
            json={"content": "use o computador para observar", "stream": True, "tools": True},
        ) as stream:
            assert stream.status_code == 200
            raw = b"".join(stream.iter_bytes()).decode()

        assert seen.get("sink_got") is True
        lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
        events = [json.loads(ln[6:]) for ln in lines]
        payloads = [
            e.get("payload")
            for e in events
            if e.get("type") == "agent_event" and isinstance(e.get("payload"), dict)
        ]
        types = [p.get("type") for p in payloads]
        assert "computer.task.started" in types
        assert "computer.activity" in types


# ---------------------------------------------------------------------------
# SEGURANÇA — modelo NUNCA eleva autonomia/permissão (Fase 20)
# ---------------------------------------------------------------------------

class TestSecurityModelCantEscalate:
    def test_computer_use_does_not_accept_autonomy_param(self):
        """O modelo não tem como elevar C0–C4: a tool só aceita goal/task_id."""
        tool = ComputerUseTool()
        props = tool.parameters.get("properties", {})
        assert "autonomy" not in props
        assert "permission_level" not in props
        assert "permission" not in props
        # goal e task_id são as únicas entradas — autonomia vem só da config.
        assert set(props) <= {"goal", "session_id", "task_id"}

    def test_in_turn_loop_is_bounded(self):
        from app.computer_agent import tool as tool_module

        assert tool_module._MAX_IN_TURN_ADVANCES > 0
        # muito menor que um teto de tarefa típico: nunca monopoliza o turno.
        assert tool_module._MAX_IN_TURN_ADVANCES <= 100

    def test_autonomy_comes_from_settings_not_model(self, enable_agent, db_session, monkeypatch):
        """Tarefa criada pela tool usa `settings.computer_agent_autonomy`."""
        from app.computer_agent import tool as tool_module
        from app.computer_agent import store as ca_store_ref

        captured = {}

        class _SpyAgent:
            def __init__(self, *a, **k):
                pass

            def is_enabled(self):
                return True

            async def advance(self, db, provider, task, *, sink=None):
                captured["autonomy"] = task.autonomy.value
                return task.with_(status=ComputerStatus.COMPLETED,
                                  completed_at="2026-01-01T00:00:00+00:00")

        monkeypatch.setattr(tool_module, "ComputerAgent", _SpyAgent)
        monkeypatch.setattr(settings, "computer_agent_autonomy", "C2")
        ctx = _ctx(db_session)
        result = run_async(
            ComputerUseTool().run(ctx, goal="x",
                                  autonomy="C4", permission_level=3)
        )
        assert result.ok
        # A autonomia NUNCA veio dos argumentos do modelo.
        assert captured["autonomy"] == "C2"