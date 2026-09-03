"""Transporte remoto (Fase 12.1) — comunicação segura com a Gateway.

Padrões de segurança desta fase:
- o PC é sempre CLIENTE (WebSocket outbound); nenhuma porta de entrada;
- o transporte nunca executa ferramentas (command → ACK not_implemented);
- tokens jamais aparecem em logs, auditoria ou ExecutionEvent;
- testes herméticos usam transporte em memória (sem rede).
"""

from app.remote.gateway import RemoteGateway
from app.remote.manager import RemoteConnectionManager
from app.remote.protocol import (
    REMOTE_PROTOCOL_VERSION,
    InvalidEnvelopeError,
    MessageType,
    RemoteEnvelope,
    UnknownMessageTypeError,
    UnsupportedVersionError,
    build_message,
    decode_message,
    encode_message,
)

__all__ = [
    "REMOTE_PROTOCOL_VERSION",
    "RemoteGateway",
    "RemoteConnectionManager",
    "RemoteEnvelope",
    "MessageType",
    "build_message",
    "encode_message",
    "decode_message",
    "UnsupportedVersionError",
    "UnknownMessageTypeError",
    "InvalidEnvelopeError",
]