"""Fase 6b — Voz & Fala: pipeline de texto para fala (sanitização + formatação).

Separa o texto DISPLAY (interface) do texto SPEECH (TTS), de forma isolada e
testável, sem alterar os contratos existentes.
"""

from .context import SpeechContext, detect_context
from .formatter import SpeechFormatter, formatter
from .manager import PreparedSpeech, prepare_speech_text
from .sanitizer import SpeechSanitizer, sanitizer

__all__ = [
    "SpeechSanitizer",
    "sanitizer",
    "SpeechFormatter",
    "formatter",
    "SpeechContext",
    "detect_context",
    "PreparedSpeech",
    "prepare_speech_text",
]
