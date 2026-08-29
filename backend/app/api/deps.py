from app.ai.providers.base import AIProvider
from app.ai.providers import get_default_provider
from app.computer.controller import get_system_controller as _get_controller


def get_ai_provider() -> AIProvider:
    """Dependency do FastAPI que fornece o provedor de IA padrão."""
    return get_default_provider()


def get_system_controller():
    """Dependency do FastAPI que fornece o SystemController (Fase 5)."""
    return _get_controller()