"""Testes das tools web_search / web_fetch (Fase 14) — fakes, nunca rede."""

import httpx
import pytest

from app.tools.base import ToolContext
from app.websearch.base import SearchResult


@pytest.fixture
def ctx():
    return ToolContext(db=None, provider=None, session_id=None)


def _results():
    return [
        SearchResult(title="Guia", url="https://exemplo.com/guia", snippet="Server Side Request Forgery.", source="duckduckgo", rank=1),
        SearchResult(title="Outro", url="https://exemplo.com/2", snippet="Conteúdo dois.", source="duckduckgo", rank=2),
    ]


class _FakeSearchProvider:
    name = "duckduckgo"

    def __init__(self):
        self._results = _results()

    async def search(self, query):
        return list(self._results)

    async def health(self):
        return {"ok": True, "enabled": True, "detail": "fake"}


def test_web_search_tool_uses_registry(ctx):
    from app.websearch.registry import get_search_registry, reset_search_registry

    reset_search_registry()
    reg = get_search_registry()
    reg.register_instance(_FakeSearchProvider())
    try:
        from app.tools.web import WebSearch

        result = _run(WebSearch(), ctx, query="ssrf")
        assert result.ok
        assert "https://exemplo.com/guia" in result.output
        assert "não-confiável" in result.output.lower() or "não confiável" in result.output.lower()
    finally:
        reset_search_registry()


def test_web_search_empty_query(ctx):
    from app.tools.web import WebSearch

    result = _run(WebSearch(), ctx, query="   ")
    assert not result.ok


async def _fetch_page(request):
    return httpx.Response(
        200,
        text="<html><head><title>Guia SSRF</title></head><body><p>conteúdo do guia</p></body></html>",
        headers={"content-type": "text/html"},
    )


async def _resolve(host: str):
    return ["93.184.216.34"]


def test_web_fetch_tool_returns_text(ctx, monkeypatch):
    from app.tools import web as web_module

    monkeypatch.setattr(
        web_module,
        "make_fetcher",
        lambda max_bytes=None: web_module.__dict__.get("_wf") or _build_fetcher(),
    )

    def _build_fetcher():
        from app.research.fetcher import WebFetcher

        f = WebFetcher(transport=httpx.MockTransport(_fetch_page), resolver=_resolve)
        web_module._wf = f
        return f

    from app.tools.web import WebFetch

    result = _run(WebFetch(), ctx, url="http://example.com/guia")
    assert result.ok
    assert "Guia SSRF" in result.output
    assert "conteúdo do guia" in result.output
    assert "não-confiável" in result.output.lower()


def test_web_fetch_validates_ssrf(ctx, monkeypatch):
    from app.tools import web as web_module

    def _broken(host: str):
        return ["127.0.0.1"]

    monkeypatch.setattr(web_module, "make_fetcher", lambda max_bytes=None: _hf())
    from app.research.fetcher import WebFetcher

    def _hf():
        return WebFetcher(transport=httpx.MockTransport(_fetch_page), resolver=_broken)

    from app.tools.web import WebFetch

    result = _run(WebFetch(), ctx, url="http://secret.internal/x")
    assert not result.ok
    assert "SSRF" in result.output or "rejeitado" in result.output.lower()


def _run(tool, ctx, **kwargs):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(tool.run(ctx, **kwargs))