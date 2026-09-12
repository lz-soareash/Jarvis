"""Núcleo de roteamento do Gateway WAN (Fase 23) — RELÉ puro.

Este módulo implementa TODAS as regras do relé:

- `hello` classifica o peer: `core` (exige `peer_token` compartilhado com o
  Core — comparação de tempo constante) ou `mobile`;
- peer móvel em estado pendente só pode enviar `auth`/`heartbeat`/`close`;
- peer móvel atrelado só pode enviar `message`/`computer_task`/
  `approval_respond`/`message_ack`/`heartbeat`/`auth`/`close`;
- peer `core` roteia para o móvel indicado por `payload["target_device_id"]`
  (resultados/eventos/acks) — sem target válido, o envelope é descartado e
  contado (`relays_dropped`), NUNCA rebatido de volta ao Core;
- móvel NUNCA roteia para outro móvel (uma hop: móvel → core → móvel);
- o relé NÃO decide confiança: um device só fica "atrelado" depois de um
  `auth_result` `ok:true` proveniente do Core (trust relay).

Respostas imediatas são retornadas por `handle()` (enviadas na MESMA conexão);
tudo que é roteado vai para a `Mailbox` do peer destino (backpressure). Quando
um device/Core é substituído, o relé fecha com graça (CLOSE) a conexão antiga.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from app.gateway.backpressure import Mailbox
from app.gateway.config import get_settings
from app.gateway.limits import GatewayLimits
from app.gateway.registry import (
    ROLE_CORE,
    ROLE_MOBILE,
    ROLE_PENDING,
    GatewayRegistry,
)
from app.remote.protocol import MessageType, RemoteEnvelope, build_message

logger = logging.getLogger("jarvis.gateway.hub")

# Envelopes que um móvel pode enviar ANTES de ser atrelado pelo Core.
_MOBILE_PENDING_ALLOWED = {
    MessageType.AUTH,
    MessageType.HEARTBEAT,
    MessageType.CLOSE,
}

# Envelopes que um móvel A TRILHADO pode enviar ao Core.
_MOBILE_BOUND_ALLOWED = {
    MessageType.AUTH,
    MessageType.MESSAGE,
    MessageType.MESSAGE_ACK,
    MessageType.COMPUTER_TASK,
    MessageType.APPROVAL_RESPOND,
    # Fase 25 — resultado de comando de dispositivo móvel → Core.
    MessageType.COMMAND_RESULT,
    MessageType.HEARTBEAT,
    MessageType.CLOSE,
}

# Envelopes de aplicação vindos do Core que precisam de destino (mobile).
_CORE_ROUTED = {
    MessageType.AUTH_RESULT,
    MessageType.MESSAGE_RESULT,
    MessageType.AGENT_EVENT,
    MessageType.COMPUTER_RESULT,
    MessageType.APPROVAL_RESULT,
    MessageType.COMMAND_RESULT,
    # Fase 25 — comando de dispositivo móvel enviado pelo Core ao móvel.
    MessageType.MOBILE_COMMAND,
    # Fase 24 — ACK de heartbeat WAN do Core é ROTEÁVEL ao móvel de destino.
    MessageType.HEARTBEAT_ACK,
}

# Envelopes de aplicação do móvel cujo roteamento ao Core é rate-limited no relé
# (defesa local mínima; a autoridade de limitação REAL continua no Core).
_RATE_LIMITED_MOBILE_TYPES = {
    MessageType.MESSAGE,
    MessageType.COMPUTER_TASK,
    MessageType.APPROVAL_RESPOND,
}


class RelayHub:
    """Relé: roteia envelopes entre o Core e os móveis (uma hop)."""

    def __init__(
        self,
        registry: GatewayRegistry | None = None,
        limits: GatewayLimits | None = None,
    ) -> None:
        self.registry = registry or GatewayRegistry()
        self.limits = limits or GatewayLimits()
        self.settings = get_settings()
        # AUTH em vôo: request_id do móvel -> (peer_id, instante). O AUTH_RESULT
        # do Core chega ANTES de o device ser atrelado, então não há como rotear
        # por device_id. TTL evita vazamento de memória se o Core não responder.
        self._pending_auths: dict[str, tuple[str, float]] = {}

    # -- pontos de entrada ----------------------------------------------------

    async def handle(
        self, envelope: RemoteEnvelope, peer_id: str
    ) -> RemoteEnvelope | None:
        """Processa um envelope recebido do peer `peer_id`.

        Retorna a resposta a enviar na MESMA conexão (ex.: `hello_ack`,
        `heartbeat_ack`, `error`) ou None. Envelopes roteados para outros peers
        são postos nas respectivas mailboxes pelo próprio hub.
        """
        peer = self.registry.peer(peer_id)
        if peer is None:
            return None
        self.registry.touch(peer_id)
        self._sweep_pending_auths()

        if envelope.type == MessageType.HELLO:
            return self._on_hello(envelope, peer)

        if peer.role == ROLE_PENDING:
            return self._error(envelope, "handshake_pending", "envie hello antes de operar")

        if peer.role == ROLE_CORE:
            return await self._from_core(envelope, peer)

        if peer.role == ROLE_MOBILE:
            return await self._from_mobile(envelope, peer)

        return self._error(envelope, "unknown_role", "papel desconhecido")

    def mailbox_for(self, peer_id: str) -> Mailbox | None:
        peer = self.registry.peer(peer_id)
        return peer.mailbox if peer is not None else None

    # -- hello (classificação de papel) ---------------------------------------

    def _on_hello(self, envelope: RemoteEnvelope, peer) -> RemoteEnvelope:
        payload = envelope.payload or {}
        role = payload.get("role")
        if role == ROLE_CORE:
            return self._hello_core(envelope, peer, payload)
        if role == ROLE_MOBILE:
            peer.role = ROLE_MOBILE
            logger.info("Peer móvel conectado (peer=%s)", peer.peer_id)
            return build_message(
                MessageType.HELLO_ACK,
                device_id=envelope.device_id,
                request_id=envelope.request_id,
                payload={"role": ROLE_MOBILE, "ok": True},
            )
        return self._error(envelope, "invalid_hello", "role inválido no hello")

    def _hello_core(self, envelope: RemoteEnvelope, peer, payload: dict[str, Any]) -> RemoteEnvelope:
        presented = str(payload.get("peer_token") or "")
        configured = self.settings.peer_token or ""
        if not configured:
            logger.error("GATEWAY_PEER_TOKEN não configurado — core não pode conectar")
            return self._error(envelope, "core_denied", "gateway sem peer_token configurado")
        if not secrets.compare_digest(presented, configured):
            logger.warning("Handshake de core rejeitado (token inválido)")
            return self._error(envelope, "core_denied", "peer_token inválido")

        previous = self.registry.promote_core(peer)
        peer.role = ROLE_CORE
        peer.peer_token_ok = True
        peer.device_id = payload.get("device_id")
        if previous is not None:
            self._graceful_close(previous, reason="core_replaced")
            logger.info("Core anterior substituído (peer=%s)", previous.peer_id)
        logger.info("Core conectado ao relé (peer=%s)", peer.peer_id)
        return build_message(
            MessageType.HELLO_ACK,
            device_id=envelope.device_id,
            request_id=envelope.request_id,
            payload={"role": ROLE_CORE, "ok": True},
        )

    # -- roteamento: core → móveis --------------------------------------------

    async def _from_core(self, envelope: RemoteEnvelope, peer) -> RemoteEnvelope | None:
        if envelope.type == MessageType.HEARTBEAT:
            return build_message(
                MessageType.HEARTBEAT_ACK,
                device_id=envelope.device_id,
                request_id=envelope.request_id,
                payload={"ok": True},
            )

        target = (envelope.payload or {}).get("target_device_id") or envelope.device_id
        if envelope.type in _CORE_ROUTED:
            if envelope.type == MessageType.AUTH_RESULT:
                mobile = self._resolve_auth_result_target(
                    envelope.request_id, target, peer
                )
            else:
                mobile = self._resolve_target_peer(target)
            if mobile is None or mobile.mailbox is None:
                self.registry.counters["relays_dropped"] += 1
                return None
            if envelope.type == MessageType.AUTH_RESULT:
                if envelope.request_id:
                    self._pending_auths.pop(envelope.request_id, None)
                self._apply_auth_result(envelope, mobile)
            if mobile.mailbox.put(envelope):
                self.registry.counters["relays"] += 1
            return None

        # Envelope de controle do core sem destino — não roteável.
        self.registry.counters["relays_dropped"] += 1
        return None

    def _resolve_target_peer(self, target: str | None):
        if not target:
            return None
        return self.registry.mobile(target)

    def _resolve_auth_result_target(self, request_id: str | None, target: str | None, core_peer):
        """Localiza o móvel que aguarda o AUTH_RESULT.

        O AUTH_RESULT chega ANTES de o device estar atrelado, então primeiro
        resolve pelo `request_id` do AUTH em vôo; sem correlação, tenta por
        `device_id` já atrelado (re-auth) e, em último caso, por `source`.
        """
        if request_id:
            pending = self._pending_auths.get(request_id)
            if pending is not None:
                mobile = self.registry.peer(pending[0])
                if mobile is not None:
                    return mobile
        return self._resolve_target_peer(target)

    def _apply_auth_result(self, envelope: RemoteEnvelope, mobile) -> None:
        payload = envelope.payload or {}
        self.registry.counters["auth_results"] += 1
        if not payload.get("ok"):
            return
        device_id = payload.get("target_device_id") or payload.get("device_id")
        if not device_id:
            return
        previous = self.registry.bind_device(mobile, device_id, payload.get("session_id"))
        if previous is not None and previous.peer_id != mobile.peer_id:
            self._graceful_close(previous, reason="device_replaced")
            logger.info("Móvel duplicado substituído (device=%s)", device_id)

    # -- roteamento: móveis → core --------------------------------------------

    async def _from_mobile(self, envelope: RemoteEnvelope, peer) -> RemoteEnvelope | None:
        if not peer.device_id:
            if envelope.type not in _MOBILE_PENDING_ALLOWED:
                return self._error(envelope, "not_authenticated", "device ainda não atrelado")
            # Fase 24 — móvel pendente não perturba o Core: heartbeat respondido
            # INLINE pelo relé (o Core só vê heartbeats de devices já atrelados).
            if envelope.type == MessageType.HEARTBEAT:
                return build_message(
                    MessageType.HEARTBEAT_ACK,
                    device_id=envelope.device_id,
                    request_id=envelope.request_id,
                    payload={"ok": True, "pending": True},
                )
        else:
            if envelope.type not in _MOBILE_BOUND_ALLOWED:
                self.registry.counters["peer_messages_rejected"] = (
                    self.registry.counters.get("peer_messages_rejected", 0) + 1
                )
                return self._error(envelope, "forbidden", "tipo não permitido para dispositivo")
            # Defesa local mínima: taxa de mensagens por peer no próprio relé.
            if envelope.type in _RATE_LIMITED_MOBILE_TYPES and not self.limits.allow_peer_message(
                peer.peer_id
            ):
                self.registry.counters["peer_messages_rate_limited"] += 1
                return self._error(envelope, "rate_limited", "muitas mensagens; aguarde")

        if envelope.type == MessageType.CLOSE:
            self._drop_pending(peer.peer_id)
            self.registry.remove(peer.peer_id)
            return None

        core = self.registry.core()
        if core is None or core.mailbox is None:
            self.registry.counters["relays_dropped"] += 1
            return self._error(envelope, "core_offline", "core indisponível no momento")

        if envelope.type == MessageType.AUTH:
            self.registry.counters["auth_requests"] += 1
            if envelope.request_id:
                self._pending_auths[envelope.request_id] = (peer.peer_id, time.monotonic())

        # O relé SEMPRE rotula o envelope com o device autenticado (nunca confia
        # no device_id alegado) e remove qualquer target escolhido pelo móvel.
        device_id = peer.device_id
        safe_payload = dict(envelope.payload)
        safe_payload.pop("target_device_id", None)
        if device_id:
            safe_payload["source_device_id"] = device_id
        routed = envelope.model_copy(update={"device_id": device_id, "payload": safe_payload})
        if core.mailbox.put(routed):
            self.registry.counters["relays"] += 1
        return None

    # -- helpers ---------------------------------------------------------------

    def _graceful_close(self, peer, *, reason: str) -> None:
        if peer.mailbox is None:
            return
        peer.mailbox.put(
            build_message(
                MessageType.CLOSE,
                device_id=peer.device_id,
                payload={"reason": reason},
            )
        )
        peer.mailbox.close()

    def _drop_pending(self, peer_id: str) -> None:
        for request_id, other in list(self._pending_auths.items()):
            if other[0] == peer_id:
                self._pending_auths.pop(request_id, None)

    def _sweep_pending_auths(self) -> None:
        """Elimina AUTH em vôo sem resposta do Core além do TTL (anti-vazamento).

        O AUTH é single-try: se o Core não responder a tempo, o móvel re-envia
        num request_id novo (o TTL é só defesa de memória no relé).
        """
        ttl = float(getattr(self.settings, "auth_timeout_seconds", 60.0) or 60.0)
        if not self._pending_auths:
            return
        now = time.monotonic()
        expired = [k for k, (_, ts) in self._pending_auths.items() if now - ts > ttl]
        for k in expired:
            self._pending_auths.pop(k, None)
        if expired:
            self.registry.counters["auth_timeouts"] = (
                self.registry.counters.get("auth_timeouts", 0) + len(expired)
            )

    def _error(
        self, envelope: RemoteEnvelope, code: str, message: str
    ) -> RemoteEnvelope:
        return build_message(
            MessageType.ERROR,
            device_id=envelope.device_id,
            command_id=envelope.command_id,
            request_id=envelope.request_id,
            payload={"error": code, "message": message},
        )