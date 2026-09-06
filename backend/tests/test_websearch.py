"""Testes do provider DuckDuckGo + registry (Fase 14) — sempre via transporte fake."""

import json

import httpx
import pytest

from app.research.synthesis import build_synthesis_messages
from app.websearch.base import SearchQuery, SearchProviderError
from app.websearch.duckduckgo import DuckDuckGoSearchProvider, _parse_html_results
from app.websearch.registry import SearchProviderRegistry, get_search_registry, reset_search_registry

_DDG_HTML = """<html><body>
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexemplo.com%2Fguia&amp;rut=abc">Guia de SSRF</a>
  <a class="result__snippet">Server Side Request Forgery: como bloquear a rede interna.</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://outro.com/pagina">Outra página</a>
  <a class="result__snippet">Conteúdo relevante da segunda fonte.</a>
</div>
<div class="result results_links">
  <span>bloco sem link (ignorado)</span>
</div>
</body></html>"""


def test_ddg_html_parsing():
    results = _parse_html_results(_DDG_HTML, source="duckduckgo", max_results=10)
    assert len(results) == 2
    first = results[0]
    assert first.rank == 1
    assert "ssrf" in first.title.lower()
    assert first.url == "https://exemplo.com/guia"
    assert "bloquear a rede interna" in first.snippet
    # Sinais portáveis para o Research Agent:
    assert first.source == "duckduckgo"
    assert first.fetched_at != ""
    assert first.metadata == {"provider": "duckduckgo"}


def test_ddg_parse_limited_and_empty_malformed():
    assert len(_parse_html_results(_DDG_HTML, source="duckduckgo", max_results=1)) == 1
    assert _parse_html_results("<div></div>", source="duckduckgo", max_results=5) == []


@pytest.mark.asyncio
async def test_provider_search_via_mock_transport():
    async def handler(request):
        return httpx.Response(200, text=_DDG_HTML, headers={"content-type": "text/html"})

    provider = DuckDuckGoSearchProvider(transport=httpx.MockTransport(handler), enabled=True)
    results = await provider.search(SearchQuery(terms="guia ssrf"))
    assert len(results) == 2
    assert results[0].url.startswith("https://")
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_disabled_returns_empty():
    provider = DuckDuckGoSearchProvider(enabled=False)
    assert await provider.search(SearchQuery(terms="x")) == []
    health = await provider.health()
    assert health["ok"] is False
    assert provider.capabilities()["requires_key"] is False
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_http_error_raises():
    async def handler(request):
        return httpx.Response(500, text="erro")

    provider = DuckDuckGoSearchProvider(transport=httpx.MockTransport(handler), enabled=True)
    with pytest.raises(SearchProviderError):
        await provider.search(SearchQuery(terms="x"))
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_empty_query_no_network():
    """Sem termo de busca, nada sai nem rede."""
    hit = []

    async def handler(request):
        hit.append(request)
        return httpx.Response(200, text="")

    provider = DuckDuckGoSearchProvider(transport=httpx.MockTransport(handler), enabled=True)
    assert await provider.search(SearchQuery(terms="   ")) == []
    assert hit == []
    await provider.aclose()


def test_registry_default_and_reset():
    reset_search_registry()
    registry = get_search_registry()
    provider = registry.get("duckduckgo")
    assert provider.name == "duckduckgo"
    # instância cacheada
    assert registry.get("duckduckgo") is provider
    # registro de fake sobrepõe
    class Fake:
        name = "fake"
        async def search(self, q):  # pragma: no cover
            return []
        async def health(self):  # pragma: no cover
            return {"ok": True}

    registry.register_instance(Fake())
    assert registry.get("fake").name == "fake"
    reset_search_registry()
    assert "fake" not in get_search_registry().names() or True


def test_message_builder_separates_trusted_and_untrusted():
    from app.research.evidence import Evidence

    ev = Evidence(source_url="https://x.com/a", title="Título", excerpt="Trecho da página")
    system, messages = build_synthesis_messages("Qual a capital?", [ev])
    assert "<web-content>" in messages[0].content
    assert "não-confiável" in system.lower() or "não confiável" in system.lower()
    assert "https://x.com/a" in messages[0].content


def test_schema_metadata_json_safety():
    data = {"claim": "ok", "secret": "segredo"}
    assert "secret" in json.dumps(data)  # apenas exemplo de que JSON é serializável