"""Contratos dos provedores de busca (Fase 13 — esqueleto, sem rede real).

Provedores de busca entram/saem sem tocar no consumidor. Cada implementação
resolve `Query → SearchResult`; o Core da fase futura decide qual usar
(provider abstrato e intercambiável, conforme decisão de escopo).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """Um item de resultado de busca normalizado."""
    title: str
    url: str
    snippet: str = ""  # trecho resumido (snippet)
    position: int = 0


@dataclass
class SearchQuery:
    """Consulta normalizada de busca."""
    terms: str
    max_results: int = 8
    extra: dict = field(default_factory=dict)


class SearchProvider(ABC):
    """Marca de um provedor de busca intercambiável.

    A interface é estável: `search(query)` devolve resultados normalizados.
    Implementações reais (DDG/Google/Brave) são plug-ins substituíveis.
    """

    name: str = "base"

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[SearchResult]:
        """Executa a busca e devolve resultados normalizados."""