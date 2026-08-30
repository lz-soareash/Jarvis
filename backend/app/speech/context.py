"""Context Detection — reconhece o contexto de uma resposta para ajustar o tom.

Categorias: NORMAL, CASUAL, INFORMATION, CONFIRMATION, ALERT, ERROR, SYSTEM,
URGENT. A detecção é heurística (sinais no texto) e retorna também um rótulo
legível usado para logging/documentação — não altera o texto falado em si,
apenas permite que o Speech Manager ajuste o enquadramento da fala.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("jarvis.speech.context")

CATEGORIES = ("NORMAL", "CASUAL", "INFORMATION", "CONFIRMATION", "ALERT", "ERROR", "SYSTEM", "URGENT")
DEFAULT_CATEGORY = "NORMAL"

# Sinais positivos/confirmação
_CONFIRM = re.compile(
    r"\b(pronto|ok|certo|claro|feito|conclu[íi]do|realizad[oa]|gravad[oa]|criad[oa]|"
    r"pod[eo] fazer|claro|sem problema|vou fazer|entendido|pode deixar)\b",
    re.IGNORECASE,
)
# Sinais de erro
_ERROR = re.compile(
    r"\b(erro|falha|falhou|n[ãa]o foi poss[ií]vel|negad[oa]|n[ãa]o consegui|"
    r"problema|indispon[ií]vel|inv[aá]lido|rejeitad[oa])\b",
    re.IGNORECASE,
)
# Sinais de alerta
_ALERT = re.compile(
    r"\b(aten[çc][ãa]o|alerta|cuidado|aten[cç]ão|aviso|detectei|suspeit[oa]|"
    r"anormal|cr[ií]tic[oa])\b",
    re.IGNORECASE,
)
# Sinais de urgência
_URGENT = re.compile(r"\b(urgente|imediato|j[aá]|agora|rapidamente|cr[ií]tic[oa])\b", re.IGNORECASE)
# Sinais casuais
_CASUAL = re.compile(r"\b(claro|tranquilo|sem problema|beleza|pode crer|ok!)\b", re.IGNORECASE)


class SpeechContext:
    """Contexto detectado de uma resposta."""

    __slots__ = ("category", "key")

    def __init__(self, category: str = DEFAULT_CATEGORY) -> None:
        self.category = category
        self.key = f"ctx:{category.lower()}"

    def __repr__(self) -> str:  # pragma: no cover — conveniência
        return f"<SpeechContext {self.category}>"


def detect_context(text: str) -> SpeechContext:
    """Detecta o contexto de fala de um texto (heurístico e determinístico)."""
    if not text:
        return SpeechContext()
    # ALERTA explícito (atenção/cuidado/detectei um problema) tem precedência
    # sobre o "problema" genérico, que também aparece em frases de ERRO.
    explicit_alert = _ALERT.search(text) or _URGENT.search(text)
    if explicit_alert:
        if re.search(r"\b(problema|anormal|cr[ií]tic[oa])\b", text, re.IGNORECASE):
            return SpeechContext("URGENT" if _URGENT.search(text) else "ALERT")
        return SpeechContext("URGENT" if _URGENT.search(text) else "ALERT")
    # ERRO (falha/não consegui/negado) sem marcador de alerta explícito
    if _ERROR.search(text):
        return SpeechContext("ERROR")
    if _CONFIRM.search(text) and len(text) < 90:
        return SpeechContext("CONFIRMATION")
    if _CASUAL.search(text) and len(text) < 60:
        return SpeechContext("CASUAL")
    if _INFORMATION_HINT.search(text):
        return SpeechContext("INFORMATION")
    return SpeechContext()


_INFORMATION_HINT = re.compile(
    r"\b(dados|relat[oó]rio|status|informa[çc][ãa]o|sistema|configura[çc][ãa]o|"
    r"lista|processos|arquivo|mem[oó]ria|atualizado)\b",
    re.IGNORECASE,
)
