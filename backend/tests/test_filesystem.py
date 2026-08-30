"""Testes da Fase 7 — Filesystem: controlador (sandbox), ferramentas e permissões."""

import json
from pathlib import Path

import pytest

from app.filesystem import controller as fs_module
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


def _tool_level(client, name):
    perms = client.get("/api/permissions").json()
    return next(p for p in perms if p["tool_name"] == name)


# ------------------------------------------------------------------------ controlador
def test_controller_resolves_inside_root(client, tmp_path):
    c = fs_module.FileSystemController(root=tmp_path)
    (tmp_path / "notas.txt").write_text("oi", encoding="utf-8")
    out = c.read_file("notas.txt")
    assert out["content"] == "oi"


def test_controller_rejects_escape(client, tmp_path):
    c = fs_module.FileSystemController(root=tmp_path)
    with pytest.raises(fs_module.FileSystemError):
        c.read_file("../outside.txt")
    with pytest.raises(fs_module.FileSystemError):
        c.read_file("C:/Windows/win.ini")
    with pytest.raises(fs_module.FileSystemError):
        c.read_file("/etc/passwd")


def test_controller_write_and_read_roundtrip(client, tmp_path):
    c = fs_module.FileSystemController(root=tmp_path)
    c.write_file("pasta/arquivo.txt", "conteúdo")
    assert (tmp_path / "pasta" / "arquivo.txt").exists()
    assert c.read_file("pasta/arquivo.txt")["content"] == "conteúdo"


def test_controller_list_dir(client, tmp_path):
    c = fs_module.FileSystemController(root=tmp_path)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b").mkdir()
    data = c.list_dir(".")
    names = [e["name"] for e in data["entries"]]
    assert "a.txt" in names
    assert "b" in names
    assert next(e for e in data["entries"] if e["name"] == "b")["type"] == "dir"


def test_controller_delete_file(client, tmp_path):
    c = fs_module.FileSystemController(root=tmp_path)
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    c.delete_path("x.txt")
    assert not (tmp_path / "x.txt").exists()


def test_controller_delete_dir_needs_recursive(client, tmp_path):
    c = fs_module.FileSystemController(root=tmp_path)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.txt").write_text("x", encoding="utf-8")
    with pytest.raises(fs_module.FileSystemError):
        c.delete_path("d")
    c.delete_path("d", recursive=True)
    assert not (tmp_path / "d").exists()


def test_controller_cannot_delete_root(client, tmp_path):
    c = fs_module.FileSystemController(root=tmp_path)
    with pytest.raises(fs_module.FileSystemError):
        c.delete_path("")


# ------------------------------------------------------------------- permissões
def test_filesystem_tools_registered_with_levels(client):
    list_dir = _tool_level(client, "list_dir")
    assert list_dir["permission_level"] == 0
    assert list_dir["requires_confirmation"] is False

    read_file = _tool_level(client, "read_file")
    assert read_file["permission_level"] == 0

    write = _tool_level(client, "write_file")
    assert write["permission_level"] == 2
    assert write["risk"] == "medium"
    assert write["requires_confirmation"] is True

    delete = _tool_level(client, "delete_path")
    assert delete["permission_level"] == 3
    assert delete["risk"] == "high"
    assert delete["blocked_by_default"] is True


# -------------------------------------------------------------- agent (fake fs)
class FakeFS:
    """Controlador determinístico — nunca toca no sistema real."""

    def __init__(self):
        self.written: list[tuple] = []
        self.deleted: list[str] = []
        self.tree = {
            "": {"entries": [{"name": "notas.txt", "type": "file", "size": 12, "modified": "01/01/2026 10:00"}]}
        }
        self.contents = {"notas.txt": {"content": "primeira linha\nsegunda linha", "path": "/notas.txt", "truncated": False}}

    def list_dir(self, path="", limit=50):
        return {"path": path or ".", "total": 1, "entries": self.tree[""]["entries"], "truncated": False}

    def read_file(self, path, limit=2000):
        return self.contents[path]

    def write_file(self, path, content, create_dirs=True):
        self.written.append((path, content))
        return f"Arquivo gravado: {path}"

    def make_dir(self, path):
        return f"Pasta criada: {path}"

    def delete_path(self, path, recursive=False):
        self.deleted.append(path)
        return f"Apagado: {path}"


@pytest.fixture
def fake_fs(monkeypatch, tmp_path):
    from app.main import app

    controller = FakeFS()
    original = fs_module.get_filesystem_controller
    monkeypatch.setattr(fs_module, "get_filesystem_controller", lambda: controller)
    yield controller


def test_agent_list_dir_runs(client, fake_ai, fake_fs):
    plan_call(fake_ai, ToolCall(name="list_dir", arguments={}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "o que tem na minha pasta?")
    done = next(e for e in events if e["type"] == "tool_done")
    assert done["ok"] is True
    assert "notas.txt" in done["output"]


def test_agent_read_file_runs(client, fake_ai, fake_fs):
    plan_call(fake_ai, ToolCall(name="read_file", arguments={"path": "notas.txt"}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "leia o notas.txt")
    done = next(e for e in events if e["type"] == "tool_done")
    assert done["ok"] is True
    assert "primeira linha" in done["output"]


def test_agent_write_file_level2_requests_approval_and_executes(client, fake_ai, fake_fs):
    plan_call(fake_ai, ToolCall(name="write_file", arguments={"path": "lista.txt", "content": "item 1"}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "salve uma lista com item 1")
    assert events[-1]["type"] == "approval_pending"
    approval = next(e for e in events if e["type"] == "approval_request")["approval"]
    assert approval["tool_name"] == "write_file"
    assert approval["risk"] == "medium"
    assert fake_fs.written == []

    with client.stream(
        "POST", f"/api/approvals/{approval['id']}/respond", json={"approved": True}
    ) as res:
        assert res.status_code == 200
        raw = b"".join(res.iter_bytes()).decode()
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    resume = [json.loads(ln[6:]) for ln in lines]
    assert [e["type"] for e in resume] == ["start", "tool_start", "tool_done", "chunk", "done"]
    assert resume[2]["ok"] is True
    assert fake_fs.written == [("lista.txt", "item 1")]


def test_agent_delete_path_level3_denied_does_not_delete(client, fake_ai, fake_fs):
    plan_call(fake_ai, ToolCall(name="delete_path", arguments={"path": "notas.txt"}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "apague o notas.txt")
    approval = next(e for e in events if e["type"] == "approval_request")["approval"]

    with client.stream(
        "POST", f"/api/approvals/{approval['id']}/respond", json={"approved": False}
    ) as res:
        raw = b"".join(res.iter_bytes()).decode()
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    resume = [json.loads(ln[6:]) for ln in lines]
    assert resume[2]["ok"] is False
    assert "negou" in resume[2]["detail"]
    assert fake_fs.deleted == []
