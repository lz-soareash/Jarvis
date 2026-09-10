"""Gateway WAN (Fase 23) — relé WebSocket deployável separadamente.

Pacote independente do Core: não importa o banco nem `app.core.config`. O
processo do Gateway é iniciado via `backend/scripts/run_gateway.py`.
"""

from .config import get_settings
from .hub import RelayHub
from .main import app, create_app
from .registry import GatewayRegistry, ROLE_CORE, ROLE_MOBILE

__all__ = [
    "RelayHub",
    "GatewayRegistry",
    "get_settings",
    "create_app",
    "app",
    "ROLE_CORE",
    "ROLE_MOBILE",
]