"""Testes da Fase 4 — Permissions: aprovação interativa, política e auditoria."""

import json
from datetime import timedelta

from app.core.enums import PermissionLevel
from app.schemas.ai import ToolCall
from app.tools.base import Tool, ToolResult
from app.tools.registry import ToolRegistry


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


class NoteTool(Tool):
    """Ferramenta nível 2 com efeito observável: o aprovado grava memória."""

    name = "write_note"
    description = "grava uma nota persistente no banco"
    parameters = {
        "type": "object",
        "properties": {"content": {"type": "string"}},
        "required": ["content"],
    }
    permission_level = PermissionLevel.LEVEL_2

    async def run(self, context, **arguments):
        from app.services import memory as memory_service

        memory = await memory_service.create_memory(
            context.db,
            content=f"nota: {arguments.get('content', '')}",
            kind="note",
            session_id=context.session_id,
            provider=context.provider,
        )
        return ToolResult.success(f"nota registrada ({memory.id})")


def _use_note_tool(monkeypatch):
    registry = ToolRegistry()
    registry.register(NoteTool())
    from app.tools import registry as tool_registry_module

    monkeypatch.setattr(tool_registry_module, "get_tool_registry", lambda: registry)
    return registry


def _stream_post(client, url, payload):
    with client.stream("POST", url, json=payload) as res:
        assert res.status_code in (200, 202, 409, 410, 404)
        raw = b"".join(res.iter_bytes()).decode()
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    return res.status_code, [json.loads(ln[6:]) for ln in lines]


