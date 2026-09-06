"""Testes da integração Agentic Core × Web Research (Fase 14).

Cobre: planner determinístico detectando `kind=research`, execução de uma
tarefa com passo de pesquisa (zero rede — providers e fetcher fake injetados
via monkeypatch no `research_service`), e falha sem evidências (passo ignorado,
a tarefa não falha).
"""

import asyncio
import json

import pytest

from app.core.enums import StepStatus, TaskStatus
from app.websearch.base import SearchResult


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


class _FakeSearch:
    name = "fakesearch"

    def __init__(self, results=None):
        self._results = list(results or [])
        self.calls = 0

    async def search(self, query):
        self.calls += 1
        return list(self._results)

    async def health(self):
        return {"ok": True, "enabled": True, "detail": "fake"}


class _Registry:
    def __init__(self, search):
        self._search = search

    def get(self, name=None):
        return self._search


class _FakePage:
    def __init__(self, text: str, url: str = "https://exemplo.com/a"):
        self.url = url
        self.final_url = url
        self.status = 200
        self.text = text
        self.text_length = len(text.encode("utf-8"))
        self.truncated = False


class _FakeFetcher:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    async def fetch(self, url):
        return _FakePage("<p>Paris é a capital da França.</p>", url)

    async def close(self):
        pass


@pytest.fixture
def research_runtime(monkeypatch):
    """Injeta busca/fetch fake no research_service (nunca rede real)."""
    from app.services import research_service

    search = _FakeSearch(
        [_result("https://exemplo.com/a", "Fonte A", "Paris é a capital da França.", 1)]
    )
    monkeypatch.setattr(research_service, "get_search_registry", lambda: _Registry(search))
    monkeypatch.setattr(research_service, "WebFetcher", _FakeFetcher)
    return search


def _result(url, title, snippet, rank):
    return SearchResult(title=title, url=url, snippet=snippet, source="fakesearch", rank=rank)


def _make_session(db):
    from app.services import chat as chat_service

    return chat_service.create_session(db)


def test_plan_for_detects_research():
    from app.services.agent_core import plan_for

    steps = plan_for("Pesquise na web sobre as tendências de IA")
    assert len(steps) == 1
    assert steps[0]["kind"] == "research"
    assert steps[0]["verify"]["kind"] == "research"


def test_plan_for_default_stays_analysis():
    from app.services.agent_core import plan_for

    steps = plan_for("me ajuda a analisar o projeto")
    assert steps and "kind" not in steps[0]


def test_agent_task_with_research_step_completes(research_runtime, fake_ai):
    from app.db.session import SessionLocal
    from app.services.agent_core import get_task, run_agent_task

    with SessionLocal() as db:
        session = _make_session(db)
        plan = [
            {
                "description": "Pesquisar na web",
                "kind": "research",
                "arguments": {"objective": "Qual a capital da França?"},
                "verify": {"kind": "research"},
                "guard": "skip",
            }
        ]
        events = _parse(
            _iter_sync(run_agent_task(db, session.id, fake_ai, "Pesquise na web", plan=plan))
        )
        done = next(e for e in events if e["type"] == "agent_task_done")
        assert done["status"] == TaskStatus.COMPLETED.value
        task = get_task(db, done["task_id"])
        assert task.progress[0]["status"] == StepStatus.DONE.value

        tools = [e["name"] for e in events if e["type"] == "tool_done"]
        assert "web_research" in tools
        # Eventos internos do Research Agent aparecem no SSE da tarefa:
        research_events = [e for e in events if e["type"].startswith("research.")]
        assert any(e["type"] == "research.completed" for e in research_events)


def test_agent_task_research_without_evidence_skips_step(research_runtime, fake_ai):
    """Sem evidências o passo é ignorado (guard=skip), a tarefa não falha."""
    research_runtime._results = []
    from app.db.session import SessionLocal
    from app.services.agent_core import get_task, run_agent_task

    with SessionLocal() as db:
        session = _make_session(db)
        plan = [
            {
                "description": "Pesquisar vazio",
                "kind": "research",
                "arguments": {"objective": "algo sem resultado"},
                "verify": {"kind": "research"},
                "guard": "skip",
            }
        ]
        events = _parse(_iter_sync(run_agent_task(db, session.id, fake_ai, "Pesquise", plan=plan)))
        done = next(e for e in events if e["type"] == "agent_task_done")
        assert done["status"] == TaskStatus.COMPLETED.value
        task = get_task(db, done["task_id"])
        assert task.progress[0]["status"] == StepStatus.SKIPPED.value
        assert any(e["type"] == "research.failed" for e in events)