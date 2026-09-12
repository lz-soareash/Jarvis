"""Runtime do Core Link WAN (Fase 23): singleton supervisionado em processo.

Espelha o padrão de `app.remote.runtime` (agente da Gateway do device) para o
lado inverso: o Core conecta ao relé como peer `core`. Toda autoridade
(auth, permissões, execução) permanece no Core — o relé apenas transporta.

`start_remote_link()` é chamado no lifespan do app quando
`REMOTE_GATEWAY_ENABLED=true`. A UI pode reconectar (novo URL) em runtime via
`POST /api/remote/gateway/connect`; o override de URL é persistido best-effort
em `data/gateway.runtime.json` (identidade e token NUNCA são persistidos/formata).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.remote.link import CoreLink

logger = logging.getLogger("jarvis.remote.link_runtime")

_link: CoreLink | None = None


def _make_factory(url: str):
    """Retorna um factory de conexão WebSocket ligado ao URL efetivo do relé."""

    def _factory():
        from app.remote.connection import WebSocketConnection

        return WebSocketConnection(
            url=url,
            headers={},
            timeout=settings.remote_gateway_connect_timeout_seconds,
            recv_timeout=max(settings.remote_gateway_heartbeat_seconds * 3.0, 90.0),
        )

    return _factory


def should_start() -> bool:
    return (
        bool(settings.remote_gateway_enabled)
        and bool(settings.remote_gateway_url)
        and bool(settings.remote_gateway_peer_token)
        and bool(settings.remote_device_id)
    )


def resolve_url() -> str:
    override = _prefs().url()
    return override or (settings.remote_gateway_url or "")


async def start_remote_link(url: str | None = None) -> CoreLink | None:
    """Sobe o core link (idempotente). `url` opcional sobrescreve o config."""
    global _link
    if not settings.remote_gateway_enabled:
        return None
    effective = (url or None) or resolve_url()
    if not (effective and settings.remote_gateway_peer_token and settings.remote_device_id):
        return None
    if _link is not None:
        if _link._gateway_url == effective:  # noqa: SLF001
            return _link
        await stop_remote_link()
    if url:
        _prefs().save(effective)
    _link = CoreLink(
        device_id=settings.remote_device_id,
        gateway_url=effective,
        peer_token=settings.remote_gateway_peer_token,
        connection_factory=_make_factory(effective),
        heartbeat_interval=settings.remote_gateway_heartbeat_seconds,
        connect_timeout=settings.remote_gateway_connect_timeout_seconds,
        max_reconnect_delay=settings.remote_gateway_max_backoff_seconds,
        reconnect_enabled=settings.remote_gateway_reconnect_enabled,
        message_timeout=settings.remote_gateway_message_timeout_seconds,
        queue_ttl=settings.remote_gateway_queue_ttl_seconds,
    )
    await _link.start()
    return _link


async def stop_remote_link() -> None:
    global _link
    link = _link
    _link = None
    if link is not None:
        await link.stop()


def get_remote_link() -> CoreLink | None:
    return _link


async def get_remote_link_status() -> dict[str, Any]:
    """Snapshot sanitizado p/ UI/ops (nunca secrets)."""
    running = _link is not None
    base = {
        "enabled": bool(settings.remote_gateway_enabled),
        "configured": should_start(),
        "running": running,
        "transport": "gateway_wan",
        "url": resolve_url(),
    }
    if running and _link is not None:
        snapshot = _link.snapshot()
        snapshot.pop("enabled", None)
        base.update(snapshot)
        return base
    base.update(
        {
            "connection": "disconnected",
            "connection_state": "disabled",
            "authenticated": False,
            "healthy": False,
            "device_id": settings.remote_device_id,
            "connected_at": None,
            "last_heartbeat": None,
            "last_state_change": None,
            "reconnect_count": 0,
            "last_error": "link não iniciado",
            "last_error_code": None,
            "revocation": None,
            "devices_bound": 0,
            "mobile_heartbeats": {},
            "latency": {"relay_rtt_ms": None, "message_latency_ms": None},
            "queued_proactive": 0,
            "counters": {},
        }
    )
    return base


def reset_remote_link() -> None:
    """Zera o singleton (usado em testes/fixtures)."""
    global _link
    _link = None


def _prefs():
    from app.remote.gateway_prefs import GatewayPrefs

    return GatewayPrefs()