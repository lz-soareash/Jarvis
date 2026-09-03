"""Autenticação remota (Fase 12.2) — camada SEPARADA do transporte.

O transporte (WebSocketConnection) apenas entrega o token bruto; a identidade é
derivada EXCLUSIVAMENTE da credencial encontrada pelo hash — qualquer
`device_id` alegado pelo cliente é ignorado (anti-spoofing). Falhas não revelam
por que fração falhou (token/device/sessão) via mensagens indistinguíveis.

Nunca é registrado token, token_hash, senha nem header Authorization.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.core.enums import DeviceStatus
from app.models.remote import Credential, Device, RemoteSession
from app.models.session import ensure_utc, utcnow
from app.remote.credentials import get_credential_by_token
from app.remote.identity_events import (
    AUDIT_DEVICE_AUTHENTICATED,
    AUDIT_DEVICE_AUTH_FAILED,
    AUDIT_SESSION_AUTHENTICATED,
    OPS_AUTH_FAILED,
    OPS_AUTH_SUCCESS,
    log_identity_event,
)
from app.remote.sessions import create_session, touch_device_safe

logger = logging.getLogger("jarvis.remote")


class RemoteAuthError(ValueError):
    """Falha genérica de autenticação (mensagem sem detalhes de segurança)."""


@dataclass(slots=True)
class AuthenticatedDevice:
    """Resultado de autenticação — identidade provada por credencial + sessão.

    Carrega device/sessão para que o transporte NUNCA precise ver o token.
    """

    device: Device
    credential: Credential
    session: RemoteSession

    @property
    def device_id(self) -> str:
        return self.device.id

    @property
    def device_type(self) -> str:
        return self.device.device_type

    @property
    def status(self) -> str:
        return self.device.status

    @property
    def session_id(self) -> str:
        return self.session.id

    def sanitized_meta(self) -> dict[str, Any]:
        """Metadados observáveis da identidade (jamais segredos)."""
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "status": self.status,
            "session_id": self.session_id,
            "credential_id": self.credential.id,
        }


def authenticate_bearer(
    db: OrmSession,
    token: str,
    *,
    transport_meta: dict[str, Any] | None = None,
    claimed_device_id: str | None = None,
) -> AuthenticatedDevice:
    """Autentica um Bearer token e abre a sessão remota.

    `claimed_device_id` (se fornecido) é usado apenas para auditoria de
    discrepância — nunca participa da resolução de identidade.
    """
    failures: list[str] = []

    if not token or len(token) > 512:
        _record_failure(failures, "invalid_token", transport_meta, claimed_device_id)
        raise RemoteAuthError("autenticação negada")

    found = get_credential_by_token(db, token)
    if found is None:
        _record_failure(failures, "invalid_token", transport_meta, claimed_device_id)
        raise RemoteAuthError("autenticação negada")

    credential, device = found

    if credential.revoked_at is not None:
        failures.append("credential_revoked")
    if credential.expires_at is not None and ensure_utc(credential.expires_at) <= utcnow():
        failures.append("credential_expired")
    if device.status != DeviceStatus.ACTIVE.value:
        failures.append("device_not_active")
    if claimed_device_id and claimed_device_id != device.id:
        # Identidade NÃO muda: registra a tentativa de violação para auditoria.
        logger.warning(
            "Discrepância de identidade: cliente alegou device_id=%s (token é do device=%s)",
            claimed_device_id,
            device.id,
        )
        failures.append("claimed_device_id_mismatch")

    if failures:
        _record_failure(failures, ";".join(failures), transport_meta, claimed_device_id)
        raise RemoteAuthError("autenticação negada")

    # Sucesso: atualiza uso e abre a sessão.
    credential.last_used_at = utcnow()
    touch_device_safe(db, device)
    session = create_session(
        db,
        device=device,
        credential=credential,
        transport_meta=transport_meta,
    )

    auth_meta = {
        "device_id": device.id,
        "device_type": device.device_type,
        "session_id": session.id,
        "credential_id": credential.id,
    }
    log_identity_event(
        audit_action=AUDIT_DEVICE_AUTHENTICATED,
        ops_event=OPS_AUTH_SUCCESS,
        meta=auth_meta,
    )
    log_identity_event(
        audit_action=AUDIT_SESSION_AUTHENTICATED,
        ops_event=OPS_AUTH_SUCCESS,
        meta=auth_meta,
    )
    return AuthenticatedDevice(device=device, credential=credential, session=session)


def _record_failure(
    failures: list[str],
    reason: str,
    transport_meta: dict[str, Any] | None,
    claimed_device_id: str | None,
) -> None:
    meta: dict[str, Any] = {"reason": reason}
    if transport_meta:
        meta["transport"] = transport_meta
    if claimed_device_id:
        meta["claimed_device_id"] = claimed_device_id
    log_identity_event(
        audit_action=AUDIT_DEVICE_AUTH_FAILED,
        ops_event=OPS_AUTH_FAILED,
        allowed=False,
        status="failed",
        meta=meta,
    )
    logger.info("Autenticação remota negada (device=%s)", claimed_device_id)