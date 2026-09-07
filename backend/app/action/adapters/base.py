"""Computer Action Layer (Fase 18) — adaptador abstrato de OS.

ABC que define o contrato entre ActionExecutor e o sistema operacional.
Cada implementação concreta deve: reportar capabilities REAIS, e falhar
com claridade quando algo não é suportado.
"""

from __future__ import annotations

import abc
from typing import Any

from ..models import ActionCapabilities, ScreenInfo


class ComputerAdapter(abc.ABC):
    """Interface abstrata para adaptação de ações ao sistema operacional.

    Implementações concretas: WindowsAdapter, UnavailableComputerAdapter.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Nome do adaptador (ex.: 'windows', 'linux', 'unavailable')."""

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        """True se o adaptador pode executar ações reais."""

    @abc.abstractmethod
    def discover_capabilities(self) -> ActionCapabilities:
        """Descobre capacidades REAIS do adaptador. Sem inventar."""

    @abc.abstractmethod
    def get_screen_info(self) -> ScreenInfo:
        """Retorna dimensões da tela para validação de coordenadas."""

    @abc.abstractmethod
    def mouse_move(self, x: int, y: int) -> None:
        """Move o cursor para (x, y)."""

    @abc.abstractmethod
    def mouse_click(self, x: int, y: int, button: str = "left") -> None:
        """Clica em (x, button). button: left/right/middle."""

    @abc.abstractmethod
    def mouse_double_click(self, x: int, y: int) -> None:
        """Clica duas vezes em (x, y)."""

    @abc.abstractmethod
    def mouse_scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        """Rola a roda do mouse. amount > 0 = cima."""

    @abc.abstractmethod
    def key_press(self, key: str) -> None:
        """Pressiona e solta uma tecla."""

    @abc.abstractmethod
    def hotkey(self, keys: list[str]) -> None:
        """Pressiona combinação de teclas."""

    @abc.abstractmethod
    def type_text(self, text: str, delay_ms: int = 0) -> None:
        """Digita texto no foco atual. NUNCA executa shell."""

    @abc.abstractmethod
    def focus_window(self, title: str | None = None, process: str | None = None) -> bool:
        """Traz janela para frente. Retorna True se encontrou."""


_adapter: ComputerAdapter | None = None


def get_adapter() -> ComputerAdapter:
    """Retorna o adaptador apropriado para a plataforma atual."""
    global _adapter
    if _adapter is not None:
        return _adapter

    import sys
    if sys.platform == "win32":
        try:
            from .windows import WindowsAdapter
            _adapter = WindowsAdapter()
            return _adapter
        except Exception:
            pass

    from .unavailable import UnavailableComputerAdapter
    _adapter = UnavailableComputerAdapter()
    return _adapter


def set_adapter(adapter: ComputerAdapter) -> None:
    """Override do adaptador (para testes)."""
    global _adapter
    _adapter = adapter


def reset_adapter() -> None:
    """Reset para testes."""
    global _adapter
    _adapter = None
