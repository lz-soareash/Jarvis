"""Testes da Fase 5 — Computer: ferramentas, níveis de permissão e API read-only."""

import json

import pytest

from app.computer import controller as computer_module
from app.schemas.ai import ToolCall


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


class FakeController:
    """Controlador determinístico — nunca toca no sistema real."""

    def __init__(self):
        self.killed: list[int] = []
        self.opened: list[str] = []
        self.processes = [
            {"pid": 4, "name": "System", "mem_bytes": 16777216, "created_at": None},
            {"pid": 308, "name": "svchost.exe", "mem_bytes": 8192000, "created_at": None},
            {"pid": 812, "name": "notepad.exe", "mem_bytes": 2621440, "created_at": None},
        ]
        self.stats = {
            "cpu_percent": 23.5,
            "memory_total": 17179869184,
            "memory_used": 6442450944,
            "disk_total": 512110190592,
            "disk_free": 214748364800,
            "boot_time": "2026-08-29T08:00:00Z",
        }

    def system_stats(self):
        return dict(self.stats)

    def list_processes(self, limit=50):
        return self.processes[:limit]

    def open_app(self, target):
        self.opened.append(target)
        return f"App iniciado: {target}"

    def kill_process(self, pid):
        pid = int(pid)
        self.killed.append(pid)
        return f"Processo {pid} encerrado"


@pytest.fixture
def fake_computer(monkeypatch):
    from app.main import app

    controller = FakeController()
    original = computer_module.get_system_controller  # função original (chave do override)
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)
    app.dependency_overrides[original] = lambda: controller
    yield controller
    app.dependency_overrides.pop(original, None)


def _tool_level(client, name):
    perms = client.get("/api/permissions").json()
    return next(p for p in perms if p["tool_name"] == name)


def test_computer_tools_registered_with_levels(client, fake_computer):
    assert _tool_level(client, "get_system_stats")["permission_level"] == 0
    assert _tool_level(client, "list_processes")["permission_level"] == 0

    open_app = _tool_level(client, "open_app")
    assert open_app["permission_level"] == 1
    assert open_app["risk"] == "medium"
    assert open_app["requires_confirmation"] is False

    kill = _tool_level(client, "kill_process")
    assert kill["permission_level"] == 3
    assert kill["risk"] == "high"
    assert kill["requires_confirmation"] is True
    assert kill["blocked_by_default"] is True


def test_agent_list_processes_runs(client, fake_ai, fake_computer):
    plan_call(fake_ai, ToolCall(name="list_processes", arguments={"limit": 10}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "quais processos estão rodando?")
    done = next(e for e in events if e["type"] == "tool_done")
    assert done["ok"] is True
    assert "notepad.exe" in done["output"]
    assert "svchost.exe" in done["output"]


def test_agent_open_app_level1_auto(client, fake_ai, fake_computer):
    plan_call(fake_ai, ToolCall(name="open_app", arguments={"target": "notepad.exe"}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "abra o bloco de notas")
    done = next(e for e in events if e["type"] == "tool_done")
    assert done["ok"] is True
    assert fake_computer.opened == ["notepad.exe"]


def test_agent_kill_process_level3_requests_approval_and_executes(client, fake_ai, fake_computer):
    plan_call(fake_ai, ToolCall(name="kill_process", arguments={"pid": 812}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "mate o processo 812")
    assert events[-1]["type"] == "approval_pending"
    approval = next(e for e in events if e["type"] == "approval_request")["approval"]
    assert approval["tool_name"] == "kill_process"
    assert approval["risk"] == "high"
    assert fake_computer.killed == []

    with client.stream(
        "POST", f"/api/approvals/{approval['id']}/respond", json={"approved": True}
    ) as res:
        assert res.status_code == 200
        raw = b"".join(res.iter_bytes()).decode()
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    resume = [json.loads(ln[6:]) for ln in lines]
    assert [e["type"] for e in resume] == ["start", "tool_start", "tool_done", "chunk", "done"]
    assert resume[2]["ok"] is True
    assert fake_computer.killed == [812]


def test_agent_kill_process_denied_does_not_kill(client, fake_ai, fake_computer):
    plan_call(fake_ai, ToolCall(name="kill_process", arguments={"pid": 812}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "mate o processo 812")
    approval = next(e for e in events if e["type"] == "approval_request")["approval"]

    with client.stream(
        "POST", f"/api/approvals/{approval['id']}/respond", json={"approved": False}
    ) as res:
        raw = b"".join(res.iter_bytes()).decode()
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    resume = [json.loads(ln[6:]) for ln in lines]
    assert resume[2]["ok"] is False
    assert "negou" in resume[2]["detail"]
    assert fake_computer.killed == []


def test_system_stats_endpoint(client, fake_computer):
    data = client.get("/api/system/stats").json()
    assert data == fake_computer.stats


def test_system_processes_endpoint(client, fake_computer):
    data = client.get("/api/system/processes", params={"limit": 2}).json()
    assert data == fake_computer.processes[:2]
    assert client.get("/api/system/processes", params={"limit": 200}).status_code == 200