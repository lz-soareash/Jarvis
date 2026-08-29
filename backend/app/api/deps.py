from app.ai.providers.base import AIProvider
from app.ai.providers import get_default_provider


def get_ai_provider() -> AIProvider:
    """Dependency do FastAPI que fornece o provedor de IA padrão."""
    return get_default_provider()