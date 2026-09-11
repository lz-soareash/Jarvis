"""Camada de transporte WebSocket do Gateway WAN (Fase 23).

Cada conexão aceita vira um `Peer` com uma `Mailbox` de saída (backpressure). O
loop de RX lê envelopes (com timeout de ociosidade), aplica o teto de payload e
entrega ao `RelayHub`; o loop de TX drena a mailbox e empurra para o cliente.
Nenhum segredo/payload passa a logs. Uvicorn `[standard]` fornece a dependência
`websockets`/`uvicorn.protocols.websockets`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.gateway.backpressure import Mailbox
from app.gateway.config import get_settings
from app.gateway.hub import RelayHub
from app.remote.protocol import MessageType, RemoteEnvelope, decode_message, encode_message

logger = logging.getLogger("jarvis.gateway.server")

# Códigos de fechamento WebSocket (ranges do protocolo/política).
CLOSE_POLICY = 1008
CLOSE_TOO_BIG = 1009
CLOSE_UNPARSEABLE = 1003
CLOSE_IDLE = 4408


def _client_host(websocket) -> str | None:
    client = getattr(websocket, "client", None)
    return getattr(client, "host", None)


def _brief(exc: BaseException, limit: int = 120) -> str:
    return f"{type(exc).__name__}: {exc}"[:limit]


async def remote_ws_endpoint(websocket: Any) -> None:
    """Endpoint `WS /api/remote/ws` — aceita, classifica e roteia envelopes."""
    settings = get_settings()
    hub: RelayHub = websocket.app.state.gateway_hub

    origin = _client_host(websocket)
    if not hub.limits.allow_connect(origin):
        hub.registry.counters["connect_rejected"] += 1
        await websocket.close(code=CLOSE_POLICY)
        return
    await websocket.accept()

    peer = hub.registry.register(Mailbox)
    peer.ws = websocket
    peer_id = peer.peer_id
    mailbox = peer.mailbox

    rx = asyncio.create_task(_rx(websocket, hub, peer_id, mailbox))
    tx = asyncio.create_task(_tx(websocket, peer_id, mailbox))
    try:
        done, pending = await asyncio.wait(
            {rx, tx}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            try:
                await task
            except Exception as exc:  # noqa: BLE001 — transporte frágil
                logger.info("Peer %s encerrado: %s", peer_id, _brief(exc))
    finally:
        hub.registry.remove(peer_id)
        hub.limits.release_connect(origin)
        mailbox.close()
        logger.debug("Peer desconectado (peer=%s)", peer_id)


async def _rx(websocket: Any, hub: RelayHub, peer_id: str, mailbox: Mailbox) -> None:
    settings = get_settings()
    while True:
        try:
            raw = await asyncio.wait_for(
                websocket.receive_text(), timeout=settings.idle_timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.info("Peer ocioso (peer=%s) — fechando", peer_id)
            await websocket.close(code=CLOSE_IDLE)
            return
        except Exception:  # noqa: BLE001 — cliente sumiu / ws fechou
            return

        if not hub.limits.allow_payload(len(raw)):
            logger.warning("Envelope acima do teto (peer=%s)", peer_id)
            hub.registry.counters["oversized_envelopes"] += 1
            await websocket.close(code=CLOSE_TOO_BIG)
            return

        try:
            envelope = decode_message(raw)
        except Exception:  # noqa: BLE001 — envelope inválido → fecha 1003
            await websocket.close(code=CLOSE_UNPARSEABLE)
            return

        try:
            reply = await hub.handle(envelope, peer_id)
        except Exception as exc:  # noqa: BLE001 — hub nunca derruba um peer sozinho
            logger.error("Falha no hub (%s): %s", peer_id, _brief(exc))
            continue
        if reply is not None:
            await _safe_send(websocket, encode_message(reply))
        if envelope.type == MessageType.CLOSE:
            return


async def _tx(websocket: Any, peer_id: str, mailbox: Mailbox) -> None:
    """Drena a mailbox p/ o cliente — acordado por evento (sem busy-wait 20ms)."""
    wake = mailbox.wake_event()
    while True:
        item = mailbox.get()
        if item is not None:
            await _safe_send(websocket, encode_message(item))
            if getattr(item, "type", None) == MessageType.CLOSE:
                return
            continue
        if mailbox._closed:  # noqa: SLF001 — encerramento gracioso
            return
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            continue
        except Exception:  # noqa: BLE001 — cliente sumiu durante a espera
            return


async def _safe_send(websocket: Any, text: str) -> None:
    try:
        await websocket.send_text(text)
    except Exception:  # noqa: BLE001 — cliente desconectado durante o envio
        pass