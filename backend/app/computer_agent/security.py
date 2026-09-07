"""Computer Security (Fase 19) — separação USER INTENT / SYSTEM POLICY /
TOOL POLICY / OBSERVATION DATA.

Regra de ouro: conteúdo observado (páginas, janelas, texto na tela) é DADO
não confiável. Nunca vira instrução autorizada — mesmo que o texto diga
"ignore as instruções anteriores" ou "execute shutdown". A observação alimenta
o contexto de PERCEBER; o guard impede que ela seja EXECUTADA como intenção.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Padrões típicos de prompt injection / instruções hostis observadas.
_INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(?:all\s+)?pre(?:vious|ceding)\s+(?:instructions|prompt)", re.IGNORECASE),
    re.compile(r"\bignore\s+todas\s+as\s+instru[çc][õo]es", re.IGNORECASE),
    re.compile(r"\bdesconsidere\s+(?:as\s+)?instru[çc][õo]es", re.IGNORECASE),
    re.compile(r"\bdelete\s+all\s+files\b", re.IGNORECASE),
    re.compile(r"\bapague\s+tudo\b|\bdelete\s+tudo\b", re.IGNORECASE),
    re.compile(r"\b(?:execute|run)\s+(?:shutdown|format|del\s+|rm\s+-rf)", re.IGNORECASE),
    re.compile(r"\bexecutar\s+(?:shutdown|format|desligar)\b", re.IGNORECASE),
    re.compile(r"\b(?:rm\s+-rf|shutdown\s+-[a-z]|format\s+c:)", re.IGNORECASE),
]

_BLOCKED_ACTION_TEXTS = {"shutdown", "delete", "rm", "kill", "format", "cmd", "powershell"}


@dataclass
class SecurityVerdict:
    ok: bool
    reason: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "detail": self.detail[:400]}


def looks_like_instruction(text: str) -> bool:
    """Detecta padrões de instrução injetada em texto observado."""
    if not text:
        return False
    return any(rx.search(text) for rx in _INJECTION_PATTERNS)


def _verbatim_from_observation(text: str, observation: dict | None) -> bool:
    """Verifica se `text` aparece literalmente em conteúdo observado (tela/janela)."""
    if not text or not observation:
        return False
    hay = " ".join(
        str(x or "") for x in [
            (observation.get("active_window") or {}).get("title"),
            (observation.get("active_window") or {}).get("window_text"),
        ]
    )
    if not hay:
        return False
    return any(len(t) >= 6 and t in hay for t in (text[i : i + 6] for i in range(len(text) - 5)))


def guard_intent(
    intended,
    *,
    observation: dict | None,
    goal: str,
) -> SecurityVerdict:
    """Barra intenções derivadas de dados observados que soem como instrução.

    - `source != 'goal'` (veio do LLM/observation) + texto perigoso → BLOCK.
    - Qualquer intenção cujo conteúdo imite instrução injetada (mesmo do goal)
      é rejeitada (goal legítimo não pede `shutdown` durante Computer Use).
    - Ações bloqueadas pela safety policy (ex.: hotkey CTRL+ALT+DEL) são
      barradas antes (ActionSafetyPolicy); aqui tratamos INJEÇÃO.
    """
    from .models import IntendedAction

    if not isinstance(intended, IntendedAction):
        return SecurityVerdict(False, "invalid_intent", "intenção mal formada")

    action = (intended.action or "").lower()
    if action in {"shutdown", "delete", "kill", "format"} or action in _BLOCKED_ACTION_TEXTS:
        return SecurityVerdict(False, "blocked_action", f"ação proibida: {action}")

    text_bits = [
        str(intended.params.get("text") or ""),
        str(intended.params.get("key") or ""),
        " ".join(str(k) for k in (intended.params.get("keys") or [])),
    ]
    joined = " ".join(b for b in text_bits if b)

    if intended.source != "goal":
        if looks_like_instruction(joined):
            return SecurityVerdict(False, "prompt_injection",
                                   "intenção replicou instrução observada")
        if _verbatim_from_observation(joined, observation):
            # Conteúdo da tela usado como parâmetro de ação (ex.: type_text de
            # texto de página) só é aceito se NÃO for instrução.
            if looks_like_instruction(joined):
                return SecurityVerdict(False, "prompt_injection",
                                       "texto observado com padrão de instrução")
            return SecurityVerdict(True, "observed_data", "conteúdo observado (dado)")
    return SecurityVerdict(True, "allowed")