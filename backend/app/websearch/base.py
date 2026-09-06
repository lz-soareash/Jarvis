"""Contratos dos provedores de busca (Fase 13 esqueleto → Fase 14 real).

Cada provedor resolve `Query → SearchResult` normalizados. O Research Agent
nunca conhece o provedor concreto: usa `search(query)`, `health()` e
`capabilities()` via o provider registry — DuckDuckGo hoje, Wikipedia/Brave/
Google CSE amanhã, sem reescrever o consumidor.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SearchResult:
    """Um item de resultado de busca normalizado (Fase 14)."""

    title: str
    url: str
    snippet: str = ""  # trecho resumido (sinal inicial, nunca confiável cegamente)
    source: str = ""  # nome do provider
    rank: int = 0  # posição ordinal (1 = primeiro)
    fetched_at: str = ""  # ISO8601 de quando o resultado foi obtido
    metadata: dict = field(default_factory=dict)  # extras do provider (sanitizados)


@dataclass
class SearchQuery:
    """Consulta normalizada de busca."""

    terms: str
    max_results: int = 8
    extra: dict = field(default_factory=dict)


class SearchProvider(ABC):
    """Marca de um provedor de busca intercambiável.

    Interface estável e assíncrona; implementações (DDG/Google/Brave) são
    plug-ins substituíveis. `search` pode levantar `SearchProviderError`.
    """

    name: str = "base"

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[SearchResult]:
        """Executa a busca e devolve resultados normalizados (vazio = sem hits)."""

    @abstractmethod
    async def health(self) -> dict:
        """Estado local do provedor (sem rede): {ok, enabled, detail, ...}."""

    def capabilities(self) -> dict:
        """Capacidades declaradas do provedor (sanitizadas, sem secrets)."""
        return {"name": self.name, "requires_key": False}


class SearchProviderError(RuntimeError):
    """Falha de busca do provider (timeout, HTTP, parse)."""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def build_result(
    *,
    title: str,
    url: str,
    snippet: str = "",
    source: str = "",
    rank: int = 0,
    metadata: dict | None = None,
) -> SearchResult:
    return SearchResult(
        title=(title or "").strip(),
        url=(url or "").strip(),
        snippet=(snippet or "").strip(),
        source=source,
        rank=rank,
        fetched_at=_now_iso(),
        metadata=dict(metadata or {}),
    )