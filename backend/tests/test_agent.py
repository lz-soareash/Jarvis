"""Testes do Tool Engine (Fase 3): loop do agente, política e integração com memória."""

import json

import app.services.agent as agent_module
from app.core.enums import PermissionLevel
from app.schemas.ai import ToolCall
from app.tools.base import Tool, ToolResult
from app.tools.registry import ToolRegistry


def create_session(client):
    res = client.post("/api/sessions", json={})
    assert res.status_code == 201
    return res.json()


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


def plan_call(fake_ai, *calls):
    fake_ai._planned_tool_calls = list(calls)


def test_agent_executes_tool_then_answers(client, fake_ai):
    plan_call(fake_ai, ToolCall(name="get_current_time", arguments={}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "que horas são?")
    assert [e["type"] for e in events] == ["start", "tool_start", "tool_done", "chunk", "done"]

    done = next(e for e in events if e["type"] == "tool_done")
    assert done["name"] == "get_current_time"
    assert done["ok"] is True

    assert events[-1]["message"]["content"] == "resposta fake"
    assert fake_ai.generate_calls == 2

    messages = client.get(f"/api/sessions/{sid}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == "resposta fake"


def test_agent_injects_tools_in_first_generation(client, fake_ai):
    sid = create_session(client)["id"]
    send_agent(client, sid, "oi")
    tools = fake_ai.last_generate_kwargs["tools"]
    assert tools and any(t.name == "get_current_time" for t in tools)
    assert any(t.name == "store_memory" for t in tools)


def test_agent_refuses_unknown_tool(client, fake_ai):
    plan_call(fake_ai, ToolCall(name="executar_qualquer_comando", arguments={}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "rode isto")
    done = next(e for e in events if e["type"] == "tool_done")
    assert done["ok"] is False
    assert "não está registrada" in done["detail"]
    assert events[-1]["type"] == "done"


class DangerTool(Tool):
    name = "apagar_disco"
    description = "apaga o disco inteiro"
    parameters = {}
    permission_level = PermissionLevel.LEVEL_2

    async def run(self, context, **arguments):
        return ToolResult.success("apagado")


def test_agent_blocks_level2_tool(client, fake_ai, monkeypatch):
    registry = ToolRegistry()
    registry.register(DangerTool())
    monkeypatch.setattr(agent_module, "get_tool_registry", lambda: registry)

    plan_call(fake_ai, ToolCall(name="apagar_disco", arguments={}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "apague o disco")
    done = next(e for e in events if e["type"] == "tool_done")
    assert done["ok"] is False
    assert "Fase 4" in done["detail"]


def test_agent_unconfigured_emits_error_event(client):
    # Sem override -> provider default sem chave (conftest).
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "oi")
    assert [e["type"] for e in events] == ["start", "error"]
    assert "GEMINI_API_KEY" in events[-1]["detail"]


def test_store_memory_tool_persists_memory(client, fake_ai):
    plan_call(
        fake_ai,
        ToolCall(name="store_memory", arguments={"content": "estudante de python", "kind": "fact"}),
    )
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "guarde que sou estudante de python")
    done = next(e for e in events if e["type"] == "tool_done")
    assert done["ok"] is True

    memories = [
        m for m in client.get("/api/memories").json() if m["content"] == "estudante de python"
    ]
    assert memories


def test_recall_memory_tool_returns_relevant(client, fake_ai):
    client.post("/api/memories", json={"content": "gosto de café", "kind": "preference"})
    plan_call(fake_ai, ToolCall(name="recall_memory", arguments={"query": "café"}))
    sid = create_session(client)["id"]
    events = send_agent(client, sid, "o que lembro sobre café?")
    done = next(e for e in events if e["type"] == "tool_done")
    assert done["ok"] is True
    assert "gosto de café" in done["output"]