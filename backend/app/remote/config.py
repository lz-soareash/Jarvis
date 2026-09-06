"""Configuração do transporte remoto (Fase 12.1 / 12.2).

Centraliza as decisões de habilitação/validação da infra remota, desacoplando o
runtime dos detalhes de onde cada flag mora (Settings). Fase 16 adiciona os
parâmetros de endurecimento da camada de acesso (TTL, payload, rate limit, CORS).
"""

from __future__ import annotations

from app.core.config import settings


def is_remote_enabled() -> bool:
    """Feature flag principal: infra remota ligada/desligada (padrão: false)."""
    return settings.remote_enabled


def is_remote_configured() -> bool:
    """True quando há endpoint e identidade suficientes para conectar à Gateway."""
    return bool(settings.remote_gateway_url and settings.remote_device_id)


def remote_session_ttl() -> int:
    """TTL de atividade de uma sessão remota (0 = sem expiração)."""
    return settings.remote_session_ttl_seconds


def remote_cors_origin_list() -> list[str]:
    """Origins CORS configuradas (lista limpa). Vazio = restritivo (padrão).

    `remote_cors_origins` é uma string separada por vírgulas. `*` é sempre
    ignorado/rejeitado — CORS irrestrito não é aceito nesta fundação.
    """
    raw = (settings.remote_cors_origins or "").strip()
    if not raw:
        return []
    origins = []
    for chunk in raw.split(","):
        origin = chunk.strip()
        if not origin or origin == "*":
            continue
        origins.append(origin)
    return origins