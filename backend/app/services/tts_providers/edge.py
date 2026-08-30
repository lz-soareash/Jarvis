"""Provedor Edge TTS — vozes neurais da Microsoft (sem chave de API).

Voz padrão: pt-BR-AntonioNeural (perfil masculino, médio-grave, natural),
equivalente ao perfil desejado "Antonio". Aplicamos `rate`, `pitch` e `volume`
a partir da configuração centralizada.
"""

from __future__ import annotations

import io
import logging

from app.core.config import settings

from .base import TTSProvider

logger = logging.getLogger("jarvis.tts.edge")


class EdgeTTSProvider(TTSProvider):
    """Síntese neural via edge-tts com rate/pitch/volume configuráveis."""

    name = "edge"

    def __init__(
        self,
        voice: str | None = None,
        rate: str | None = None,
        pitch: str | None = None,
        volume: str | None = None,
    ) -> None:
        self.voice = voice or settings.tts_voice
        self.rate = rate if rate is not None else settings.tts_rate
        self.pitch = pitch if pitch is not None else settings.tts_pitch
        self.volume = volume if volume is not None else settings.tts_volume

    async def synthesize(self, text: str) -> bytes:
        from edge_tts import Communicate

        buffer = io.BytesIO()
        communicate = Communicate(
            text,
            self.voice,
            rate=self.rate,
            volume=self.volume,
            pitch=self.pitch,
        )
        logger.debug("tts_started::edge voice=%s rate=%s", self.voice, self.rate)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        data = buffer.getvalue()
        if not data:
            raise RuntimeError("Sem áudio gerado pelo serviço de voz")
        logger.debug("tts_completed::edge bytes=%d", len(data))
        return data
