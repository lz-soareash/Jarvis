"""Vocabulário estável de auditoria/observabilidade da identidade remota (Fase 12.2).

Regras de segurança reproduzidas aqui:
- Nunca registrar token, token_hash, código de pairing, senha ou header
  Authorization. Meta é sempre sanitizada (device_id, session_id, credential_id,
  status, razão, latência — nunca segredos).
- Serviços registram via `log_identity_event`, que serializa escrita via
  `_telemetry_lock` (SQLite :memory: compartilhado dos testes = StaticPool único).

Nomes de auditoria (AuditLog.action): device_created, device_authenticated,
device_authentication_failed, device_revoked, device_renamed,
device_capabilities_updated, credential_created,
credential_revoked, pairing_created, pairing_failed, pairing_succeeded,
session_authenticated, session_ended, session_shared.

Eventos observáveis (ExecutionEvent.event_type), prefixo `remote.*`/`device.*`:
remote.device.created, remote.device.registered, remote.device.renamed,
remote.device.revoked, remote.credential.created,
remote.credential.revoked, remote.pairing.created, remote.pairing.succeeded,
remote.pairing.failed, remote.auth.success, remote.auth.failed,
remote.session.ended, remote.session.shared,
device.connected, device.disconnected, device.reconnected (Fase 21).
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.session import SessionLocal
from app.services import audit, ops

logger = logging.getLogger("jarvis.remote")

# Auditoria
AUDIT_DEVICE_CREATED = "device_created"
AUDIT_DEVICE_AUTHENTICATED = "device_authenticated"
AUDIT_DEVICE_AUTH_FAILED = "device_authentication_failed"
AUDIT_DEVICE_REVOKED = "device_revoked"
AUDIT_DEVICE_RENAMED = "device_renamed"
AUDIT_DEVICE_CAPABILITIES = "device_capabilities_updated"
AUDIT_DEVICE_TRUSTED = "device_trusted"
AUDIT_CREDENTIAL_CREATED = "credential_created"
AUDIT_CREDENTIAL_REVOKED = "credential_revoked"
AUDIT_PAIRING_CREATED = "pairing_created"
AUDIT_PAIRING_FAILED = "pairing_failed"
AUDIT_PAIRING_SUCCEEDED = "pairing_succeeded"
AUDIT_SESSION_AUTHENTICATED = "session_authenticated"
AUDIT_SESSION_ENDED = "session_ended"
AUDIT_SESSION_SHARED = "session_shared"

# Observabilidade
OPS_DEVICE_CREATED = "remote.device.created"
OPS_DEVICE_REGISTERED = "remote.device.registered"
OPS_DEVICE_RENAMED = "remote.device.renamed"
OPS_DEVICE_REVOKED = "remote.device.revoked"
OPS_CREDENTIAL_CREATED = "remote.credential.created"
OPS_CREDENTIAL_REVOKED = "remote.credential.revoked"
OPS_PAIRING_CREATED = "remote.pairing.created"
OPS_PAIRING_SUCCEEDED = "remote.pairing.succeeded"
OPS_PAIRING_FAILED = "remote.pairing.failed"
OPS_AUTH_SUCCESS = "remote.auth.success"
OPS_AUTH_FAILED = "remote.auth.failed"
OPS_SESSION_ENDED = "remote.session.ended"
OPS_SESSION_SHARED = "remote.session.shared"
OPS_DEVICE_CONNECTED = "device.connected"
OPS_DEVICE_RECONNECTED = "device.reconnected"
OPS_DEVICE_DISCONNECTED = "device.disconnected"

TOOL = "remote.identity"

# As chamadas de identidade (pairing/auth/revoke) são síncronas dentro de um
# único processo: cada `commit()` é atômico e serializado pelo GIL, então a
# escrita de telemetria não intercala (ao contrário das tasks assíncronas da
# 12.1, que precisaram de `_telemetry_lock` na gateway).


def log_identity_event(
    *,
    audit_action: str,
    ops_event: str,
    allowed: bool = True,
    status: str = "ok",
    meta: dict[str, Any] | None = None,
    detail: str | None = None,
) -> None:
    """Grava audit + ExecutionEvent de forma best-effort (nunca derruba o fluxo)."""
    meta = meta or {}
    try:
        with SessionLocal() as db:
            audit.log_action(
                db,
                action=audit_action,
                tool=TOOL,
                allowed=allowed,
                detail=detail or f"meta={meta}",
            )
            ops.record_event(
                db,
                event_type=ops_event,
                provider="remote",
                status=status,
                meta=meta,
            )
    except Exception as exc:  # noqa: BLE001 — telemetria nunca bloqueia identidade
        logger.error("Falha ao registrar evento de identidade (%s): %s", audit_action, exc)
    # Fase 12.5 — streama ao barramento SSE (best-effort, sanitizado).
    from app.remote.events import publish_event

    publish_event(f"remote.identity.{audit_action}", {**meta, "status": status})