"""Provedor DuckDuckGo (HTML/Lite) — Fase 14, busca real sem API key.

Coleta NÃO depende de LLM nem de credenciais (local-first). O provider faz um
GET ao endpoint público HTML do DDG e normaliza os resultados. A rede é
SEMPRE injetável por `transport` (httpx.MockTransport) nos testes — nenhum
teste toca a internet. Construtor sem efeitos colaterais; o client HTTP é
criado apenas na primeira `search`.
"""

import html
import logging
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.config import settings

from .base import SearchProvider, SearchProviderError, SearchQuery, SearchResult, build_result

logger = logging.getLogger("jarvis.websearch.ddg")


class _DDGResultParser(HTMLParser):
    """Varre o HTML inteiro e extrai blocos de resultado do DDG (`results_links`).

    Rastreia a profundidade das `<div>` para capturar o bloco completo mesmo
    com `<div>` aninhados internos (estrutura real do DDG HTML/Lite).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []  # {"href", "title_parts", "snippet_parts"}
        self._div_depth = 0
        self._block_base: int | None = None
        self._cur: dict | None = None
        self._mode: str | None = None  # "title" | "snippet"

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class", "") or ""
        if tag == "div":
            self._div_depth += 1
            if self._cur is None and "results_links" in cls:
                self._cur = {"href": None, "title_parts": [], "snippet_parts": []}
                self._block_base = self._div_depth
            return
        if self._cur is None:
            return
        if tag == "a":
            if "result__a" in cls:
                self._cur["href"] = attrs.get("href")
                self._mode = "title"
            elif "result__snippet" in cls:
                self._mode = "snippet"
        elif tag in ("svg", "img", "script", "style"):
            self._mode = None

    def handle_data(self, data):
        if self._cur is not None and self._mode:
            parts = self._cur["title_parts"] if self._mode == "title" else self._cur["snippet_parts"]
            parts.append(data)

    def handle_endtag(self, tag):
        if tag == "div" and self._block_base is not None:
            self._div_depth -= 1
            if self._div_depth == self._block_base - 1:
                self.results.append(self._cur)
                self._cur = None
                self._block_base = None
                self._mode = None
            return
        if self._cur is not None and tag in ("a", "span"):
            self._mode = None


def _clean(parts: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _resolve_ddg_url(raw: str | None) -> str:
    """Normaliza a URL de um resultado DDG (redirect `uddg` ou relativa)."""
    if not raw:
        return ""
    url = html.unescape(raw.strip().strip('"'))
    parsed = urlparse(url)
    if parsed.netloc in ("duckduckgo.com", "www.duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        url = unquote(target)
    elif url.startswith("//"):
        url = "https:" + url
    # Sanidade: só http/https (o resto é descartado aqui; o Fetcher valida SSRF).
    if urlparse(url).scheme not in ("http", "https"):
        return ""
    return url


class DuckDuckGoSearchProvider(SearchProvider):
    """Busca real no endpoint HTML/Lite do DDG (sem API key)."""

    name = "duckduckgo"
    base_url = "https://html.duckduckgo.com/html/"
    path = "/html/"

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        timeout: float | None = None,
        max_results: int | None = None,
        user_agent: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._enabled = settings.web_search_enabled if enabled is None else enabled
        self._timeout = timeout if timeout is not None else settings.web_search_timeout
        self._max_results = max(max_results or settings.web_search_max_results, 1)
        self._user_agent = user_agent or settings.web_fetch_user_agent
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self.model = self.name

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=True,
                headers={"User-Agent": self._user_agent, "Accept": "text/html"},
            )
        return self._client

    async def search(self, query: SearchQuery) -> list[SearchResult]:
        if not self._enabled:
            return []
        terms = (query.terms or "").strip()
        if not terms:
            return []
        params = {"q": terms}
        client = self._get_client()
        try:
            resp = await client.get(self.base_url, params=params)
        except httpx.HTTPError as exc:
            raise SearchProviderError(f"DuckDuckGo indisponível: {exc}") from exc
        if resp.status_code != 200:
            raise SearchProviderError(f"DuckDuckGo respondeu HTTP {resp.status_code}.")
        body = resp.text
        results = _parse_html_results(body, source=self.name, max_results=self._max_results)
        if not results:
            logger.info("DuckDuckGo: nenhum resultado p/ %r", terms)
        return results

    async def health(self) -> dict:
        return {
            "ok": self._enabled,
            "enabled": self._enabled,
            "detail": "provider configurado" if self._enabled else "desabilitado (WEB_SEARCH_ENABLED=false)",
        }

    def capabilities(self) -> dict:
        caps = super().capabilities()
        caps.update({"requires_key": False, "network": self._enabled, "endpoint": self.path})
        return caps

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _parse_html_results(body: str, *, source: str, max_results: int) -> list[SearchResult]:
    """Extrai resultados do HTML do DDG (blocos `results_links`)."""
    parser = _DDGResultParser()
    try:
        parser.feed(body or "")
    except Exception:  # noqa: BLE001 — malformado não derruba o provider
        return []
    results: list[SearchResult] = []
    for i, block in enumerate(parser.results):
        if len(results) >= max_results:
            break
        url = _resolve_ddg_url(block["href"])
        title = _clean_title(_clean(block["title_parts"]))
        if not url or not title:
            continue
        snippet = _clean(block["snippet_parts"])
        results.append(
            build_result(
                title=title,
                url=url,
                snippet=snippet,
                source=source,
                rank=i + 1,
                metadata={"provider": source},
            )
        )
    return results


def _clean_title(title: str) -> str:
    # Remove sufixos decorativos comuns que o DDG injeta no HTML.
    return re.sub(r"\s*::\s*", " ", title).strip()[:300]


def get_default_provider() -> SearchProvider:
    """Provider default da busca (DDG), via interface intercambiável."""
    return DuckDuckGoSearchProvider()