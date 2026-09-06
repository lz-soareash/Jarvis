"""Testes da API de Research (Fase 14) — SSE hermético com fakes injetados."""

import json

import pytest

from app.models import ResearchRun
from app.research.agent import ResearchAgent
from app.research.evidence import Evidence
from app.research.knowledge import KnowledgeCandidate, KnowledgeStore
from app.websearch.base import SearchResult


class FakeSearchProvider:
    name = "fakesearch"

    def __init__(self, results):
        self._results = list(results)

    async def search(self, query):
        return list(self._results)

    async def health(self) -> dict:
        return {"ok": True, "enabled": True, "detail": "fake"}


class FakePage:
    def __init__(self, text: str, url: str, status: int = 200):
        self.url = url
        self.final_url = url
        self.status = status
        self.text = text
        self.text_length = len(text.encode("utf-8"))
        self.truncated = False


class FakeFetcherForTests:
    def __init__(self):
        self._pages = {
            "https://exemplo.com/a": FakePage("<h1>A</h1><p>Paris é a capital da França.</p>", "https://exemplo.com/a"),
            "https://exemplo.com/b": FakePage("<h1>B</h1><p>A Torre Eiffel fica em Paris.</p>", "https://exemplo.com/b"),
        }

    async def fetch(self, url):
        return self._pages[url]

    async def close(self):
        pass


def _result(url, title, snippet, rank=1):
    return SearchResult(title=title, url=url, snippet=snippet, source="fakesearch", rank=rank)


@pytest.fixture(autouse=True)
def _monkey_inject_research_deps(monkeypatch):
    """Toda a Fase 14 na API usa fakes (zero rede)."""
    from app.services import research_service

    class FakeSearch:
        name = "fakesearch"

        def __init__(self):
            self._results = [
                _result("https://exemplo.com/a", "Fonte A", "Paris é a capital da França.", 1),
                _result("https://exemplo.com/b", "Fonte B", "A Torre Eiffel fica em Paris.", 2),
            ]

        async def search(self, query):
            return list(self._results)

        async def health(self):
            return {"ok": True, "enabled": True, "detail": "fake"}

    class _Registry:
        def get(self, name=None):
            return _search

    _search = FakeSearch()
    monkeypatch.setattr(research_service, "get_search_registry", lambda: _Registry())
    monkeypatch.setattr(research_service, "WebFetcher", FakeFetcherForTests)


def _synthesis_ok():
    return json.dumps(
        {
            "answer": "A capital da França é Paris.",
            "facts": [{"text": "Paris é a capital.", "sources": [1]}],
            "inferences": [],
            "uncertainties": [],
            "citations": [{"source": 1, "excerpt": "Paris é a capital."}],
        }
    )


def _make_session(client):
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 201
    return resp.json()["id"]


def test_create_research_streams_sse(client, fake_ai):
    fake_ai.reply = _synthesis_ok()
    session_id = _make_session(client)
    resp = client.post(
        f"/api/research?session_id={session_id}",
        json={"objective": "Qual a capital da França?"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert "research.started" in types
    assert "research.result" in types
    assert "done" in types
    final = next(e for e in events if e["type"] == "research.result")
    assert final["answer"] == "A capital da França é Paris."
    assert final["citations"][0]["source_url"] == "https://exemplo.com/a"


def test_research_requires_real_session(client, fake_ai):
    resp = client.post("/api/research?session_id=nao-existe", json={"objective": "x"})
    assert resp.status_code == 404


def test_research_requires_objective(client, fake_ai):
    session_id = _make_session(client)
    resp = client.post(f"/api/research?session_id={session_id}", json={"objective": "  "})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_run_and_sources(client, fake_ai):
    fake_ai.reply = _synthesis_ok()
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        agent = ResearchAgent(
            search_provider=FakeSearchProvider(
                [_result("https://exemplo.com/a", "Fonte A", "Paris é a capital.", 1)]
            ),
            fetcher=FakeFetcherForTests(),
            ai_provider=fake_ai,
            ai_router=None,
            db=db,
            max_queries=1,
            max_pages=1,
        )
        outcome = await agent.run("Qual a capital?")
        assert outcome.run_id

        resp = client.get(f"/api/research/{outcome.run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["objective"] == "Qual a capital?"
        assert data["citations"]

        src = client.get(f"/api/research/{outcome.run_id}/sources")
        assert src.status_code == 200
        assert any(s["source_url"] for s in src.json())
    finally:
        db.close()


def test_promote_knowledge_endpoint(client):
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        run = ResearchRun(objective="objetivo", queries_json="[]", status="completed", answer="ok")
        db.add(run)
        db.commit()
        db.refresh(run)
        cand = KnowledgeCandidate(claim="Fato confirmado pela API.", sources=[Evidence(source_url="https://exemplo.com/x")])
        (record,), _ = KnowledgeStore(db).record_candidates([cand], research_id=run.id)

        resp = client.post(f"/api/research/{run.id}/knowledge/promote", json={"candidate_ids": [record.id]})
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["claim"] == cand.claim
        assert body[0]["status"] == "confirmed"
    finally:
        db.close()


def test_promote_unknown_run_404(client):
    resp = client.post("/api/research/nao-existe/knowledge/promote", json={"candidate_ids": []})
    assert resp.status_code == 404