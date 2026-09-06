"""Testes da Fase 13 — Agentic Core & Workspace (tarefas agênticas).

Cobre o pipeline TASK→PLAN→EXECUTE→OBSERVE→VERIFY→RECOVER→COMPLETION:
planner determinístico, persistência, execução L0 automática, pausa por
aprovação (nível ≥ 2) com retomada, recuperação com retry e falha crítica.
Nenhum teste toca o sistema de arquivos/computador reais (fakes injetados).
"""

import asyncio
import json

import pytest

from app.core.enums import StepStatus, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeFS:
    """Controlador de arquivos determinístico (nunca toca o sistema real)."""

    def __init__(self, fail_once: bool = False):
        self.fail_once = fail_once
        self.calls = 0
        self.tree = {
            "": {"entries": [{"name": "notas.txt", "type": "file", "size": 12, "modified": "01/01/2026"}]}
        }

    def list_dir(self, path="", limit=50):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RuntimeError("falha de leitura simulada")
        return {"path": path or ".", "total": 1, "entries": self.tree[""]["entries"], "truncated": False}


class _FakeComputer:
    """Controlador de computador determinístico (allowlist segura)."""

    def __init__(self, fail_command: bool = False):
        self.fail_command = fail_command
        self.commands: list[str] = []

    def run_allowed_command(self, command):
        from app.computer.controller import SystemControllerError

        self.commands.append(command)
        if self.fail_command:
            raise SystemControllerError("comando falhou na inspeção")
        return "informações do sistema (tasklist)"


@pytest.fixture
def agent_runtime(monkeypatch):
    """Isola filesystem/computer reais: tudo determinístico nos testes."""
    from app.computer import controller as computer_module
    from app.filesystem import controller as fs_module

    fs = _FakeFS()
    comp = _FakeComputer()
    monkeypatch.setattr(fs_module, "get_filesystem_controller", lambda: fs)
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: comp)
    return fs, comp


def _make_session(db):
    from app.services import chat as chat_service

    return chat_service.create_session(db)


# ---------------------------------------------------------------------------
# PLAN — planner determinístico
# ---------------------------------------------------------------------------

def test_plan_for_classifies_analyze_default():
    from app.services.agent_core import plan_for

    steps = plan_for("analise o projeto e me diga o que há aqui")
    assert steps and steps[0]["tool"] == "list_dir"
    assert steps[0]["guard"] == "skip"
    assert all(s["verify"] for s in steps)


def test_plan_for_tests_uses_l2_command():
    from app.services.agent_core import plan_for

    steps = plan_for("rode os testes do projeto")
    assert any(s["tool"] == "run_allowed_command" for s in steps)
    assert next(s for s in steps if s["tool"] == "run_allowed_command")["guard"] == "stop"


def test_plan_for_code_starts_read_only(client, agent_runtime):
    from app.services.agent_core import plan_for

    steps = plan_for("corrija o bug no login")
    tools = [s["tool"] for s in steps]
    assert tools[0] == "list_dir"
    assert "dev_diagnostics" in tools


def test_plan_for_respects_max_steps():
    from app.core.config import settings
    from app.services.agent_core import plan_for

    steps = plan_for("analise o projeto e rode os testes")
    assert len(steps) <= settings.agent_core_max_steps


# ---------------------------------------------------------------------------
# VERIFY
# ---------------------------------------------------------------------------

def test_verify_step_exit_ok():
    from app.services.agent_core import verify_step
    from app.tools import ToolResult

    assert verify_step({"verify": {"kind": "exit_ok"}}, ToolResult.success("ok")) is True
    assert verify_step({"verify": {"kind": "exit_ok"}}, ToolResult.failure("boom")) is False


def test_verify_step_contains():
    from app.services.agent_core import verify_step
    from app.tools import ToolResult

    step = {"verify": {"kind": "contains", "expect": "info"}}
    assert verify_step(step, ToolResult.success("informações do sistema")) is True
    assert verify_step(step, ToolResult.success("só texto")) is False


# ---------------------------------------------------------------------------
# TASK — persistência e observabilidade
# ---------------------------------------------------------------------------

def test_create_task_persists_and_records_ops(client):
    from app.db.session import SessionLocal
    from app.services import ops as ops_service
    from app.services.agent_core import create_task, get_task, list_tasks

    with SessionLocal() as db:
        session = _make_session(db)
        task = create_task(db, session.id, "analise o projeto")
        assert task.status == TaskStatus.PLANNED.value
        assert get_task(db, task.id) is not None
        assert [t.id for t in list_tasks(db, session.id)] == [task.id]

        events = ops_service.list_events(db, event_type=ops_service.EVENT_TASK_STARTED)
        assert any(task.id in (e.meta_json or "") for e in events)


