"""Computer Action Layer (Fase 18) — registro de tipos de ação.

Cada ActionType possui: validador de params, nível de permissão default,
risco default, e descrição para o LLM. O registry é a fonte única de
verdade sobre quais ações existem e como são classificadas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.enums import PermissionLevel, RiskLevel

from .models import ActionType

# ---------------------------------------------------------------------------
# Teclas válidas (whitelist — nunca aceitar strings arbitrárias de API baixo nível)
# ---------------------------------------------------------------------------

_VALID_KEYS: frozenset[str] = frozenset({
    "ENTER", "ESC", "TAB", "SPACE", "BACKSPACE", "DELETE", "DEL", "INSERT",
    "HOME", "END", "PAGE_UP", "PAGE_DOWN",
    "UP", "DOWN", "LEFT", "RIGHT",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
    "CTRL", "SHIFT", "ALT", "SUPER",
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
})

_VALID_MODIFIERS: frozenset[str] = frozenset({"CTRL", "SHIFT", "ALT", "SUPER"})
_VALID_MOUSE_BUTTONS: frozenset[str] = frozenset({"left", "right", "middle"})


def _validate_int(value: Any, name: str, min_val: int | None = None, max_val: int | None = None) -> int:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} deve ser inteiro, recebeu {type(value).__name__}")
    iv = int(value)
    if min_val is not None and iv < min_val:
        raise ValueError(f"{name}={iv} abaixo do mínimo {min_val}")
    if max_val is not None and iv > max_val:
        raise ValueError(f"{name}={iv} acima do máximo {max_val}")
    return iv


def _validate_key(key: str, *, allow_modifiers: bool = False) -> str:
    k = key.upper().strip()
    if k not in _VALID_KEYS:
        raise ValueError(f"Tecla inválida: {k!r}")
    if not allow_modifiers and k in _VALID_MODIFIERS:
        raise ValueError(f"Tecla {k} não pode ser usada sozinha (use hotkey para combos)")
    return k


# ---------------------------------------------------------------------------
# Per-type parameter validators
# ---------------------------------------------------------------------------

def _validate_mouse_move(params: dict[str, Any]) -> dict[str, Any]:
    return {"x": _validate_int(params.get("x"), "x", min_val=0), "y": _validate_int(params.get("y"), "y", min_val=0)}


def _validate_click(params: dict[str, Any]) -> dict[str, Any]:
    result = _validate_mouse_move(params)
    button = (params.get("button") or "left").lower().strip()
    if button not in _VALID_MOUSE_BUTTONS:
        raise ValueError(f"button inválido: {button!r} (use: {', '.join(sorted(_VALID_MOUSE_BUTTONS))})")
    result["button"] = button
    return result


def _validate_scroll(params: dict[str, Any]) -> dict[str, Any]:
    amount = _validate_int(params.get("amount"), "amount")
    if abs(amount) > 10:
        raise ValueError(f"amount={amount} fora dos limites (±10)")
    result: dict[str, Any] = {"amount": amount}
    if "x" in params:
        result["x"] = _validate_int(params["x"], "x", min_val=0)
    if "y" in params:
        result["y"] = _validate_int(params["y"], "y", min_val=0)
    return result


def _validate_key_press(params: dict[str, Any]) -> dict[str, Any]:
    key = params.get("key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("key é obrigatório")
    return {"key": _validate_key(key)}


def _validate_hotkey(params: dict[str, Any]) -> dict[str, Any]:
    keys = params.get("keys")
    if not isinstance(keys, list) or len(keys) == 0:
        raise ValueError("keys deve ser lista não vazia")
    if len(keys) > 3:
        raise ValueError(f"hotkey máximo 3 teclas, recebeu {len(keys)}")
    validated = []
    for k in keys:
        if not isinstance(k, str):
            raise ValueError(f"Tecla inválida: {k!r}")
        validated.append(_validate_key(k, allow_modifiers=True))
    return {"keys": validated}


def _validate_type_text(params: dict[str, Any]) -> dict[str, Any]:
    text = params.get("text")
    if not isinstance(text, str):
        raise ValueError("text deve ser string")
    if len(text) == 0:
        raise ValueError("text não pode ser vazio")
    return {"text": text}


def _validate_focus_window(params: dict[str, Any]) -> dict[str, Any]:
    title = params.get("title")
    process = params.get("process")
    if not title and not process:
        raise ValueError("Pelo menos um de title ou process é obrigatório")
    result: dict[str, Any] = {}
    if title:
        result["title"] = str(title)
    if process:
        result["process"] = str(process)
    return result


# ---------------------------------------------------------------------------
# Action type descriptor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionTypeDescriptor:
    """Descriptor completo de um tipo de ação registrado."""

    action_type: ActionType
    description: str
    permission_level: PermissionLevel
    risk: RiskLevel
    validate_params: Callable[[dict[str, Any]], dict[str, Any]]
    param_schema: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry singleton
# ---------------------------------------------------------------------------

_DEFAULT_DESCRIPTORS: dict[ActionType, ActionTypeDescriptor] = {
    ActionType.MOUSE_MOVE: ActionTypeDescriptor(
        action_type=ActionType.MOUSE_MOVE,
        description="Move o cursor do mouse para coordenadas absolutas no desktop.",
        permission_level=PermissionLevel.LEVEL_1,
        risk=RiskLevel.LOW,
        validate_params=_validate_mouse_move,
        param_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": 0, "description": "Coordenada X"},
                "y": {"type": "integer", "minimum": 0, "description": "Coordenada Y"},
            },
            "required": ["x", "y"],
        },
    ),
    ActionType.CLICK: ActionTypeDescriptor(
        action_type=ActionType.CLICK,
        description="Clica em coordenadas do desktop (botão esquerdo por padrão).",
        permission_level=PermissionLevel.LEVEL_1,
        risk=RiskLevel.LOW,
        validate_params=_validate_click,
        param_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
            },
            "required": ["x", "y"],
        },
    ),
    ActionType.DOUBLE_CLICK: ActionTypeDescriptor(
        action_type=ActionType.DOUBLE_CLICK,
        description="Clica duas vezes rapidamente em coordenadas do desktop.",
        permission_level=PermissionLevel.LEVEL_1,
        risk=RiskLevel.LOW,
        validate_params=_validate_click,
        param_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
            },
            "required": ["x", "y"],
        },
    ),
    ActionType.RIGHT_CLICK: ActionTypeDescriptor(
        action_type=ActionType.RIGHT_CLICK,
        description="Clica com o botão direito em coordenadas do desktop.",
        permission_level=PermissionLevel.LEVEL_1,
        risk=RiskLevel.LOW,
        validate_params=_validate_click,
        param_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
            },
            "required": ["x", "y"],
        },
    ),
    ActionType.SCROLL: ActionTypeDescriptor(
        action_type=ActionType.SCROLL,
        description="Rola a roda do mouse. amount > 0 = cima, < 0 = baixo. Máximo ±10.",
        permission_level=PermissionLevel.LEVEL_1,
        risk=RiskLevel.LOW,
        validate_params=_validate_scroll,
        param_schema={
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "minimum": -10, "maximum": 10},
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
            },
            "required": ["amount"],
        },
    ),
    ActionType.KEY_PRESS: ActionTypeDescriptor(
        action_type=ActionType.KEY_PRESS,
        description="Pressiona e solta uma tecla (whitelist: ENTER, ESC, TAB, setas, F-keys, etc).",
        permission_level=PermissionLevel.LEVEL_1,
        risk=RiskLevel.LOW,
        validate_params=_validate_key_press,
        param_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Nome da tecla (ex.: ENTER, ESC, TAB)"},
            },
            "required": ["key"],
        },
    ),
    ActionType.HOTKEY: ActionTypeDescriptor(
        action_type=ActionType.HOTKEY,
        description="Pressiona combinação de até 3 teclas (ex.: CTRL+C). Combinações bloqueadas: CTRL+ALT+DEL, CTRL+SHIFT+ESC.",
        permission_level=PermissionLevel.LEVEL_1,
        risk=RiskLevel.MEDIUM,
        validate_params=_validate_hotkey,
        param_schema={
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 3,
                    "description": "Teclas da combinação (ex.: ['CTRL', 'C'])",
                },
            },
            "required": ["keys"],
        },
    ),
    ActionType.TYPE_TEXT: ActionTypeDescriptor(
        action_type=ActionType.TYPE_TEXT,
        description="Digita texto no foco atual. NUNCA executa comandos shell. Tratado como dado puro.",
        permission_level=PermissionLevel.LEVEL_1,
        risk=RiskLevel.MEDIUM,
        validate_params=_validate_type_text,
        param_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Texto para digitar"},
                "delay_ms": {"type": "integer", "minimum": 0, "maximum": 500, "default": 0},
            },
            "required": ["text"],
        },
    ),
    ActionType.FOCUS_WINDOW: ActionTypeDescriptor(
        action_type=ActionType.FOCUS_WINDOW,
        description="Traz uma janela para frente por título ou processo.",
        permission_level=PermissionLevel.LEVEL_1,
        risk=RiskLevel.LOW,
        validate_params=_validate_focus_window,
        param_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título da janela"},
                "process": {"type": "string", "description": "Nome do processo"},
            },
        },
    ),
}


class ActionTypeRegistry:
    """Registro de tipos de ação com validação de params e classificação de risco.

    Singleton: `get_action_registry()`.
    """

    def __init__(self) -> None:
        self._descriptors: dict[ActionType, ActionTypeDescriptor] = dict(_DEFAULT_DESCRIPTORS)

    def get(self, action_type: ActionType) -> ActionTypeDescriptor | None:
        return self._descriptors.get(action_type)

    def is_known(self, action_type: ActionType) -> bool:
        return action_type in self._descriptors

    def all_types(self) -> list[ActionType]:
        return list(self._descriptors.keys())

    def validate_params(self, action_type: ActionType, params: dict[str, Any]) -> dict[str, Any]:
        """Valida e normaliza parâmetros para um tipo de ação.

        Retorna params normalizados ou levanta ValueError.
        """
        desc = self._descriptors.get(action_type)
        if desc is None:
            raise ValueError(f"Tipo de ação desconhecido: {action_type}")
        return desc.validate_params(params)


_registry: ActionTypeRegistry | None = None


def get_action_registry() -> ActionTypeRegistry:
    global _registry
    if _registry is None:
        _registry = ActionTypeRegistry()
    return _registry


def reset_action_registry() -> None:
    """Reset para testes (hermeticidade)."""
    global _registry
    _registry = None
