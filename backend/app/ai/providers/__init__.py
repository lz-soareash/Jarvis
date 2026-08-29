from app.core.config import settings

from .base import AIProvider, AIProviderError
from .gemini import GeminiProvider

__all__ = ["AIProvider", "AIProviderError", "GeminiProvider", "get_default_provider"]

_default_provider: AIProvider | None = None


def get_default_provider() -> AIProvider:
    """Singleton do provedor padrão (Gemini), construído a partir do settings."""
    global _default_provider
    if _default_provider is None:
        _default_provider = GeminiProvider()
    return _default_provider