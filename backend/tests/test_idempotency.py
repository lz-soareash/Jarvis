"""Testes da Fase 11.3 (#2/#3/#4 — idempotência, fallback seguro, intent primária).

Cobrem:
- deduplicação determinística de tool calls DENTRO do mesmo turno;
- repetição PERMITIDA entre turnos (histórico informa, não bloqueia);
- persistência de `tools_executed` no metadata da resposta final;
- reconstrução de mensagens tool a partir do metadata em build_ai_messages;
- intent primária executando operação conhecida sem depender de tool_call do LLM;
- `_sse_with_fallback` NÃO recorrendo a outro provider após tool executada ok.
"""

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


# ------------------------------------------------------------------- fixtures

class FakeController:
    def __init__(self):
        self.opened: list[str] = []
        self.processes = [
            {"pid": 4, "name": "System", "mem_bytes": 16777216, "created_at": None},
            {"pid": 812, "name": "notepad.exe", "mem_bytes": 2621440, "created_at": None},
        ]
        self.stats = {"cpu_percent": 10.0}

    def system_stats(self):
        return dict(self.stats)

    def list_processes(self, limit=50):
        return self.processes[:limit]

    def open_app(self, target):
        self.opened.append(target)
        return f"App iniciado: {target}"


@pytest.fixture
def fake_computer(monkeypatch):
    controller = FakeController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)
    return controller


# ------------------------------------------------- #2 — idempotência no turno

class TestDedupSameTurn:
    async def test_duplicate_blocked_same_turn(self, client, fake_computer):
        from app.db.session import SessionLocal
        from app.services import agent

        session = create_session(client)
        db = SessionLocal()
        try:
            executed: dict[str, str] = {}
            executions: list[dict] = []
            call = ToolCall(name="open_application", arguments={"target": "notepad.exe"})
            results, pending = await agent._execute_tool_calls(
                db, session["id"], None, [call, call],
                executed_this_turn=executed,
                executions=executions,
            )
            assert len(pending) == 0
            assert len(results) == 2
            assert results[0].ok is True
            assert results[1].ok is False  # duplicata bloqueada
            assert fake_computer.opened == ["notepad.exe"]  # 1 execução apenas
            assert len(executed) == 1
            assert len(executions) == 1
        finally:
            db.close()

    async def test_repeat_allowed_across_turns(self, client, fake_computer, fake_ai):
        """Mesma tool em turnos separados: NÃO é bloqueada (usuário pode reabrir)."""
        from app.db.session import SessionLocal
        from app.services import agent

        session = create_session(client)
        db = SessionLocal()
        try:
            executed: dict[str, str] = {}
            executions: list[dict] = []
            call = ToolCall(name="open_application", arguments={"target": "notepad.exe"})
            # Turno 1
            r1, _ = await agent._execute_tool_calls(
                db, session["id"], None, [call],
                executed_this_turn=executed, executions=executions,
            )
            assert r1[0].ok is True
            # Turno 2 (novo dict — novo turno)
            r2, _ = await agent._execute_tool_calls(
                db, session["id"], None, [call],
                executed_this_turn={}, executions=list(executions),
            )
            assert r2[0].ok is True
            assert fake_computer.opened == ["notepad.exe", "notepad.exe"]
        finally:
            db.close()


# ----------------------------------------------- #2 — persistência e histórico

def test_run_agent_persists_tools_executed_metadata(client, fake_computer, fake_ai):
    session = create_session(client)
    plan_call(
        fake_ai,
        ToolCall(name="open_application", arguments={"target": "notepad.exe"}),
    )
    events = send_agent(client, session["id"], "abra o bloco de notas")
    assert any(e["type"] == "tool_done" and e["ok"] is True for e in events)

    from app.models import Message
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = db.query(Message).filter(
            Message.session_id == session["id"], Message.role == "assistant"
        ).all()
        assert any(row.metadata_json for row in rows)  # metadata presente
        metas = [json.loads(r.metadata_json) for r in rows if r.metadata_json]
        assert any("tools_executed" in m for m in metas)
        executed = next(m["tools_executed"] for m in metas if "tools_executed" in m)
        assert any(
            e["tool"] == "open_application"
            and e["arguments"].get("target") == "notepad.exe"
            and e["ok"] is True
            for e in executed
        )
    finally:
        db.close()


def test_build_ai_messages_reconstructs_tool_messages(client, fake_computer, fake_ai):
    session = create_session(client)
    plan_call(
        fake_ai,
        ToolCall(name="open_application", arguments={"target": "notepad.exe"}),
    )
    send_agent(client, session["id"], "abra o bloco de notas")

    from app.db.session import SessionLocal
    from app.services.chat import build_ai_messages

    db = SessionLocal()
    try:
        ai_messages = build_ai_messages(db, session["id"])
        roles = [m.role for m in ai_messages]
        # Fase 27 — execuções persistidas viram resumo TEXTUAL do histórico (não
        # function_call fabricada): o Gemini exige thought_signature em chamadas
        # originais do modelo; chamadas determinísticas/reconstruídas não têm —
        # fabricá-las causava erro 400 no Gemini thinking. O resultado (nome da
        # ferramenta + output) continua disponível como contexto para o modelo.
        assert "tool" not in roles
        user_contents = " ".join(
            m.content or "" for m in ai_messages if m.role == "user"
        )
        assert "open_application" in user_contents
        assert "notepad" in user_contents
    finally:
        db.close()


