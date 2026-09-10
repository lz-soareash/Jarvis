"""Observabilidade sanitizada do Gateway WAN (Fase 23).

Sem secrets/payloads: apenas contagens, papel do peer e destino (device_id).
`GET /health` do Gateway usa este snapshot + estado do relé.
"""

from __future__ import annotations

from typing import Any


def gateway_snapshot(hub) -> dict[str, Any]:
    """Estado do relé: registro + limites locais (nunca tokens/secrets)."""
    reg = hub.registry.snapshot()
    return {
        **reg,
        "connect_rejected": hub.limits.rejected_connects,
        "oversized_envelopes": hub.limits.oversized_envelopes,
        "rejected_total": hub.limits.rejected_connects + hub.limits.oversized_envelopes,
    }