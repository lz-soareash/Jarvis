"""Abstração de provedores de TTS — permite trocar o provider sem reescrever o
Speech Manager / serviços. Cada provider implementa a síntese de áudio de forma
isolada; o restante do JARVIS conversa apenas com a interface `TTSProvider`.

Implementações disponíveis:
- `EdgeTTSProvider`: Microsoft Edge neural voices (padrão atual, sem chave).
- `RegistryTTSProvider` / `resolve_provider`: escolhe o provider ativo via config.

Providers futuros (OpenAI, ElevenLabs) entram aqui sem alterar o resto.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("jarvis.tts.providers")


class TTSProvider(ABC):
    """Contrato comum de síntese de fala. Novos providers implementam o `synthesize`."""

    name: str = "abstract"

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Gera e devolve os bytes do áudio (formatos suportados pelo provider)."""

    def available(self) -> bool:
        """Indica se o provider está pronto para uso."""
        return True


class MissingProviderError(RuntimeError):
    """Provedor não encontrado na configuração."""
