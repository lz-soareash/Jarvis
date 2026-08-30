"""Síntese de voz (Fase 6/6b) — fachada estável para o restante do Core.

Mantém o contrato público original (`synthesize(text, voice)`, `ALLOWED_VOICES`,
`DEFAULT_VOICE`, `MAX_TEXT_CHARS`) para não quebrar a API `/api/tts`, mas agora:

- roteia o áudio através da abstração de provedores (`tts_providers`);
- aplica a configuração centralizada (voz Antonio, rate, pitch, volume);
- permite recuperar o texto falado (SPEECH_TEXT) via `prepare_speech_text`;
- mantém um cache de áudio em memória p/ frases repetidas (ex.: "Pronto.").
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Callable

from app.core.config import settings

from . import tts_providers
from .tts_providers import EdgeTTSProvider, TTSProvider

logger = logging.getLogger("jarvis.services.tts")

ALLOWED_VOICES = {
    "pt-BR-FranciscaNeural": "Feminina (natural)",
    "pt-BR-AntonioNeural": "Masculina (natural)",
    "pt-PT-RaquelNeural": "Português de Portugal (feminina)",
    "en-US-AriaNeural": "Inglês (feminina)",
}

DEFAULT_VOICE = "pt-BR-AntonioNeural"
MAX_TEXT_CHARS = 1500

# Mantém compatibilidade: resolução da voz por voz/config real.
def _resolve_voice(voice: str | None) -> str:
    candidate = voice or settings.tts_voice
    if candidate in ALLOWED_VOICES:
        return candidate
    # fallback para a voz masculina padrão do perfil Antonio, se conhecida
    if "Antonio" in ALLOWED_VOICES:
        return "pt-BR-AntonioNeural"
    return DEFAULT_VOICE


class _AudioCache:
    """Cache LRU simples de áudio sintetizado (limite, limpeza, thread-safe-ish).

    Chave = (texto da fala, voz). Evita regenerar áudio de frases repetidas.
    """

    def __init__(self, maxsize: int = 128) -> None:
        self.maxsize = max(0, int(maxsize))
        self._store: OrderedDict = OrderedDict()

    def clear(self) -> None:
        self._store.clear()

    def get(self, key: tuple[str, str]) -> bytes | None:
        if self.maxsize <= 0:
            return None
        value = self._store.pop(key, None)
        if value is None:
            return None
        self._store[key] = value  # re-insere no fim (LRU)
        return value

    def put(self, key: tuple[str, str], value: bytes) -> None:
        if self.maxsize <= 0:
            return
        self._store[key] = value
        self._store.move_to_end(key)
        while len(self._store) > self.maxsize:
            self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)


_audio_cache = _AudioCache(maxsize=settings.tts_cache_size)


def clear_audio_cache() -> None:
    """Limpa o cache de áudio em memória."""
    _audio_cache.clear()
    logger.info("cache_cleared::tts_audio")


def cache_info() -> dict:
    return {"size": len(_audio_cache), "maxsize": _audio_cache.maxsize}


def configure_provider(
    voice: str | None = None,
    rate: str | None = None,
    pitch: str | None = None,
    volume: str | None = None,
) -> TTSProvider:
    """Constrói um provider Edge com parâmetros override (útil p/ trocar voz/velocidade)."""
    return EdgeTTSProvider(voice=voice, rate=rate, pitch=pitch, volume=volume)


async def _synthesize_with_provider(provider: TTSProvider, speech_text: str) -> bytes:
    """Sintetiza com retry simples (fallback) sem travar o sistema."""
    attempts = max(1, int(settings.tts_fallback_attempts) + 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await provider.synthesize(speech_text)
        except Exception as exc:  # noqa: BLE001 — serviço externo pode falhar
            last_error = exc
            logger.warning("tts_failed::provider attempt=%d error=%s", attempt + 1, type(exc).__name__)
            if attempt + 1 < attempts:
                time.sleep(0.4)
    provider_name = type(provider).__name__
    raise RuntimeError(f"Voz indisponível ({provider_name}): {type(last_error).__name__}") from last_error


async def synthesize(text: str, voice: str | None = None) -> bytes:
    """Sintetiza `text` (já pronto p/ fala) e devolve bytes de áudio MP3.

    Mantém a assinatura/contrato original. O texto entra como DISPLAY ou SPEECH;
    aqui apenas geramos o áudio (com cache de frases repetidas).
    """
    if not text or not text.strip():
        raise ValueError("Texto vazio")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"Texto muito longo (máximo {MAX_TEXT_CHARS} caracteres)")

    actual = _resolve_voice(voice)
    key = (text, actual)

    cached = _audio_cache.get(key)
    if cached is not None:
        logger.debug("cache_hit::tts_audio")
        return cached
    logger.debug("cache_miss::tts_audio")

    provider = tts_providers.get_tts_provider()
    # Se o provider ativo não é o Edge (ex.: config de outro), usa-o; senão Edge direto.
    if isinstance(provider, EdgeTTSProvider) or provider.name == "edge":
        # Reaplica a voz solicitada se diferente da configurada.
        if actual != settings.tts_voice:
            provider = configure_provider(voice=actual)
    else:
        if actual != settings.tts_voice:
            logger.debug("provider_fallback::edge voice=%s", actual)
            provider = configure_provider(voice=actual)

    audio = await _synthesize_with_provider(provider, text)
    _audio_cache.put(key, audio)
    return audio


def prepare_speech_text(display_text: str, split: bool = True):
    """Delega ao pipeline de voz (sanitização + formatação + contexto).

    Expõe uma importação conveniente: `from app.services.tts import prepare_speech_text`.
    """
    from app.speech.manager import prepare_speech_text as _prepare

    return _prepare(display_text, split=split)
