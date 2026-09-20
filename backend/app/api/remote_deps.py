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

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as OrmSession

from app.db.session import get_db
from app.remote.auth import (
    AuthenticatedDevice,
    RemoteAuthError,
    authenticate_bearer,
)
from app.remote.credentials import CredentialLimitError
from app.remote.errors import RemoteError
from app.remote.sessions import ensure_session_valid

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


# ---------------------------------------------------------------------------
# Fase 28.1 — autenticação HTTP remota (camada central comum)
# ---------------------------------------------------------------------------
# Identidade deriva EXCLUSIVAMENTE do `Authorization: Bearer <token>` (nunca de
# `device_id` alegado). A mesma regra dos contratos Bearer existentes
# (`/remote/auth`, `/remote/heartbeat`, `/remote/message`...): token → device →
# sessão, validando revogação/expiração/TTL/device-trust. Token nunca é logado.


def extract_bearer_token(request: Request) -> str | None:
    """Lê o token de `Authorization: Bearer`, sem revelá-lo na resposta."""
    auth = request.headers.get("authorization", "").strip()
    if not auth:
        return None
    scheme, _, value = auth.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _auth_error_http(exc: Exception) -> HTTPException:
    """Falhas de autenticação viram 401 (token/sessão) e 403 (device negado).

    `RemoteError` já carrega `http_status`/`message` sanitizados (mesmo contrato
    dos demais endpoints remotos); falhas genéricas AZ 401 sem revelar causa.
    """
    if isinstance(exc, RemoteError):
        return HTTPException(status_code=exc.http_status, detail=exc.message)
    return HTTPException(status_code=401, detail="autenticação necessária")


def optional_remote_device(
    request: Request,
    db: OrmSession = Depends(get_db),
) -> AuthenticatedDevice | None:
    """Valida `Authorization: Bearer` quando presente; `None` = caminho local.

    - header ausente            → None (request local/SPA, comportamento atual);
    - header presente + ok      → `AuthenticatedDevice`;
    - token inválido/revogado/expirado → 401;
    - device não autorizado     → 403.
    """
    token = extract_bearer_token(request)
    if not token:
        return None
    try:
        authed = authenticate_bearer(
            db, token, transport_meta={"http": True, "auth": "header"}
        )
        ensure_session_valid(db, authed.session)
        return authed
    except (RemoteAuthError, CredentialLimitError, RemoteError) as exc:
        raise _auth_error_http(exc) from exc


def require_remote_device(
    request: Request,
    db: OrmSession = Depends(get_db),
) -> AuthenticatedDevice:
    """Exige identidade remota: 401 quando o token está ausente/inválido."""
    authed = optional_remote_device(request, db)
    if authed is None:
        raise HTTPException(status_code=401, detail="autenticação necessária")
    return authed


def device_anchor_session(db: OrmSession, authed: AuthenticatedDevice) -> str:
    """Sessão JARVIS estável do device — escopo de posse de dados (Fase 28.1).

    Operações, histórico e aprovações remotas pertencem à sessão-âncora do
    device autenticado; abstrai `get_or_create_jarvis_session` para a API.
    """
    from app.remote.jarvis_session import get_or_create_jarvis_session

    return get_or_create_jarvis_session(db, authed.device)