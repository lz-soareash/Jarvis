"""Runtime do transporte remoto (Fase 12.1/12.3): ciclo de vida p/ lifespan.

Singleton `_agent`: quando `REMOTE_ENABLED=false` (padrão), `should_start()`
retorna False e **nenhum worker/conexão é criado**. Quando habilitado, o
lifespan inicia o `RemoteAgent` (conexão persistente com reconexão/backoff) e o
encerra graciosamente no shutdown.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.remote.agent import RemoteAgent
from app.remote.config import is_remote_enabled
from app.remote.connection import WebSocketConnection

logger = logging.getLogger("jarvis.remote")

_agent: RemoteAgent | None = None


def should_start() -> bool:
    """True apenas quando habilitado E configurado (endpoint + device id)."""
    if not is_remote_enabled():
        return False
    if not settings.remote_gateway_url or not settings.remote_device_id:
        logger.warning(
            "REMOTE_ENABLED=true mas remote_gateway_url/remote_device_id ausentes — transporte inativo"
        )
        return False
    return True


def _auth_headers() -> dict[str, str]:
    """Cabeçalho de autenticação do transporte (Bearer token, nunca logado)."""
    return {"Authorization": f"Bearer {settings.remote_device_token}"} if settings.remote_device_token else {}


def _connection_factory():
    """Cria uma nova conexão WebSocket para cada tentativa de conexão/reconexão."""
    return WebSocketConnection(
        settings.remote_gateway_url,
        headers=_auth_headers(),
        timeout=settings.remote_connect_timeout,
    )


def _build_agent() -> RemoteAgent:
    return RemoteAgent(
        device_id=settings.remote_device_id,
        device_token=settings.remote_device_token,
        connection_factory=_connection_factory,
        heartbeat_interval=float(settings.remote_heartbeat_interval),
        connect_timeout=settings.remote_connect_timeout,
        min_reconnect_delay=settings.remote_reconnect_min_delay,
        max_reconnect_delay=settings.remote_reconnect_max_delay,
        jitter=settings.remote_reconnect_jitter,
    )


async def start_remote_gateway() -> RemoteAgent | None:
    """Inicia o agente remoto. Retorna None quando inativo (padrão)."""
    global _agent
    if _agent is not None:
        return _agent
    if not should_start():
        return None
    agent = _build_agent()
    await agent.start()
    _agent = agent
    logger.info("Transporte remoto ativo (device=%s)", settings.remote_device_id)
    return agent


async def stop_remote_gateway() -> None:
    """Encerra graciosamente o agente remoto, se estiver ativo."""
    global _agent
    agent = _agent
    _agent = None
    if agent is not None:
        await agent.stop()
        logger.info("Transporte remoto encerrado")


def get_remote_gateway() -> RemoteAgent | None:
    """Agente remoto ativo (ou None) — usado por rotas/fases futuras."""
    return _agent


def get_remote_status() -> dict[str, Any] | None:
    """Snapshot de observabilidade sanitizado do agente (ou None se inativo)."""
    agent = _agent
    if agent is None:
        return None
    return agent.snapshot()


def reset_remote_gateway() -> None:
    """Zera o singleton (usado em testes/fixtures)."""
    global _agent
    _agent = None
