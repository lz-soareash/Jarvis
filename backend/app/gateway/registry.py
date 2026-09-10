"""Registro de peers do Gateway WAN (Fase 23) — estado do relé em memória.

O Gateway NÃO persiste nada (sem banco, sem sessões próprias). Ele mantém, por
processo (um único event loop), as conexões ativas e o mapeamento de roteamento:

- UMA conexão `core` (o Core/PC), pinada pela apresentação do `peer_token`;
- N conexões `mobile`, cada uma atrelada ao `device_id` decidido pelo Core no
  `auth_result` (o relé não decide confiança — apenas topa o que o Core aceitou).

Regras implementadas aqui (não em camadas superiores):
- um `device_id` só pode ter UMA conexão móvel ativa; um novo `auth_result`
  para o mesmo device expulsa a conexão antiga (kick por duplicata);
- `core` novo substitui o `core` antigo (evita "duplo core" após crash);
- todos os contadores são sanitizados no `snapshot()` — sem secrets/payloads.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("jarvis.gateway.registry")

ROLE_CORE = "core"
ROLE_MOBILE = "mobile"
ROLE_PENDING = "pending"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class Peer:
    peer_id: str
    role: str = ROLE_PENDING
    device_id: str | None = None
    session_id: str | None = None
    connected_at: datetime = field(default_factory=_utcnow)
    last_seen: datetime = field(default_factory=_utcnow)
    mailbox: Any = None  # Mailbox de saída (backpressure) — setado pelo server
    ws: Any = None  # transporta (starlette WebSocket ou stub de teste)
    peer_token_ok: bool = False  # handshake `core` validado (nunca logado)

    @property
    def is_core(self) -> bool:
        return self.role == ROLE_CORE

    @property
    def is_mobile(self) -> bool:
        return self.role == ROLE_MOBILE

    def touch(self) -> None:
        self.last_seen = _utcnow()


class GatewayRegistry:
    """Registro em memória de peers + roteamento por device_id (thread single)."""

    def __init__(self) -> None:
        self._peers: dict[str, Peer] = {}
        self._devices: dict[str, str] = {}  # device_id → peer_id (móveis)
        self._core_peer_id: str | None = None
        self._started_at = _utcnow()
        self.counters: dict[str, int] = {
            "auth_requests": 0,
            "auth_results": 0,
            "kicks": 0,
            "core_reconnects": 0,
            "relays": 0,
            "relays_dropped": 0,
            "oversized_envelopes": 0,
            "connect_rejected": 0,
        }

    # -- lifecycle -----------------------------------------------------------

    def register(self, mailboxes_factory) -> Peer:
        """Registra um novo peer (estado `pending` até o `hello`)."""
        peer_id = f"peer_{uuid.uuid4().hex[:12]}"
        peer = Peer(peer_id=peer_id)
        peer.mailbox = mailboxes_factory()
        self._peers[peer_id] = peer
        return peer

    def peer(self, peer_id: str) -> Peer | None:
        return self._peers.get(peer_id)

    def remove(self, peer_id: str) -> Peer | None:
        peer = self._peers.pop(peer_id, None)
        if peer is None:
            return None
        if self._core_peer_id == peer_id:
            self._core_peer_id = None
        if peer.device_id and self._devices.get(peer.device_id) == peer_id:
            self._devices.pop(peer.device_id, None)
        return peer

    def touch(self, peer_id: str) -> None:
        peer = self._peers.get(peer_id)
        if peer is not None:
            peer.touch()

    # -- papéis ---------------------------------------------------------------

    def promote_core(self, peer: Peer) -> Peer | None:
        """Promove o peer a `core`; expulsa o core anterior (se houver)."""
        previous = None
        old_id = self._core_peer_id
        if old_id is not None and old_id != peer.peer_id:
            old = self._peers.get(old_id)
            if old is not None:
                previous = old
        self._core_peer_id = peer.peer_id
        self.counters["core_reconnects"] += 1
        return previous

    def bind_device(self, peer: Peer, device_id: str, session_id: str | None) -> Peer | None:
        """Atrela o peer móvel ao device decidido pelo Core (kick de duplicata).

        Retorna o peer antigo (deve ser CLOSEd pelo chamador) quando o device já
        tinha outra conexão ativa.
        """
        peer.device_id = device_id
        peer.session_id = session_id
        peer.role = ROLE_MOBILE
        previous_id = self._devices.get(device_id)
        previous = None
        if previous_id is not None and previous_id != peer.peer_id:
            previous = self._peers.get(previous_id)
            if previous is not None:
                self._devices.pop(device_id, None)
                self.counters["kicks"] += 1
        self._devices[device_id] = peer.peer_id
        return previous

    # -- consultas ------------------------------------------------------------

    def core(self) -> Peer | None:
        if self._core_peer_id is None:
            return None
        return self._peers.get(self._core_peer_id)

    def mobile(self, device_id: str) -> Peer | None:
        peer_id = self._devices.get(device_id)
        if peer_id is None:
            return None
        return self._peers.get(peer_id)

    def mobiles(self) -> list[Peer]:
        return [p for p in self._peers.values() if p.is_mobile]

    # -- observabilidade -------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Estado sanitizado do relé — nunca secrets/payloads."""
        now = _utcnow()
        core = self.core()
        mobiles = self.mobiles()
        return {
            "started_at": self._started_at,
            "uptime_seconds": round((now - self._started_at).total_seconds(), 1),
            "core_connected": core is not None,
            "core_device_id": core.device_id if core else None,
            "mobiles": [
                {
                    "device_id": p.device_id,
                    "session_id": p.session_id,
                    "connected_at": p.connected_at,
                    "last_seen": p.last_seen,
                }
                for p in mobiles
            ],
            "mobile_count": len(mobiles),
            "peer_count": len(self._peers),
            "counters": dict(self.counters),
        }

    def reset(self) -> None:
        """Zera o registro (uso em testes)."""
        self._peers.clear()
        self._devices.clear()
        self._core_peer_id = None
        self._started_at = _utcnow()
        for k in self.counters:
            self.counters[k] = 0