# ------------------------------------------------------------- #4 — intent primária

def test_intent_primary_executes_without_llm_tool_call(client, fake_computer, fake_ai):
    """Texto operacional claro roda via Intent primária (sem tool_call do LLM)."""
    session = create_session(client)
    # O LLM NÃO planeja tool_call — responde por texto. A intent primária deve
    # executar open_application antes do loop.
    plan_call(fake_ai)  # nenhuma tool planejada
    events = send_agent(client, session["id"], "abra o bloco de notas")
    assert any(e["type"] == "tool_start" for e in events)
    assert any(e["type"] == "tool_done" and e["ok"] is True for e in events)
    assert fake_computer.opened, "intent primária deveria ter aberto o app"
    assert any("notepad" in str(o) for o in fake_computer.opened)


def test_intent_primary_still_answers_text(client, fake_computer, fake_ai):
    session = create_session(client)
    plan_call(fake_ai)
    events = send_agent(client, session["id"], "abra o bloco de notas")
    chunks = "".join(e.get("text", "") for e in events if e["type"] == "chunk")
    assert chunks  # mesmo após tool, o LLM gera a resposta final


# ------------------------------------------------- #3 — fallback seguro (sem re-execução)

class _Router:
    def __init__(self, fallback):
        self._fallback = fallback

    def fallback_for(self, provider, task="generate"):
        return self._fallback


def _flat_sse(*payloads):
    return ["data: " + json.dumps(p) for p in payloads]


async def test_fallback_suppressed_after_tool_executed():
    """Após tool_done ok, erro NÃO dispara fallback (evita re-execução)."""
    from app.ai.core import _sse_with_fallback
    from app.db.session import SessionLocal

    class Primary:
        name = "local"

    class Fallback:
        name = "gemini"
        calls = 0

        async def generate(self, messages, **kwargs):
            Fallback.calls += 1
            return None

    fallback = Fallback()

    async def make_generator(_provider):
        for item in _flat_sse(
            {"type": "tool_done", "name": "open_application", "ok": True},
            {"type": "error", "detail": "falha pós-tool"},
        ):
            yield item

    db = SessionLocal()
    try:
        items = []
        async for item in _sse_with_fallback(
            make_generator,
            db,
            session_id=None,
            path="/test",
            provider=Primary(),
            router=_Router(fallback),
        ):
            items.append(item)
        # Erro emitido, mas fallback NÃO chamado.
        assert fallback.calls == 0
        assert any('"error"' in i for i in items)
    finally:
        db.close()


async def test_fallback_triggered_without_tool_execution():
    """Sem tool executada, erro dispara fallback normalmente."""
    from app.ai.core import _sse_with_fallback
    from app.db.session import SessionLocal

    class Primary:
        name = "local"

        async def generate(self, messages, **kwargs):
            return None

    class Fallback:
        name = "gemini"

        async def generate(self, messages, **kwargs):
            return None

    called_with = []

    async def make_generator(prov):
        called_with.append(prov.name)
        if prov.name == "local":
            for item in _flat_sse({"type": "error", "detail": "falha inicial"}):
                yield item
        else:
            for item in _flat_sse({"type": "chunk", "text": "ok"}) + _flat_sse(
                {"type": "done"}
            ):
                yield item

    db = SessionLocal()
    try:
        items = []
        async for item in _sse_with_fallback(
            make_generator,
            db,
            session_id=None,
            path="/test",
            provider=Primary(),
            router=_Router(Fallback()),
        ):
            items.append(item)
        assert called_with == ["local", "gemini"]  # fallback foi alcançado
    finally:
        db.close()


# ------------------------------------------------------------------ offline

def test_offline_request_does_not_depend_on_gemini(client, fake_computer, monkeypatch):
    """Operação computacional conhecida resolve offline via intent primária
    (sem Gemini — o router usa local/deterministic; nenhuma key/API necessária)."""
    # Garante política LOCAL_FIRST ativa (padrão já é True).
    from app.core.config import settings

    assert settings.local_first is True

    # Sem override de get_ai_provider (router real: local -> deterministic).
    # A intent primária detecta "abra o bloco de notas" e executa open_application
    # mesmo sem o LLM local estar disponível para geração de tool call.
    session = create_session(client)
    events = send_agent(client, session["id"], "abra o bloco de notas")
    assert any(e["type"] == "tool_done" and e["ok"] is True for e in events)
    assert any("notepad" in str(o) for o in fake_computer.opened)