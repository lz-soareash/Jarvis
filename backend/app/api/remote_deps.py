"""Dependências HTTP da camada remota (Fase 16).

`RemoteRequestContext` fornece o `request_id` obrigatório a toda operação remota:

- lido do header `X-Request-ID` (quando é um UUID bem-formado) — permite ao
  cliente correlacionar logs/streams/auditoria;
- gerado pelo servidor quando ausente/inválido;
- ecoado de volta no header `X-Request-ID` da resposta;
- reutilizado em auditoria, eventos SSE e corpos de resposta/erro.

Nunca se depende de timestamp como identificador (colisão/rastreio fracos).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RemoteRequestContext:
    """Contexto correlacional de uma operação remota (nunca carries secrets)."""

    request_id: str
    session_id: str | None = None
    device_id: str | None = None
    credential_id: str | None = None

    def audit_detail(self, extra: str | None = None) -> str:
        parts = [f"request={self.request_id}"]
        if self.session_id:
            parts.append(f"remote_session={self.session_id}")
        if self.device_id:
            parts.append(f"device={self.device_id}")
        if extra:
            parts.append(extra)
        return "; ".join(parts)

    def sse_meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {"request_id": self.request_id}
        if self.session_id:
            meta["session_id"] = self.session_id
        if self.device_id:
            meta["device_id"] = self.device_id
        return meta


def get_remote_context(request: Request, response: Response) -> RemoteRequestContext:
    """Extrai/gera o `request_id` e reflete o header na resposta."""
    raw = request.headers.get("x-request-id", "").strip()
    request_id = raw if raw and _UUID_RE.match(raw) else str(uuid.uuid4())
    response.headers["X-Request-ID"] = request_id
    return RemoteRequestContext(request_id=request_id)


def expose_request_id(context: RemoteRequestContext, body: dict[str, Any]) -> dict[str, Any]:
    """Injeta `request_id` num dict de resposta (protocolo)."""
    body["request_id"] = context.request_id
    if context.session_id and "session_id" not in body:
        body["session_id"] = context.session_id
    return body