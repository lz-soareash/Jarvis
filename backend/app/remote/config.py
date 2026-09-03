"""Configuração do transporte remoto (Fase 12.1).

Centraliza as decisões de habilitação/validação da infra remota, desacoplando o
runtime dos detalhes de onde cada flag mora (Settings).
"""

from __future__ import annotations

from app.core.config import settings


def is_remote_enabled() -> bool:
    """Feature flag principal: infra remota ligada/desligada (padrão: false)."""
    return settings.remote_enabled


def is_remote_configured() -> bool:
    """True quando há endpoint e identidade suficientes para conectar à Gateway."""
    return bool(settings.remote_gateway_url and settings.remote_device_id)