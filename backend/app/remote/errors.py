"""Erros estruturados da camada remota (Fase 16).

Taxonomia única e reutilizável pelo transporte HTTP remoto. Regras:

- toda falha vira `RemoteError` com `code`, `http_status` e `message` sanitizada
  (NUNCA stack trace, paths internos ou detalhes que ajudem um atacante);
- detalhes técnicos completos ficam apenas nos logs internos;
- o handler global converte `RemoteError` no contrato do protocolo:
  `{"type": "error", "request_id": ..., "code": ..., "message": ...}`;
- `RemoteDisabled` é o guard padrão: acesso com a camada OFF é rejeitado antes
  de qualquer processamento (503).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger("jarvis.remote")


class RemoteErrorCode(str, Enum):
    """Códigos de erro estáveis do contrato remoto (Fase 16, seção 26)."""

    REMOTE_DISABLED = "REMOTE_DISABLED"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_SESSION = "INVALID_SESSION"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SESSION_REVOKED = "SESSION_REVOKED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RemoteError(Exception):
    """Falha remota já sanitizada.

    `message` é o que o cliente pode ver (sem stack trace). `detail` (opcional)
    é apenas para logs internos — nunca chega ao cliente.
    """

    def __init__(
        self,
        code: RemoteErrorCode,
        message: str,
        *,
        http_status: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status or _DEFAULT_STATUS[code]
        self.detail = detail

    def to_body(self, request_id: str | None = None) -> dict[str, Any]:
        """Corpo de erro já formatado segundo o contrato do protocolo."""
        body: dict[str, Any] = {
            "type": "error",
            "code": self.code.value,
            "message": self.message,
        }
        if request_id:
            body["request_id"] = request_id
        return body


class RemoteDisabledError(RemoteError):
    """Camada remota desligada (padrão) — acesso deve ser rejeitado."""

    def __init__(self, message: str = "Remote desabilitado (REMOTE_ENABLED=false)") -> None:
        super().__init__(RemoteErrorCode.REMOTE_DISABLED, message, detail="remote_enabled=false")


_DEFAULT_STATUS = {
    RemoteErrorCode.REMOTE_DISABLED: 503,
    RemoteErrorCode.UNAUTHORIZED: 401,
    RemoteErrorCode.FORBIDDEN: 403,
    RemoteErrorCode.INVALID_REQUEST: 400,
    RemoteErrorCode.INVALID_SESSION: 401,
    RemoteErrorCode.SESSION_EXPIRED: 401,
    RemoteErrorCode.SESSION_REVOKED: 401,
    RemoteErrorCode.PAYLOAD_TOO_LARGE: 413,
    RemoteErrorCode.RATE_LIMITED: 429,
    RemoteErrorCode.TIMEOUT: 504,
    RemoteErrorCode.INTERNAL_ERROR: 500,
}