"""Provedores de TTS (Fase 6b) — abstração para síntese de fala substituível."""

from .base import MissingProviderError, TTSProvider
from .edge import EdgeTTSProvider
from .registry import get_tts_provider, register_provider, reset_provider_cache

register_provider(EdgeTTSProvider.name, EdgeTTSProvider)

__all__ = [
    "TTSProvider",
    "EdgeTTSProvider",
    "MissingProviderError",
    "get_tts_provider",
    "register_provider",
    "reset_provider_cache",
]
