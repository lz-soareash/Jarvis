"""Provedor DuckDuckGo (HTML/Lite) — esqueleto da Fase 13.

Nesta fase o provider NÃO faz chamadas reais (sem rede, sem dependência
externa, local-first). Ele materializa o contrato da interface para a futura
fase Web Research: quando ativado via configuração, executará a busca DDG.

Decisão de escopo aprovada:
  - provider ABSTRATO e INTERCAMBIÁVEL (aqui: `SearchProvider`);
  - default e preferido: DuckDuckGo HTML/Lite;
  - placeholder agora; implementação real só quando a fase Web for implementada.
"""

from app.websearch.base import SearchProvider, SearchQuery, SearchResult


class DuckDuckGoSearchProvider(SearchProvider):
    """DuckDuckGo como provider default (placeholder — implementação na fase Web)."""

    name = "duckduckgo"
    base_url = "https://html.duckduckgo.com/html/"

    def __init__(self) -> None:
        self.enabled = False  # desligado até a fase Web (sem chamar rede)

    async def search(self, query: SearchQuery) -> list[SearchResult]:
        """Placeholder seguro: NÃO executa busca real nesta fase.

        Devolve lista vazia e documenta o uso pretendido, evitando qualquer
        chamada de rede ou efeito colateral.
        """
        return []


def get_default_provider() -> SearchProvider:
    """Provider default da fase (DDG), via interface intercambiável."""
    return DuckDuckGoSearchProvider()