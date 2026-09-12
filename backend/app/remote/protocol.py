"""Protocolo de transporte com a Gateway (Fase 12.1).

Envelope versionado: {version, type, message_id, device_id, command_id,
timestamp, payload}. Este módulo é puro (sem rede, sem I/O) — apenas construi,
serializa e valida mensagens, para ser testável hermeticamente.

Segurança: campos desconhecidos são rejeitados (`extra="forbid"`); versão e
tipo desconhecidos geram erros específicos em vez de falhar silenciosamente.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("jarvis.remote.protocol")

# Versão do protocolo de transporte. Mudanças incompatíveis incrementam; o
# cliente rejeita envelopes de outras versões (UnsupportedVersionError).
REMOTE_PROTOCOL_VERSION = 1


class MessageType(str, Enum):
    HELLO = "hello"
    HELLO_ACK = "hello_ack"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    COMMAND = "command"
    COMMAND_ACK = "command_ack"
    COMMAND_RESULT = "command_result"
    ERROR = "error"
    # Fase 23 — WAN relay (tipos ADITIVOS; não alteram o protocolo v1 existente).
    # AUTH/AUTH_RESULT: handshake do cliente fino; o relé encaminha ao Core e a
    # autoridade de confiança continua sendo o Core (trust relay).
    AUTH = "auth"
    AUTH_RESULT = "auth_result"
    # MESSAGE/MESSAGE_ACK/MESSAGE_RESULT: conversação WAN (reusa o AI Core).
    MESSAGE = "message"
    MESSAGE_ACK = "message_ack"
    MESSAGE_RESULT = "message_result"
    # AGENT_EVENT: stream/eventos do Core → cliente fino (chunks, progresso,
    # approval_required, conclusão) durante mensagens/tarefas de Computer Use.
    AGENT_EVENT = "agent_event"
    # COMPUTER_TASK/COMPUTER_RESULT: Computer Use remoto (reusa Computer Agent).
    COMPUTER_TASK = "computer_task"
    COMPUTER_RESULT = "computer_result"
    # APPROVAL_RESPOND/APPROVAL_RESULT: decisão de aprovação via WAN (reusa o
    # mesmo fluxo de `/api/approvals/{id}/respond`).
    APPROVAL_RESPOND = "approval_respond"
    APPROVAL_RESULT = "approval_result"
    # Fase 25 — VEGA Mobile Control: comando de dispositivo Core → móvel
    # (MOBILE_COMMAND) e seu resultado móvel → Core (reusa COMMAND_RESULT).
    # A autoridade de execução permanece NO MÓVEL (allowlist local + permissões
    # do SO); o Core valida contra o registry de capabilities e correlaciona.
    MOBILE_COMMAND = "mobile_command"
    # CLOSE: encerramento cooperativo do peer (sanitizado pelo relé).
    CLOSE = "close"


class UnsupportedVersionError(ValueError):
    """Envelope chegou com versão de protocolo não suportada."""


class UnknownMessageTypeError(ValueError):
    """Envelope chegou com `type` fora do vocabulário conhecido."""


class InvalidEnvelopeError(ValueError):
    """Envelope não é JSON válido ou não satisfaz o schema."""


class RemoteEnvelope(BaseModel):
    """Mensagem do protocolo — contrato rígido (sem campos extras)."""

    model_config = {"extra": "forbid"}

    version: int
    type: MessageType
    message_id: str  # corrrelação/rastreabilidade (gerado por build_message)
    device_id: str | None = None
    command_id: str | None = None
    # Fase 23 — correlação de operações WAN (mensagens/tarefas/approvals).
    # ADITIVO: peers da v1 (Fase 12) não o enviam e o campo é opcional.
    request_id: str | None = None
    timestamp: str  # ISO-8601 UTC (gerado por build_message)
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_command(self) -> bool:
        return self.type == MessageType.COMMAND


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_message(
    msg_type: MessageType,
    *,
    device_id: str | None = None,
    message_id: str | None = None,
    command_id: str | None = None,
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> RemoteEnvelope:
    """Constrói um envelope válido com as convenções do protocolo."""
    return RemoteEnvelope(
        version=REMOTE_PROTOCOL_VERSION,
        type=msg_type,
        message_id=message_id or str(uuid4()),
        device_id=device_id,
        command_id=command_id,
        request_id=request_id,
        timestamp=_now_iso(),
        payload=payload or {},
    )


def encode_message(envelope: RemoteEnvelope) -> str:
    """Serializa um envelope para a wire format (JSON)."""
    return envelope.model_dump_json()


def decode_message(raw: str) -> RemoteEnvelope:
    """Desserializa e valida uma mensagem recebida.

    Erros específicos: UnsupportedVersionError, UnknownMessageTypeError e
    InvalidEnvelopeError (JSON quebrado ou schema violado).
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidEnvelopeError(f"mensagem não é JSON válido: {exc}") from exc

    if not isinstance(data, dict):
        raise InvalidEnvelopeError("envelope deve ser um objeto JSON")

    version = data.get("version")
    if version != REMOTE_PROTOCOL_VERSION:
        raise UnsupportedVersionError(
            f"versão do protocolo não suportada: {version!r} (esperado {REMOTE_PROTOCOL_VERSION})"
        )

    msg_type = data.get("type")
    if msg_type not in {member.value for member in MessageType}:
        raise UnknownMessageTypeError(f"tipo de mensagem desconhecido: {msg_type!r}")

    try:
        return RemoteEnvelope.model_validate(data)
    except ValidationError as exc:
        raise InvalidEnvelopeError(f"envelope inválido: {exc}") from exc