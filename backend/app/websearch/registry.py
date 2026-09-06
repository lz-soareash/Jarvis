"""Registry de provedores de busca (Fase 14).

Resolve `WEB_SEARCH_PROVIDER` para uma instância `SearchProvider` pronta para
uso. O singleton admite reset para testes (imports/pulso de teste não
reiniciam o app). Nenhum provider é construído com efeitos colaterais.
"""

import logging
from typing import Callable

from app.core.config import settings

from .base import SearchProvider, SearchResult, SearchQuery
from .duckduckgo import DuckDuckGoSearchProvider

logger = logging.getLogger("jarvis.websearch.registry")

ProviderFactory = Callable[[], SearchProvider]

_BUILTIN_FACTORIES: dict[str, ProviderFactory] = {
    "duckduckgo": DuckDuckGoSearchProvider,
}


class SearchProviderRegistry:
    """Contém providers registrados e resolve o default do settings."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = dict(_BUILTIN_FACTORIES)
        self._providers: dict[str, SearchProvider] = {}

    def register(self, name: str, factory: ProviderFactory) -> None:
        self._factories[name] = factory
        self._providers.pop(name, None)

    def register_instance(self, provider: SearchProvider) -> None:
        """Registra uma instância pronta (testes/fakes) — sombreia a fábrica.

        A fábrica builtin permanece registrada para que `reset()` volte ao estado
        inicial mesmo depois de um provider fake ter sido injetado.
        """
        self._providers[provider.name] = provider

    def get(self, name: str | None = None) -> SearchProvider:
        """Provider default (settings) ou o nome escolhido."""
        name = name or settings.web_search_provider or "duckduckgo"
        if name in self._providers:
            return self._providers[name]
        factory = self._factories.get(name)
        if factory is None:
            logger.warning("Provider de busca '%s' desconhecido; usando duckduckgo", name)
            name = "duckduckgo"
            factory = self._factories[name]
        provider = factory()
        self._providers[name] = provider
        return provider

    def reset(self) -> None:
        """Limpa instâncias cacheadas (testes) mantendo fábricas builtin."""
        self._providers = {}

    def names(self) -> list[str]:
        return sorted(set(self._factories) | set(self._providers))


_registry: SearchProviderRegistry | None = None


def get_search_registry() -> SearchProviderRegistry:
    global _registry
    if _registry is None:
        _registry = SearchProviderRegistry()
    return _registry


def reset_search_registry() -> None:
    get_search_registry().reset()


__all__ = [
    "ProviderFactory",
    "SearchProvider",
    "SearchProviderRegistry",
    "SearchQuery",
    "SearchResult",
    "get_search_registry",
    "reset_search_registry",
]