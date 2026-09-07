"""Computer Action Layer (Fase 18) — política de segurança.

Avalia ação validada contra regras de segurança. Determina:
- blocked → ação rejeitada imediatamente
- confirmation → requer confirmação (ACTION_REJECTED sem confirmação)
- allowed → prossegue

NÃO é o Permission Engine — é uma camada COMPLEMENTAR. A decisão
final é: (safety_policy + permission_engine). Ambos devem autorizar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import ActionRejectedError
from .models import ActionRequest, ActionType


# ---------------------------------------------------------------------------
# Combinações bloqueadas por padrão (extensível via config)
# ---------------------------------------------------------------------------

def _parse_hotkey(h: str) -> frozenset[str]:
    return frozenset(k.strip().upper() for k in h.split("+") if k.strip())


_BLOCKED_HOTKEYS_DEFAULT: frozenset[frozenset[str]] = frozenset({
    frozenset({"CTRL", "ALT", "DEL"}),
    frozenset({"CTRL", "SHIFT", "ESC"}),
    frozenset({"ALT", "F4"}),
    frozenset({"CTRL", "ALT", "F2"}),
})

_CONFIRMATION_REQUIRED_HOTKEYS_DEFAULT: frozenset[frozenset[str]] = frozenset({
    frozenset({"ALT", "TAB"}),
})


def _load_blocked_hotkeys() -> tuple[frozenset[frozenset[str]], frozenset[frozenset[str]]]:
    """Carrega hotkeys bloqueadas e que requerem confirmação de settings."""
    try:
        from app.core.config import settings
        extra_blocked = {_parse_hotkey(h) for h in (settings.computer_action_blocked_hotkeys or [])}
        blocked = _BLOCKED_HOTKEYS_DEFAULT | extra_blocked
    except Exception:
        blocked = _BLOCKED_HOTKEYS_DEFAULT
    return blocked, _CONFIRMATION_REQUIRED_HOTKEYS_DEFAULT


# ---------------------------------------------------------------------------
# type_text safety — detecção de padrões perigosos
# ---------------------------------------------------------------------------

_DANGEROUS_TEXT_PATTERNS: frozenset[str] = frozenset({
    "cmd.exe", "powershell", "bash", "/bin/sh", "rm -rf", "del /f",
    "format c:", "shutdown", "reboot", "sudo", "chmod 777",
    "eval(", "exec(", "os.system", "subprocess",
})


def _check_text_safety(text: str) -> None:
    """Verifica se o texto contém padrões que parecem comandos de shell."""
    text_lower = text.lower().strip()
    for pattern in _DANGEROUS_TEXT_PATTERNS:
        if pattern in text_lower:
            raise ActionRejectedError(
                f"text contém padrão potencialmente perigoso: {pattern!r}",
                detail="text é tratado como dado puro, não como comando",
            )


# ---------------------------------------------------------------------------
# Safety verdict
# ---------------------------------------------------------------------------

class SafetyVerdict:
    ALLOW = "allow"
    BLOCK = "block"
    CONFIRM = "confirm"


@dataclass(frozen=True)
class SafetyResult:
    """Resultado da avaliação de segurança."""

    verdict: str
    reason: str | None = None
    blocked_hotkey: frozenset[str] | None = None
    confirmation_required: bool = False


# ---------------------------------------------------------------------------
# SafetyPolicy
# ---------------------------------------------------------------------------

class ActionSafetyPolicy:
    """Política de segurança para ações de computador.

    Determinista, sem side effects, sem IO. Chamado ANTES do permission engine.
    """

    def __init__(self) -> None:
        self._blocked, self._confirmation = _load_blocked_hotkeys()

    def evaluate(self, request: ActionRequest) -> SafetyResult:
        """Avalia a action request contra a política de segurança."""
        if request.action_type == ActionType.HOTKEY:
            return self._evaluate_hotkey(request)
        if request.action_type == ActionType.TYPE_TEXT:
            return self._evaluate_text(request)
        if request.action_type == ActionType.FOCUS_WINDOW:
            return SafetyResult(verdict=SafetyVerdict.ALLOW)
        return SafetyResult(verdict=SafetyVerdict.ALLOW)

    def _evaluate_hotkey(self, request: ActionRequest) -> SafetyResult:
        keys_raw = request.params.get("keys", [])
        keys = frozenset(k.upper() for k in keys_raw)

        if keys in self._blocked:
            return SafetyResult(
                verdict=SafetyVerdict.BLOCK,
                reason=f"Combinação bloqueada: {'+'.join(sorted(keys))}",
                blocked_hotkey=keys,
            )

        if keys in self._confirmation:
            return SafetyResult(
                verdict=SafetyVerdict.CONFIRM,
                reason=f"Combinação requer confirmação: {'+'.join(sorted(keys))}",
                confirmation_required=True,
            )

        return SafetyResult(verdict=SafetyVerdict.ALLOW)

    def _evaluate_text(self, request: ActionRequest) -> SafetyResult:
        text = request.params.get("text", "")
        try:
            _check_text_safety(text)
        except ActionRejectedError as e:
            return SafetyResult(verdict=SafetyVerdict.BLOCK, reason=str(e))
        return SafetyResult(verdict=SafetyVerdict.ALLOW)

    def check(self, request: ActionRequest) -> None:
        """Verifica e levanta ActionRejectedError se bloqueado."""
        result = self.evaluate(request)
        if result.verdict == SafetyVerdict.BLOCK:
            raise ActionRejectedError(result.reason or "Ação bloqueada pela política de segurança")
        if result.verdict == SafetyVerdict.CONFIRM and not request.metadata.get("confirmed"):
            raise ActionRejectedError(
                result.reason or "Ação requer confirmação",
                detail="Envie metadata.confirmed=true após confirmação explícita",
            )
