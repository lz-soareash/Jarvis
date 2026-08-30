"""Speech Manager (backend) — compõe Sanitizer -> Formatter -> Contexto.

Gera o `speech_text` final (SPEECH_TEXT) a partir do `display_text` (DISPLAY_TEXT)
sem nunca alterar o original. Disponibiliza utilitários de logging com os rótulos
pedidos (speech_requested, speech_sanitized, speech_formatted, ...).

Nota: a fila de reprodução e os estados de fala (IDLE/LISTENING/THINKING/SPEAKING)
são responsabilidade do Speech Manager no FRONTEND; aqui concentramos apenas o
preparo do texto (limpeza/forma) e a escolha do contexto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .context import SpeechContext, detect_context
from .formatter import formatter
from .sanitizer import sanitizer

logger = logging.getLogger("jarvis.speech")


@dataclass
class PreparedSpeech:
    """Resultado do preparo de fala, com separação clara DISPLAY x SPEECH."""

    display_text: str
    speech_text: str
    context: SpeechContext
    utterances: list[str]


def prepare_speech_text(display_text: str, split: bool = True) -> PreparedSpeech:
    """Gera `speech_text` (limpo e formatado) a partir do `display_text`.

    - display_text: NUNCA é alterado.
    - speech_text: sanitizado + formatado, pronto para o TTS.
    - utterances: divisão em blocos naturais (para fala em sequência).
    """
    logger.debug("speech_requested")

    cleaned = sanitizer.sanitize(display_text)
    logger.debug("speech_sanitized")

    formatted = formatter.format(cleaned)
    logger.debug("speech_formatted")

    context = detect_context(display_text)

    utterances = formatter.split_into_utterances(formatted) if split else ([formatted] if formatted else [])

    return PreparedSpeech(
        display_text=display_text,
        speech_text=formatted,
        context=context,
        utterances=utterances,
    )
