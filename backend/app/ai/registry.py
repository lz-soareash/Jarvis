"""AI Router (Fase 11): seleção de provedores de IA por prioridade e capacidade.

Camada entre o AI Core e os provedores (Gemini / determinístico / LLM local
futuro). Resolve o provedor adequado para a tarefa, fazendo fallback automático
para o próximo configurado, e expõe o estado dos provedores para observabilidade.

O Gemini deixa de ser dependência arquitetural obrigatória: se não estiver
configurado, o router escolhe o próximo provedor com capacidade para a tarefa
(geralmente o determinístico — safety fallback).
"""

import logging

from app.core.config import settings

from .providers.base import AIProvider, AIProviderStatus
from .providers.deterministic import DeterministicProvider
from .providers.gemini import GeminiProvider

logger = logging.getLogger("jarvis.ai.router")

# Tarefas conhecidas do router.
TASK_GENERATE = "generate"
TASK_EMBED = "embed"

# Nome -> fábrica de provedores conhecidos (novos provedores entram aqui).
_PROVIDER_FACTORIES: dict[str, callable] = {
    "gemini": GeminiProvider,
    "deterministic": DeterministicProvider,
}

_ALL_CAPABILITIES = {"generate", "stream", "analyze", "embed", "tools"}


class AIProviderRouter:
    """Resolve o primeiro provedor configurado que atende à tarefa.

    `providers` vem ordenado por prioridade (o primeiro é o preferido). A ordem
    é definida por `AI_PROVIDER_ORDER` (config), preservando a capacidade de
    adicionar provedores futuros sem refatorar o AI Core.
    """

    def __init__(self, providers: list[AIProvider]):
        self._providers = list(providers)

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    def all(self) -> list[AIProvider]:
        return list(self._providers)

    def supports(self, provider: AIProvider, task: str) -> bool:
        caps = getattr(provider, "capabilities", _ALL_CAPABILITIES)
        return bool(caps & {task}) if isinstance(caps, (set, frozenset)) else task in _ALL_CAPABILITIES

    def configured(self) -> list[AIProvider]:
        return [p for p in self._providers if p.is_configured]

    def primary(self, task: str = TASK_GENERATE) -> AIProvider | None:
        """Provedor preferido configurado para a tarefa (ou None)."""
        for p in self._providers:
            if self.supports(p, task) and p.is_configured:
                return p
        return None

    def resolve(self, task: str = TASK_GENERATE) -> AIProvider:
        """Primeiro provedor configurado que atende à tarefa.

        Se nenhum estiver configurado, devolve o último candidato registrado
        (o caller decide o que fazer — ex.: 503 — quando `is_configured` é False).
        """
        for p in self._providers:
            if self.supports(p, task) and p.is_configured:
                return p
        # Nenhum configurado: melhor esforço — o primeiro que suporta a tarefa.
        for p in self._providers:
            if self.supports(p, task):
                return p
        if self._providers:
            return self._providers[0]
        raise RuntimeError("Nenhum provedor de IA registrado no AI Router")

    def preferencia_estado(self, provider: AIProvider, task: str = TASK_GENERATE) -> dict:
        """Rotula o provedor quanto à sua posição na prioridade configurada."""
        primary = self.primary(task)
        return {
            "provider": provider.name,
            "model": getattr(provider, "model", None),
            "fallback": bool(primary is not None and provider is not primary),
            "configured": provider.is_configured,
        }

    def statuses(self) -> list[AIProviderStatus]:
        """Estado dos provedores (leitura local — sem chamadas de rede)."""
        result: list[AIProviderStatus] = []
        for p in self._providers:
            model = getattr(p, "model", None)
            if not p.is_configured:
                result.append(
                    AIProviderStatus(
                        status="unconfigured",
                        provider=p.name,
                        model=model,
                        detail="sem credenciais/configuração",
                    )
                )
            else:
                result.append(
                    AIProviderStatus(
                        status="ok",
                        provider=p.name,
                        model=model,
                        detail="configurado",
                    )
                )
        return result


_default_router: AIProviderRouter | None = None


def build_default_router() -> AIProviderRouter:
    """Monta o router a partir de `settings.ai_provider_order`.

    A ordem lista os nomes por prioridade (primeiro = preferido). Nomes
    desconhecidos são ignorados com aviso; provedores duplicados são descartados.
    """
    order = [name.strip().lower() for name in settings.ai_provider_order.split(",") if name.strip()]
    if not order:
        order = list(_PROVIDER_FACTORIES.keys())

    providers: list[AIProvider] = []
    seen: set[str] = set()
    for name in order:
        if name in seen:
            continue
        factory = _PROVIDER_FACTORIES.get(name)
        if factory is None:
            logger.warning("Provedor de IA desconhecido no AI_PROVIDER_ORDER: %s", name)
            continue
        provider = factory()
        if isinstance(provider, DeterministicProvider) and not settings.ai_deterministic_enabled:
            continue  # fallback determinístico desabilitado por configuração
        providers.append(provider)
        seen.add(name)

    # Garante que provedores conhecidos não citados na ordem entram no fim
    # (fallback residencial), e que o determinístico só entra se habilitado.
    for name, factory in _PROVIDER_FACTORIES.items():
        if name in seen:
            continue
        if name == "deterministic" and not settings.ai_deterministic_enabled:
            continue
        if name in _PROVIDER_FACTORIES:
            providers.append(factory())
            seen.add(name)
    return AIProviderRouter(providers)


def get_ai_router() -> AIProviderRouter:
    global _default_router
    if _default_router is None:
        _default_router = build_default_router()
    return _default_router


def reset_ai_router() -> None:
    """Descarta o router singleton (testes isolam configuração)."""
    global _default_router
    _default_router = None