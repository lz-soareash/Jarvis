"""Computer Action Layer (Fase 18) — adaptador Windows (ctypes).

Usa APIs nativas do Windows via ctypes (stdlib). Sem dependências externas.
Se ctypes ou user32 não estiverem disponíveis, levanta RuntimeError na
inicialização — o `get_adapter()` fallback para UnavailableComputerAdapter.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time
from typing import Any

from ..models import ActionCapabilities, ScreenInfo
from .base import ComputerAdapter

# Constantes Windows
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

KEYEVENTF_KEYUP = 0x0002

VK_MAP: dict[str, int] = {
    "ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09, "SPACE": 0x20,
    "BACKSPACE": 0x08, "DELETE": 0x2E, "INSERT": 0x2D,
    "HOME": 0x24, "END": 0x23, "PAGE_UP": 0x21, "PAGE_DOWN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "CTRL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "SUPER": 0x5B,
}
for _i in range(26):
    VK_MAP[chr(65 + _i)] = 65 + _i
for _i in range(10):
    VK_MAP[str(_i)] = 48 + _i

MOUSE_BUTTON_MAP = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUT_UNION)]


def _send_input(*inputs: _INPUT) -> int:
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    return ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(_INPUT))


def _make_mouse_input(flags: int, data: int = 0, x: int = 0, y: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi.dx = x
    inp.union.mi.dy = y
    inp.union.mi.mouseData = data
    inp.union.mi.dwFlags = flags
    inp.union.mi.time = 0
    return inp


def _make_keybd_input(vk: int, flags: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.wScan = 0
    inp.union.ki.dwFlags = flags
    inp.union.ki.time = 0
    return inp


class WindowsAdapter(ComputerAdapter):
    """Adaptador Windows usando ctypes (user32.dll)."""

    def __init__(self) -> None:
        if not hasattr(ctypes, "windll"):
            raise RuntimeError("ctypes.windll não disponível")
        self._user32 = ctypes.windll.user32

    @property
    def name(self) -> str:
        return "windows"

    @property
    def available(self) -> bool:
        return True

    def discover_capabilities(self) -> ActionCapabilities:
        return ActionCapabilities(
            mouse=True, keyboard=True, scroll=True,
            window_focus=True, type_text=True, hotkey=True,
            detail="Windows (ctypes/user32)",
        )

    def get_screen_info(self) -> ScreenInfo:
        SM_CXSCREEN, SM_CYSCREEN = 0, 1
        w = self._user32.GetSystemMetrics(SM_CXSCREEN)
        h = self._user32.GetSystemMetrics(SM_CYSCREEN)
        return ScreenInfo(width=w or 1920, height=h or 1080)

    def mouse_move(self, x: int, y: int) -> None:
        self._user32.SetCursorPos(x, y)

    def mouse_click(self, x: int, y: int, button: str = "left") -> None:
        self._user32.SetCursorPos(x, y)
        down_flag, up_flag = MOUSE_BUTTON_MAP.get(button, MOUSE_BUTTON_MAP["left"])
        inp_down = _make_mouse_input(down_flag)
        inp_up = _make_mouse_input(up_flag)
        _send_input(inp_down, inp_up)

    def mouse_double_click(self, x: int, y: int) -> None:
        self._user32.SetCursorPos(x, y)
        down, up = MOUSE_BUTTON_MAP["left"]
        inp = [
            _make_mouse_input(down), _make_mouse_input(up),
            _make_mouse_input(down), _make_mouse_input(up),
        ]
        _send_input(*inp)

    def mouse_scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self._user32.SetCursorPos(x, y)
        data = amount * WHEEL_DELTA
        inp = _make_mouse_input(MOUSEEVENTF_WHEEL, data=data)
        _send_input(inp)

    def key_press(self, key: str) -> None:
        vk = VK_MAP.get(key.upper())
        if vk is None:
            raise ValueError(f"Tecla mapeada não encontrada: {key}")
        inp_down = _make_keybd_input(vk)
        inp_up = _make_keybd_input(vk, flags=KEYEVENTF_KEYUP)
        _send_input(inp_down, inp_up)

    def hotkey(self, keys: list[str]) -> None:
        vk_list = []
        for k in keys:
            vk = VK_MAP.get(k.upper())
            if vk is None:
                raise ValueError(f"Tecla mapeada não encontrada: {k}")
            vk_list.append(vk)
        inputs = [_make_keybd_input(vk) for vk in vk_list]
        inputs += [_make_keybd_input(vk, flags=KEYEVENTF_KEYUP) for vk in reversed(vk_list)]
        _send_input(*inputs)

    def type_text(self, text: str, delay_ms: int = 0) -> None:
        KEYEVENTF_UNICODE = 0x0004
        for ch in text:
            inp = _INPUT()
            inp.type = INPUT_KEYBOARD
            inp.union.ki.wVk = 0
            inp.union.ki.wScan = ord(ch)
            inp.union.ki.dwFlags = KEYEVENTF_UNICODE
            inp.union.ki.time = 0
            inp_up = _INPUT()
            inp_up.type = INPUT_KEYBOARD
            inp_up.union.ki.wVk = 0
            inp_up.union.ki.wScan = ord(ch)
            inp_up.union.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            inp_up.union.ki.time = 0
            _send_input(inp, inp_up)
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

    def focus_window(self, title: str | None = None, process: str | None = None) -> bool:
        if title:
            hwnd = self._user32.FindWindowW(None, title)
            if hwnd:
                self._user32.SetForegroundWindow(hwnd)
                return True
        if process:
            import subprocess
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     f"(Get-Process -Name '{process}' -ErrorAction SilentlyContinue "
                     f"| Where-Object {{$_.MainWindowHandle -ne 0}} "
                     f"| Select-Object -First 1).MainWindowHandle"],
                    capture_output=True, text=True, timeout=5,
                )
                hwnd_str = result.stdout.strip()
                if hwnd_str and hwnd_str.isdigit():
                    hwnd = int(hwnd_str)
                    self._user32.SetForegroundWindow(hwnd)
                    return True
            except Exception:
                pass
        return False
