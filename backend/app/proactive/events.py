"""Proactive Agent (Fase 17) — barramento de eventos e deduplicação.

Este módulo define o contrato `ProactiveEvent` (tipado, extensível, SEM Enum
rígido), a sanitização obrigatória (nunca secrets), e a porta de entrada
`emit()` que grava no inbox com dedup por `event_id` (UNIQUE) antes de chamar
o engine. Entrega ao vivo (SSE) usa o MESMO broker pub/sub da camada remota
(`RemoteEventBroker`, reuso literal) — "distinguível por tipo", não por cópia.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.remote.events import RemoteEventBroker, consume_broker

logger = logging.getLogger("jarvis.proactive.events")

# Fontes canônicas (namespaces do catálogo de eventos). O catálogo é ABERTO:
# novos namespaces são bem-vindos, desde que sigam `ns.sub[.sub...]` e passem
# pela sanitização. Não há Enum rígido (requisito Fase 17).
SOURCE_SYSTEM = "system"
SOURCE_JARVIS = "jarvis"
SOURCE_AGENT = "agent"
SOURCE_WORKSPACE = "workspace"
SOURCE_COMPUTER = "computer"
SOURCE_REMOTE = "remote"

# Catálogo documental de eventos tipados (referência). Registros usados na
# Fase 17: `system.ready`, `system.shutdown`, `proactive.scheduled`.
EVENT_CATALOG = {
    "system.ready": "JARVIS iniciou (após lifespan startup).",
    "system.shutdown": "JARVIS encerrando (lifespan shutdown).",
    "system.error": "Erro interno de um subsistema, origem conhecida.",
    "session.created": "Nova sessão de chat criada.",
    "conversation.started": "Novo turno conversacional iniciado.",
    "message.received": "Mensagem recebida do usuário.",
    "tool.started": "Início de execução de uma ferramenta.",
    "tool.completed": "Ferramenta concluída com sucesso.",
    "tool.failed": "Ferramenta falhou (origem e erro sanitizados).",
    "task.started": "Tarefa agêntica iniciada.",
    "task.completed": "Tarefa agêntica concluída.",
    "task.failed": "Tarefa agêntica falhou.",
    "project.changed": "Alteração relevante em arquivos do workspace.",
    "project.test_failed": "Uma suíte de testes falhou.",
    "project.test_passed": "Uma suíte de testes passou.",
    "computer.perception": "Mudança de estado percebida no computador.",
    "computer.app_open": "Aplicativo aberto em primeiro plano.",
    "remote.device_connected": "Device remoto conectou.",
    "remote.device_disconnected": "Device remoto desconectou.",
    "remote.command_completed": "Comando remoto concluído.",
    "remote.command_failed": "Comando remoto falhou.",
    "proactive.scheduled": "Agendamento proativo disparou.",
}

# Fragmentos de nome de chave considerados sensíveis — descartados na
# sanitização (defensivo: qualquer payload, em qualquer namespace).
_SECRET_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "api_key",
    "apikey",
    "credential",
    "pairing",
    "authorization",
    "auth_",
    "_auth",
    "signature",
    "private_key",
    "privatekey",
    "client_secret",
    "cookie",
    "session_token",
)

_MAX_DEPTH = 3
_MAX_ITEMS = 32
_MAX_PAYLOAD_CHARS = 4096

_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)+$")
_PRIORITIES = {"low", "normal", "high", "critical"}


def _uuid() -> str:
    return str(uuid.uuid4())


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().strip()
    if not lowered:
        return True
    return any(fragment in lowered for fragment in _SECRET_FRAGMENTS)


def sanitize_payload(payload: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    """Recorta payload a dados escalares; descarta chaves sensíveis, listas
    longas e estruturas profundas (defensivo e determinístico)."""
    if not isinstance(payload, dict) or depth >= _MAX_DEPTH:
        return {}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or _is_sensitive_key(key):
            continue
        if isinstance(value, bool) or value is None:
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:256]
        elif isinstance(value, list):
            trimmed = [sanitize_payload({"v": v}, depth=depth + 1).get("v", v) if isinstance(v, dict) else v for v in value[: _MAX_ITEMS]]
            out[key] = [v for v in trimmed if isinstance(v, (str, int, float, bool)) or v is None]
        elif isinstance(value, dict):
            out[key] = sanitize_payload(value, depth=depth + 1)
    return out


def is_valid_event_type(event_type: str) -> bool:
    """Um tipo de evento é válido se parece `ns.sub[.sub...]` (namespace aberto)."""
    return isinstance(event_type, str) and bool(_EVENT_TYPE_RE.match(event_type))


def normalize_priority(priority: str | None) -> str:
    p = (priority or "normal").lower().strip()
    return p if p in _PRIORITIES else "normal"


@dataclass
class ProactiveEvent:
    """Contrato de evento proativo: tipado, identificável e já sanitizado."""

    event_id: str
    event_type: str
    source: str = SOURCE_SYSTEM
    priority: str = "normal"
    device_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# -- broker de entrega (reuso do pub/sub thread-safe da camada remota) -------
_broker = RemoteEventBroker()


def publish(delivery_type: str, data: dict[str, Any] | None = None) -> None:
    """Publica uma entrada de entrega (ex.: `proactive.message`) no broker."""
    try:
        _broker.publish(delivery_type, data)
    except Exception as exc:  # noqa: BLE001 — entrega nunca derruba o fluxo
        logger.error("Falha ao publicar entrega proativa (%s): %s", delivery_type, exc)


async def proactive_event_stream(*, limit: int | None = None) -> Any:
    """Gerador SSE de entradas proativas — replay + ao vivo (reuso `consume_broker`)."""
    async for entry in consume_broker(
        _broker, limit=limit, ready_event_type="proactive.connected"
    ):
        yield entry


def reset_broker() -> None:
    """Limpa replay/assinantes do broker proativo (uso em testes)."""
    _broker._replay.clear()
    _broker._subs.clear()


def emit(
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
    source: str = SOURCE_SYSTEM,
    priority: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    event_id: str | None = None,
) -> str | None:
    """Porta de entrada de um evento proativo (síncrona, nunca lança).

    Deduplica por `event_id` (UNIQUE) e delega ao engine para a decisão.
    Retorna `event_id` do evento processado, ou `None` se for inválido/dup.
    """
    if not is_valid_event_type(event_type):
        logger.warning("emit: event_type inválido ignorado: %r", event_type)
        return None
    eid = event_id or _uuid()
    from app.db.session import SessionLocal
    from app.models.proactive import ProactiveInboxEvent

    db = SessionLocal()
    row = None
    try:
        row = ProactiveInboxEvent(
            event_id=eid,
            event_type=event_type,
            source=source,
            priority=normalize_priority(priority),
            device_id=device_id,
            session_id=session_id,
            conversation_id=conversation_id,
            payload_json=json.dumps(
                sanitize_payload(payload or {}), ensure_ascii=False, default=str
            ),
        )
        db.add(row)
        try:
            db.commit()
        except Exception:  # noqa: BLE001 — IntegrityError de event_id duplicado
            db.rollback()
            return None
    finally:
        db.close()

    from app.proactive.engine import process_event

    try:
        process_event(row.id)
    except Exception:  # noqa: BLE001 — erro de decisão nunca derruba a fonte
        logger.exception("Erro processando evento proativo %s", eid)
    return eid