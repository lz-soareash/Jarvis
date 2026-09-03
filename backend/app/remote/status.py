"""Estado observável do agente remoto (Fase 12.3) — SÓ observabilidade.

Nunca expõe token, credential, pairing code, password ou payload sensível.
`GET /remote/status` é leitura pura e não modifica estado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class RemoteHealthState:
    """Instante observável de um agente remoto (imutável p/ leitura)."""

    enabled: bool
    connection_state: str  # ConnectionState (reutilizado)
    authenticated: bool
    healthy: bool
    device_id: str | None = None
    connected_at: datetime | None = None
    last_heartbeat: datetime | None = None
    reconnect_count: int = 0
    last_error: str | None = None
    pending_commands: int = 0
    active_session: str | None = None  # id sessão remota ativa (sanitizado)
    revocation: str | None = None  # "revoked" quando credencial revogada

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "connection_state": self.connection_state,
            "authenticated": self.authenticated,
            "healthy": self.healthy,
            "device_id": self.device_id,
            "connected_at": self.connected_at,
            "last_heartbeat": self.last_heartbeat,
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
            "pending_commands": self.pending_commands,
            "active_session": self.active_session,
            "revocation": self.revocation,
        }
