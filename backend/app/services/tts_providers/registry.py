"""Registro/factory de provedores de TTS.

Escolhe o provider ativo baseado na configuração centralizada (`TTS_PROVIDER`).
O resto do JARVIS não precisa conhecer provedores específicos.
"""

from __future__ import annotations

import logging

from app.core.config import settings

from .base import MissingProviderError, TTSProvider

logger = logging.getLogger("jarvis.tts.registry")

PROVIDERS: dict[str, type[TTSProvider]] = {}


def register_provider(name: str, cls: type[TTSProvider]) -> None:
    PROVIDERS[name] = cls


def _build_active_provider() -> TTSProvider:
    name = (settings.tts_provider or "edge").lower()
    if name == "edge":
        from .edge import EdgeTTSProvider

        return EdgeTTSProvider()
    raise MissingProviderError(f"Provedor de TTS desconhecido: {name}")


_active_provider: TTSProvider | None = None


def get_tts_provider() -> TTSProvider:
    """Devolve o provider ativo (singleton, recriado se a config mudar em testes)."""
    global _active_provider
    if _active_provider is None or _active_provider.name != settings.tts_provider:
        _active_provider = _build_active_provider()
    return _active_provider


def reset_provider_cache() -> None:
    """Zera o singleton do provider (útil para testes)."""
    global _active_provider
    _active_provider = None