# ---------------------------------------------------------------------------
# EXECUTE → OBSERVE → VERIFY → COMPLETION (nível 0 automático)
# ---------------------------------------------------------------------------

def _parse(items):
    parsed = []
    for item in items:
        if isinstance(item, str) and item.startswith("data:"):
            try:
                parsed.append(json.loads(item.removeprefix("data:").strip()))
            except ValueError:
                continue
        else:
            parsed.append(item)
    return parsed


async def _collect_all(agen):
    return [e async for e in agen]


def _iter_sync(agen):
    """Itera um async generator num loop privado (não envenena o loop da thread)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_collect_all(agen))
    finally:
        loop.close()


def test_run_agent_task_level0_completes(client, agent_runtime, fake_ai):
    from app.db.session import SessionLocal
    from app.services import audit as audit_service
    from app.services.agent_core import get_task, run_agent_task

    with SessionLocal() as db:
        session = _make_session(db)
        raw = _iter_sync(run_agent_task(db, session.id, fake_ai, "analise o projeto"))
        events = _parse(raw)
        task = get_task(db, events[0]["task"]["id"])

        done = [e for e in events if e["type"] == "agent_task_done"]
        assert done and done[0]["status"] == TaskStatus.COMPLETED.value
        assert task.status == TaskStatus.COMPLETED.value
        assert task.steps_done == 2

        tools = [e["name"] for e in events if e["type"] == "tool_done"]
        assert "list_dir" in tools and "dev_diagnostics" in tools

        logs = audit_service.list_audit(db)
        assert any(a.action == "tool.execute" and a.tool == "list_dir" for a in logs)


def test_run_agent_task_skips_failed_step_without_failing(client, agent_runtime, fake_ai, monkeypatch):
    """Falha recuperável (guard=skip) não derruba a tarefa (RECOVER via skip)."""

    def _flaky(self, path="", limit=50):
        raise RuntimeError("falha de leitura simulada")

    from app.filesystem import controller as fs_module

    monkeypatch.setattr(fs_module.get_filesystem_controller().__class__, "list_dir", _flaky)

    from app.db.session import SessionLocal
    from app.services.agent_core import get_task, run_agent_task

    with SessionLocal() as db:
        session = _make_session(db)
        events = _parse(_iter_sync(run_agent_task(db, session.id, fake_ai, "analise o projeto")))
        done = next(e for e in events if e["type"] == "agent_task_done")
        assert done["status"] == TaskStatus.COMPLETED.value
        task = get_task(db, done["task_id"])
        assert task.progress[0]["status"] == StepStatus.SKIPPED.value


# ---------------------------------------------------------------------------
# RECOVER — retry dentro do limite
# ---------------------------------------------------------------------------

def test_run_agent_task_retries_then_succeeds(client, agent_runtime, fake_ai, monkeypatch):
    from app.filesystem import controller as fs_module

    flaky = _FakeFS(fail_once=True)
    monkeypatch.setattr(fs_module, "get_filesystem_controller", lambda: flaky)

    from app.db.session import SessionLocal
    from app.services.agent_core import get_task, run_agent_task

    plan = [{"description": "ler estrutura", "tool": "list_dir", "arguments": {}, "verify": {"kind": "exit_ok"}, "guard": "retry"}]
    with SessionLocal() as db:
        session = _make_session(db)
        events = _parse(_iter_sync(run_agent_task(db, session.id, fake_ai, "analise", plan=plan)))
        assert flaky.calls == 2  # tentativa inicial + 1 retry
        done = next(e for e in events if e["type"] == "agent_task_done")
        assert done["status"] == TaskStatus.COMPLETED.value
        task = get_task(db, done["task_id"])
        assert task.progress[0]["status"] == StepStatus.DONE.value
        assert task.progress[0]["attempts"] == 2

        assert flaky.calls <= 3  # nunca estoura o limite


# ---------------------------------------------------------------------------
# APROVAÇÃO — pausa (nível ≥ 2) e retomada (ápice do fluxo agêntico)
# ---------------------------------------------------------------------------

def _send_task(client, session_id, objective):
    with client.stream(
        "POST",
        f"/api/workspace/tasks?session_id={session_id}",
        json={"objective": objective},
    ) as res:
        assert res.status_code == 200
        raw = b"".join(res.iter_bytes()).decode()
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    return [json.loads(ln[6:]) for ln in lines]


def test_task_pauses_on_level2_and_resumes_on_approve(client, agent_runtime, fake_ai):
    from app.db.session import SessionLocal
    from app.models import ApprovalRequest
    from app.services.agent_core import get_task, list_tasks

    sid = client.post("/api/sessions", json={}).json()["id"]

    events = _send_task(client, sid, "rode os testes do projeto")
    # O passo run_allowed_command (L2) pausa a tarefa; nada executa ainda.
    assert any(e["type"] == "approval_request" for e in events)
    assert any(e["type"] == "approval_pending" for e in events)
    assert not any(e["type"] == "agent_task_done" for e in events)
    assert not any(e["type"] == "done" for e in events)

    with SessionLocal() as db:
        task = list_tasks(db, sid)[0]
        assert task.approval_id is not None
        approval = db.get(ApprovalRequest, task.approval_id)
        assert approval is not None and approval.status == "pending"
        approval_id = approval.id

    with client.stream(
        "POST",
        f"/api/approvals/{approval_id}/respond",
        json={"approved": True},
    ) as res:
        assert res.status_code == 200
        raw = b"".join(res.iter_bytes()).decode()
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    resumed = [json.loads(ln[6:]) for ln in lines]

    # Retomada: executa o passo aprovado (cmd seguro) e segue até COMPLETION.
    done = next(e for e in resumed if e["type"] == "agent_task_done")
    assert done["status"] == TaskStatus.COMPLETED.value
    tool_ok = [e for e in resumed if e["type"] == "tool_done" and e.get("ok")]
    assert tool_ok and tool_ok[0]["name"] == "run_allowed_command"

    with SessionLocal() as db:
        task = get_task(db, done["task_id"])
        assert task.status == TaskStatus.COMPLETED.value
        assert task.approval_id is None  # pendência limpa após retomada


def test_task_denied_skips_step_and_completes(client, agent_runtime, fake_ai):
    from app.db.session import SessionLocal
    from app.models import ApprovalRequest as AR
    from app.services.agent_core import get_task, list_tasks

    sid = client.post("/api/sessions", json={}).json()["id"]
    _send_task(client, sid, "rode os testes do projeto")

    with SessionLocal() as db:
        task = list_tasks(db, sid)[0]
        approval = db.get(AR, task.approval_id)
        approval_id = approval.id

    with client.stream(
        "POST",
        f"/api/approvals/{approval_id}/respond",
        json={"approved": False},
    ) as res:
        assert res.status_code == 200
        raw = b"".join(res.iter_bytes()).decode()
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    resumed = [json.loads(ln[6:]) for ln in lines]

    done = next(e for e in resumed if e["type"] == "agent_task_done")
    assert done["status"] == TaskStatus.COMPLETED.value
    with SessionLocal() as db:
        task = get_task(db, done["task_id"])
        # passo negado ficou skipped; tarefa concluiu sem executar o comando.
        denied_idx = next(
            i for i, s in enumerate(task.plan) if s["tool"] == "run_allowed_command"
        )
        assert task.progress[denied_idx]["status"] == StepStatus.SKIPPED.value


# ---------------------------------------------------------------------------
# Workspace API — gestão de tarefas
# ---------------------------------------------------------------------------

def test_workspace_api_crud_and_cancel(client, agent_runtime, fake_ai):
    sid = client.post("/api/sessions", json={}).json()["id"]

    # Create-run (objetivo que pausa por aprovação para não bloquear o delete).
    events = _send_task(client, sid, "rode os testes do projeto")
    task_id = events[0]["task"]["id"]

    res = client.get(f"/api/workspace/tasks/{task_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == TaskStatus.RUNNING.value
    assert body["steps_total"] >= 2

    scheduled_plan = body["plan"]
    assert any(s["tool"] == "run_allowed_command" for s in scheduled_plan)

    res = client.get("/api/workspace/tasks", params={"session_id": sid})
    assert len(res.json()) == 1

    # Cancelar uma tarefa pausada (não-terminal) funciona; terminal é no-op.
    res = client.post(f"/api/workspace/tasks/{task_id}/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == TaskStatus.CANCELLED.value

    res = client.delete(f"/api/workspace/tasks/{task_id}")
    assert res.status_code == 204

    res = client.get(f"/api/workspace/tasks/{task_id}")
    assert res.status_code == 404


def test_workspace_api_validates_missing_session(client, agent_runtime, fake_ai):
    with client.stream(
        "POST",
        "/api/workspace/tasks?session_id=inexistente",
        json={"objective": "analise"},
    ) as res:
        assert res.status_code == 404