"""Configuração do Gateway WAN (Fase 23) — deployável de forma separada.

O Gateway é um processo FastAPI independente (NUNCA o App do Core). Ele mantém,
em memória, apenas o roteamento de conexões WebSocket entre o Core (PC) e os
clientes finos (móveis); a AUTORIDADE de confiança continua sendo o Core (trust
relay). Por isso este pacote não toca no banco nem no Settings do Core: apenas
lê variáveis `GATEWAY_*` do ambiente / `.env` da raiz do repositório.

Segredos: `GATEWAY_PEER_TOKEN` é o único segredo que o próprio Gateway conhece
(só o Core legítimo o apresenta no handshake). NUNCA é entregue a mobiles; o
token dos móveis trafega apenas como relay para o Core validar.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class GatewaySettings(BaseSettings):
    """Configuração do processo Gateway (prefixo de ambiente `GATEWAY_`)."""

    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    name: str = "vega-gateway"
    host: str = "0.0.0.0"
    port: int = 8200

    # Segredo compartilhado com o Core (link outbound). Só quem o apresenta no
    # handshake `hello` é promovido a papel `core` no relé.
    peer_token: str = ""

    ws_path: str = "/api/remote/ws"

    # Defesas de abuso (relé só transporta — limites locais mínimo vitais).
    max_payload_bytes: int = 256 * 1024  # 256 KiB — teto de um envelope
    max_peers_per_ip: int = 8  # conexões simultâneas por IP
    connect_rate_capacity: float = 10.0  # token bucket de conexões por IP
    connect_rate_refill_per_sec: float = 0.5

    # Backpressure de saída (mailbox por conexão) — cliente lento não bloqueia
    # o relé nem o Core; acima do teto, envelopes são descartados e contados.
    mailbox_max: int = 128

    # Heartbeat inativo: acima de `idle_timeout_seconds` sem mensagem, o relé
    # encerra a conexão (custo mínimo por peer ocioso).
    idle_timeout_seconds: float = 90.0
    heartbeat_interval_seconds: float = 30.0

    log_level: str = "INFO"


_settings: GatewaySettings | None = None


def get_settings() -> GatewaySettings:
    global _settings
    if _settings is None:
        _settings = GatewaySettings()
    return _settings


def reset_settings() -> None:
    """Zera o singleton (uso em testes)."""
    global _settings
    _settings = None