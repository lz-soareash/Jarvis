from app.core.config import settings

from .base import AIProvider, AIProviderError
from .gemini import GeminiProvider

from app.ai.registry import get_ai_router, AIProviderRouter  # noqa: E402

__all__ = [
    "AIProvider",
    "AIProviderError",
    "GeminiProvider",
    "get_default_provider",
    "get_default_router",
]


def get_default_router() -> AIProviderRouter:
    """Singleton do AI Router configurado a partir do settings."""
    return get_ai_router()


def get_default_provider() -> AIProvider:
    """Provedor padrão — resolvido pelo AI Router (Fase 11).

    Retorna o primeiro provedor configurado para a tarefa de geração; sem nada
    configurado, devolve o último candidato registrado (o chamador recebe
    `is_configured` False e decide o erro). O Gemini deixou de ser obrigatório.
    """
    return get_ai_router().resolve(task="generate")