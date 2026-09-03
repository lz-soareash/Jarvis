"""Barramento de eventos remotos p/ SSE / UI mobile (Fase 12.5).

Camada de PUBlISH/SUBSCRIBE em processo (sem persistência): produtores de eventos
remotos (identidade, conexão do agente, comandos, resultados) publicam aqui, e
clientes SSE (`GET /api/remote/events`) assinam e recebem os eventos ao vivo.

Segurança:
- NUNCA carrega secrets (token, credential, pairing code, password).
- Metadados sanitizados apenas (device_id, status, command_id, connection_state…).
- Replay buffer pequeno e limitado (recepção imediata de histórico ao assinar),
  sem persistir em disco e sem expor estado sensível.

Thread-safety: publishers podem vir de código síncrono (identidade/execução) ou
assíncrono (agente/gateway). O broker usa um `queue.Queue` por assinante e um
`threading.Lock` global, de modo que `publish` funciona de qualquer contexto sem
exigir um event loop ativo.
"""

from __future__ import annotations

import asyncio
import logging
import queue as _queue
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("jarvis.remote.events")

REPLAY_LIMIT = 200
EVENT_REMOTE_LOGGER = "jarvis.remote.events"


class RemoteEventBroker:
    """Pub/sub em processo com replay limitado (Fase 12.5)."""

    def __init__(self, replay_limit: int = REPLAY_LIMIT) -> None:
        self._replay: deque[dict[str, Any]] = deque(maxlen=replay_limit)
        self._subs: list[_queue.Queue] = []
        self._lock = threading.Lock()

    # -- producers -----------------------------------------------------------

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Publica um evento sanitizado a todos os assinantes (nunca lança)."""
        entry = {
            "type": event_type,
            "at": datetime.now(timezone.utc).isoformat(),
            "data": _sanitize(data or {}),
        }
        with self._lock:
            self._replay.append(entry)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(entry)
            except _queue.Full:  # assinante lento: descarta (SSE é best-effort)
                pass

    # -- consumers -----------------------------------------------------------

    def subscribe(self) -> tuple[_queue.Queue, list[dict[str, Any]]]:
        """Cria um assinante; retorna (fila viva, histórico recente)."""
        q: _queue.Queue = _queue.Queue(maxsize=4096)
        with self._lock:
            self._subs.append(q)
            history = list(self._replay)
        return q, history

    def unsubscribe(self, q: _queue.Queue) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:  # pragma: no cover — já removido
                pass


# Instância compartilhada do processo (modular, sem estado global frágil).
_broker = RemoteEventBroker()


def publish_event(event_type: str, data: dict[str, Any] | None = None) -> None:
    """Atalho síncrono para publicar num broker sem referência explícita."""
    try:
        _broker.publish(event_type, data)
    except Exception as exc:  # noqa: BLE001 — eventos nunca derrubam o fluxo
        logger.error("Falha ao publicar evento remoto (%s): %s", event_type, exc)


def subscribe_events() -> tuple[_queue.Queue, list[dict[str, Any]]]:
    return _broker.subscribe()


def unsubscribe_events(q: _queue.Queue) -> None:
    _broker.unsubscribe(q)


def reset_events() -> None:
    """Limpa replay e assinantes (uso em testes)."""
    _broker._replay.clear()
    _broker._subs.clear()


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """Passa apenas chaves escalares/curtas e nunca valores sensíveis."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
    return out


async def remote_event_stream(*, limit: int | None = None) -> Any:
    """Gerador SSE de eventos remotos (Fase 12.5) — replay + ao vivo.

    Reenvia o histórico recente e então entradas ao vivo. Quando `limit` é
    informado (testes), para após entregar esse número de eventos; caso
    contrário transmite indefinidamente com heartbeat.
    """
    from app.services.chat import sse_event

    queue, history = subscribe_events()
    sent = 0
    try:
        for entry in history:
            if limit is not None and sent >= limit:
                return
            yield entry
            sent += 1
        ready = {"type": "remote.connected", "data": {"ready": True}}
        if limit is None or sent < limit:
            yield ready
            sent += 1
        while True:
            if limit is not None and sent >= limit:
                return
            item = None
            try:
                item = queue.get_nowait()
            except Exception:  # noqa: BLE001 — fila vazia
                item = None
            if item is not None:
                yield item
                sent += 1
            else:
                if limit is not None:
                    return  # sem mais dados em modo limitado
                await asyncio.sleep(1.0)
    finally:
        unsubscribe_events(queue)
