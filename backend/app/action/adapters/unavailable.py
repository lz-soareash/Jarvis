"""Computer Action Layer (Fase 18) — adaptador indisponível.

Retorna UNAVAILABLE para todas as ações. Usado quando a plataforma não
é suportada ou quando o adaptador real falhou ao inicializar.
"""

from __future__ import annotations

from ..errors import ActionUnavailableError
from ..models import ActionCapabilities, ScreenInfo
from .base import ComputerAdapter


class UnavailableComputerAdapter(ComputerAdapter):
    """Adaptador que reporta indisponibilidade para qualquer ação."""

    @property
    def name(self) -> str:
        return "unavailable"

    @property
    def available(self) -> bool:
        return False

    def discover_capabilities(self) -> ActionCapabilities:
        return ActionCapabilities(
            mouse=False, keyboard=False, scroll=False,
            window_focus=False, type_text=False, hotkey=False,
            detail="Adaptador indisponível — plataforma não suportada",
        )

    def get_screen_info(self) -> ScreenInfo:
        return ScreenInfo(width=0, height=0)

    def _fail(self) -> None:
        raise ActionUnavailableError(
            "Adaptador indisponível — ação não executável",
            detail="Plataforma não suportada ou adaptador desabilitado",
        )

    def mouse_move(self, x: int, y: int) -> None:
        self._fail()

    def mouse_click(self, x: int, y: int, button: str = "left") -> None:
        self._fail()

    def mouse_double_click(self, x: int, y: int) -> None:
        self._fail()

    def mouse_scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        self._fail()

    def key_press(self, key: str) -> None:
        self._fail()

    def hotkey(self, keys: list[str]) -> None:
        self._fail()

    def type_text(self, text: str, delay_ms: int = 0) -> None:
        self._fail()

    def focus_window(self, title: str | None = None, process: str | None = None) -> bool:
        self._fail()
