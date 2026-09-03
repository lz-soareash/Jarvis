"""Wake-on-LAN (Fase 12.6) — tool `wake_on_lan` para acordar uma máquina na LAN.

Envia o *magic packet* UDP padrão (6 bytes `0xFF` + 16× o MAC) para um endereço
de broadcast, fazendo a placa de rede da máquina-alvo ligar. A execução é um
simples `sendto` UDP (não bloqueante por prática), sem abrir portas de escuta —
consistente com o princípio "PC/cliente é sempre outbound".

Segurança:
- MAC validado (formatos `AA:BB:...`/`AA-BB-...`/contíguo); nada é executado.
- Apenas o magic packet é enviado; nenhum comando arbitrário é permitido.
- A tool é `LEVEL_2` (requer confirmação): afeta o estado de energia de outra
  máquina na rede.
"""

from __future__ import annotations

import re

from app.core.config import settings
from app.core.enums import PermissionLevel, RiskLevel
from app.tools.base import Tool, ToolContext, ToolResult

_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}([:-]?[0-9a-fA-F]{2}){5}$")


def parse_mac(mac: str) -> bytes:
    """Valida e normaliza um MAC para 6 bytes (ou lança ValueError)."""
    mac = (mac or "").strip()
    if not _MAC_RE.match(mac):
        raise ValueError("MAC inválido (use AA:BB:CC:DD:EE:FF)")
    clean = mac.replace(":", "").replace("-", "")
    return bytes(int(clean[i : i + 2], 16) for i in range(0, 12, 2))


def build_magic_packet(mac: str) -> bytes:
    """Constrói o magic packet WoL: 6×0xFF + 16×MAC (102 bytes)."""
    mac_bytes = parse_mac(mac)
    return b"\xff" * 6 + mac_bytes * 16


def _udp_sendto(packet: bytes, address: tuple[str, int], connect_timeout: float) -> int:
    """Envia o packet via UDP socket. Separada p/ teste (monkeypatch em `wol.socket`)."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(connect_timeout)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        return sock.sendto(packet, address)
    finally:
        sock.close()


class WakeOnLan(Tool):
    """Acorda uma máquina na LAN via magic packet WoL."""

    name = "wake_on_lan"
    description = (
        "Envia um magic packet Wake-on-LAN para acordar uma máquina na rede. "
        "Requer o MAC da máquina-alvo; broadcast e porta têm defaults. "
        "Exemplo: wake_on_lan(mac_address='AA:BB:CC:DD:EE:FF')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "mac_address": {
                "type": "string",
                "description": "Endereço MAC da máquina-alvo (AA:BB:CC:DD:EE:FF).",
            },
            "broadcast": {
                "type": "string",
                "description": "Endereço de broadcast (padrão: configurado).",
            },
            "port": {
                "type": "integer",
                "description": "Porta UDP (padrão: 9).",
            },
        },
        "required": ["mac_address"],
    }
    permission_level = PermissionLevel.LEVEL_2
    risk = RiskLevel.MEDIUM

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        if not settings.wol_enabled:
            return ToolResult.failure("Wake-on-LAN desabilitado (REMOTE_ENABLED/wol_enabled)")
        mac = arguments.get("mac_address")
        try:
            packet = build_magic_packet(mac)
        except (ValueError, TypeError) as exc:  # noqa: BLE001
            return ToolResult.failure(str(exc))

        broadcast = (arguments.get("broadcast") or settings.wol_default_broadcast).strip()
        try:
            port = int(arguments.get("port") or settings.wol_default_port)
        except (ValueError, TypeError):  # noqa: BLE001
            return ToolResult.failure("porta inválida")

        try:
            _udp_sendto(packet, (broadcast, port), connect_timeout=2.0)
        except OSError as exc:  # noqa: BLE001 — rede indisponível vira resultado
            return ToolResult.failure(f"falha ao enviar magic packet: {exc}")

        return ToolResult.success(
            f"magic packet enviado para {mac} (broadcast={broadcast}:{port})"
        )