def test_approve_executes_tool_and_resumes(client, fake_ai, monkeypatch):
    _use_note_tool(monkeypatch)
    plan_call(fake_ai, ToolCall(name="write_note", arguments={"content": "pizza"}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "anote que gosto de pizza")
    approval = next(e for e in events if e["type"] == "approval_request")["approval"]
    assert events[-1]["type"] == "approval_pending"

    status, events = _stream_post(
        client, f"/api/approvals/{approval['id']}/respond", {"approved": True}
    )
    assert status == 200
    assert [e["type"] for e in events] == [
        "start",
        "tool_start",
        "tool_done",
        "chunk",
        "done",
    ]
    done = events[2]
    assert done["ok"] is True
    assert "nota registrada" in done["output"]
    assert events[-1]["message"]["content"] == "resposta fake"

    notes = [m for m in client.get("/api/memories").json() if m["content"] == "nota: pizza"]
    assert len(notes) == 1
    assert client.get(f"/api/approvals/pending?session_id={sid}").json() == []


def test_deny_feeds_refusal_and_does_not_execute(client, fake_ai, monkeypatch):
    _use_note_tool(monkeypatch)
    plan_call(fake_ai, ToolCall(name="write_note", arguments={"content": "pizza"}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "anote que gosto de pizza")
    approval = next(e for e in events if e["type"] == "approval_request")["approval"]

    status, events = _stream_post(
        client, f"/api/approvals/{approval['id']}/respond", {"approved": False}
    )
    assert status == 200
    types = [e["type"] for e in events]
    assert types[:3] == ["start", "tool_start", "tool_done"]
    assert events[2]["ok"] is False
    assert "negou" in events[2]["detail"]

    notes = [m for m in client.get("/api/memories").json() if m["content"] == "nota: pizza"]
    assert notes == []


def test_respond_twice_conflicts(client, fake_ai, monkeypatch):
    _use_note_tool(monkeypatch)
    plan_call(fake_ai, ToolCall(name="write_note", arguments={"content": "onça"}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "anote onça")
    approval = next(e for e in events if e["type"] == "approval_request")["approval"]

    status, _ = _stream_post(client, f"/api/approvals/{approval['id']}/respond", {"approved": True})
    assert status == 200
    status, _ = _stream_post(client, f"/api/approvals/{approval['id']}/respond", {"approved": True})
    assert status == 409


def test_respond_unknown_approval_404(client, fake_ai):
    status, _ = _stream_post(
        client, "/api/approvals/00000000-0000-0000-0000-000000000000/respond", {"approved": True}
    )
    assert status == 404


def test_respond_expired_410(client, fake_ai, monkeypatch):
    from app.db.session import SessionLocal
    from app.models import utcnow
    from app.services import approvals as approval_service

    _use_note_tool(monkeypatch)
    sid = create_session(client)["id"]
    db = SessionLocal()
    try:
        approval = approval_service.create_approval(
            db,
            session_id=sid,
            tool_name="write_note",
            arguments={"content": "x"},
            permission_level=2,
            risk="medium",
        )
        approval.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
        approval_id = approval.id
    finally:
        db.close()

    status, _ = _stream_post(client, f"/api/approvals/{approval_id}/respond", {"approved": True})
    assert status == 410


def test_agent_pending_list_filtered_by_session(client, fake_ai, monkeypatch):
    _use_note_tool(monkeypatch)
    plan_call(fake_ai, ToolCall(name="write_note", arguments={"content": "água"}))
    sid = create_session(client)["id"]
    send_agent(client, sid, "anote água")

    assert len(client.get("/api/approvals/pending").json()) == 1
    assert len(client.get(f"/api/approvals/pending?session_id={sid}").json()) == 1
    res = client.get("/api/approvals/pending?session_id=00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


def test_pending_list_excludes_expired(client, fake_ai, monkeypatch):
    """Fase 29 — aprovação expirada NÃO aparece como pendente (responder → 410)."""
    from app.db.session import SessionLocal
    from app.models import utcnow
    from app.services import approvals as approval_service

    _use_note_tool(monkeypatch)
    sid = create_session(client)["id"]
    db = SessionLocal()
    try:
        fresh = approval_service.create_approval(
            db,
            session_id=sid,
            tool_name="write_note",
            arguments={"content": "nova"},
            permission_level=2,
            risk="medium",
        )
        stale = approval_service.create_approval(
            db,
            session_id=sid,
            tool_name="write_note",
            arguments={"content": "velha"},
            permission_level=2,
            risk="medium",
        )
        stale.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
        fresh_id, stale_id = fresh.id, stale.id
    finally:
        db.close()

    ids = [a["id"] for a in client.get(f"/api/approvals/pending?session_id={sid}").json()]
    assert fresh_id in ids
    assert stale_id not in ids

    status, _ = _stream_post(client, f"/api/approvals/{stale_id}/respond", {"approved": True})
    assert status == 410


def test_audit_records_materialized_trace(client, fake_ai, monkeypatch):
    _use_note_tool(monkeypatch)
    plan_call(fake_ai, ToolCall(name="write_note", arguments={"content": "pedra"}))
    sid = create_session(client)["id"]
    send_agent(client, sid, "anote pedra")
    audit = client.get("/api/audit").json()

    actions = [e["action"] for e in audit]
    assert "tool.approve_requested" in actions

    executed = [e for e in audit if e["action"] == "tool.execute"]
    assert not executed  # ainda não foi aprovado


def test_permissions_defaults_and_overrides(client):
    perms = client.get("/api/permissions").json()
    store = next(p for p in perms if p["tool_name"] == "store_memory")
    assert store["permission_level"] == 1
    assert store["requires_confirmation"] is False
    assert store["source"] == "default"

    res = client.put("/api/permissions/store_memory", json={"permission_level": 2})
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "override"
    assert data["requires_confirmation"] is True

    after = client.get("/api/permissions").json()
    store = next(p for p in after if p["tool_name"] == "store_memory")
    assert store["permission_level"] == 2

    assert client.delete("/api/permissions/store_memory").status_code == 204
    reset = client.get("/api/permissions").json()
    store = next(p for p in reset if p["tool_name"] == "store_memory")
    assert store["source"] == "default"


def test_override_level2_forces_approval_for_store_memory(client, fake_ai):
    client.put("/api/permissions/store_memory", json={"permission_level": 2})
    plan_call(
        fake_ai,
        ToolCall(name="store_memory", arguments={"content": "segredo", "kind": "fact"}),
    )
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "guarde segredo")

    assert events[-1]["type"] == "approval_pending"
    req = next(e for e in events if e["type"] == "approval_request")["approval"]
    assert req["tool_name"] == "store_memory"
    mems = client.get("/api/memories").json()
    assert not any(m["content"] == "segredo" for m in mems)


def test_permission_level_validation(client):
    res = client.put("/api/permissions/store_memory", json={"permission_level": 9})
    assert res.status_code == 422


def test_permission_unknown_tool_404(client):
    res = client.put("/api/permissions/ferramenta_inexistente", json={"permission_level": 2})
    assert res.status_code == 404
    assert client.delete("/api/permissions/ferramenta_inexistente").status_code == 404