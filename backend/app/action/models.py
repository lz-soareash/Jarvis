"""Computer Action Layer (Fase 18) — modelos de ação e resultado.

`ActionRequest` é a representação estruturada de uma intenção de ação no
computador. `ActionResult` é o veredito determinístico da execução. Ambos
são serializáveis, auditáveis e NUNCA contêm secrets, binários ou payloads
sensíveis.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


class ActionType(str, enum.Enum):
    """Tipos de ação suportados pela Computer Action Layer (Fase 18)."""

    MOUSE_MOVE = "mouse_move"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    SCROLL = "scroll"
    KEY_PRESS = "key_press"
    HOTKEY = "hotkey"
    TYPE_TEXT = "type_text"
    FOCUS_WINDOW = "focus_window"


ACTION_TYPE_NAMES: frozenset[str] = frozenset(t.value for t in ActionType)


class ActionStatus(str, enum.Enum):
    """Estados do ciclo de vida de uma ação."""

    ACCEPTED = "accepted"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    DRY_RUN = "dry_run"


@dataclass(frozen=True)
class ActionRequest:
    """Pedido de ação estruturado — único formato aceito pelo executor.

    Validates on construction: action_type must be known.
    """

    action_type: ActionType
    params: dict[str, Any] = field(default_factory=dict)
    action_id: str = field(default_factory=_uuid)
    requested_by: str = "agent"
    session_id: str | None = None
    device_id: str | None = None
    dry_run: bool = False
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        if isinstance(self.action_type, str):
            try:
                object.__setattr__(self, "action_type", ActionType(self.action_type))
            except ValueError:
                raise ValueError(f"action_type desconhecido: {self.action_type!r}")


@dataclass
class ActionResult:
    """Resultado determinístico da execução de uma ação.

    Serializável, auditável. `error_message` sanitizado — nunca stack traces.
    """

    action_id: str
    action_type: ActionType
    status: ActionStatus
    started_at: str = field(default_factory=_utcnow_iso)
    finished_at: str | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    dry_run: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    adapter_detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (ActionStatus.EXECUTED, ActionStatus.ACCEPTED, ActionStatus.DRY_RUN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "dry_run": self.dry_run,
            "metadata": self.metadata,
            "adapter_detail": self.adapter_detail,
        }


@dataclass(frozen=True)
class ActionCapabilities:
    """Capability discovery para ações de computador (mirrors Perception pattern).

    Reflete capacidades REAIS do adapter — nunca inventado.
    """

    mouse: bool = False
    keyboard: bool = False
    scroll: bool = False
    window_focus: bool = False
    type_text: bool = False
    hotkey: bool = False
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mouse": self.mouse,
            "keyboard": self.keyboard,
            "scroll": self.scroll,
            "window_focus": self.window_focus,
            "type_text": self.type_text,
            "hotkey": self.hotkey,
            "detail": self.detail,
        }

    @property
    def summary(self) -> str:
        flags = [k for k, v in self.to_dict().items() if v is True and k != "detail"]
        return ", ".join(flags) if flags else "nenhuma"


@dataclass(frozen=True)
class ScreenInfo:
    """Informações da tela para validação de coordenadas."""

    width: int = 1920
    height: int = 1080
