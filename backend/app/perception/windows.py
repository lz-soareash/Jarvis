"""Windows Perception Provider (Fase 15.2).

Implementação L0 segura e determinística de `PerceptionProvider` para Windows.

Reusa o `SystemController` existente (stats/processos via PowerShell nativo) e
adiciona a janela ativa via ctypes (user32.GetForegroundWindow + GetWindowText +
GetWindowThreadProcessId). Sem dependência obrigatória pesada: as capacidades
opcionais (screenshot/ocr/vision/accessibility) são detectadas reflexivamente
pelas bibliotecas realmente presentes — nunca inventadas.
"""

import ctypes
import logging
import os
import platform
import sys

from app.computer import controller as computer_controller
from app.computer.controller import SystemController, SystemControllerError

from .base import (
    ActiveWindow,
    ComputerCapabilities,
    ComputerObservation,
    PerceptionError,
    PerceptionProvider,
    PerceptionResult,
    ScreenshotMetadata,
)

logger = logging.getLogger("jarvis.perception.windows")


def _has_module(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _windows_active_window() -> ActiveWindow | None:
    """Janela ativa via user32 (ctypes) — apenas leitura, sem interferência."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        kernel32.GetModuleBaseNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or None

        pid = ctypes.c_ulong(0)
        thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        process_name: str | None = None
        # GetModuleBaseNameW precisa de um handle de processo com leitura.
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if handle:
            try:
                name_buf = ctypes.create_unicode_buffer(260)
                if kernel32.GetModuleBaseNameW(handle, 0, name_buf, 260):
                    process_name = name_buf.value or None
            finally:
                kernel32.CloseHandle(handle)

        if not title and not process_name:
            return None
        return ActiveWindow(title=title, process_name=process_name, pid=int(pid.value) if pid.value else None)
    except Exception as exc:  # noqa: BLE001 — janela ativa é best-effort
        logger.debug("falha ao obter janela ativa: %s", exc)
        return None


class WindowsPerceptionProvider(PerceptionProvider):
    """Provider de percepção do Windows (L0)."""

    name = "windows"

    def __init__(self, controller: SystemController | None = None) -> None:
        self._controller = controller or computer_controller.get_system_controller()
        self._injected = controller is not None
        self._capabilities: ComputerCapabilities | None = None

    def available(self) -> bool:
        return sys.platform == "win32"

    # ------------------------------------------------------------ capabilities
    def discover_capabilities(self) -> ComputerCapabilities:
        if self._capabilities is None:
            self._capabilities = self._build_capabilities()
        return self._capabilities

    def _build_capabilities(self) -> ComputerCapabilities:
        is_win = sys.platform == "win32"
        screenshot = is_win and _has_module("PIL") and _has_module("mss")
        return ComputerCapabilities(
            os="windows" if is_win else platform.system() or None,
            os_version=platform.release() if is_win else platform.release() or None,
            platform=sys.platform,
            screenshot=screenshot,
            active_window=is_win,
            processes=bool(getattr(self._controller, "list_processes", None)),
            accessibility_tree=False,
            ocr=False,
            vision=False,
            detail="windows provider (percepção nativa L0)" if is_win else "provider indisponível fora do Windows",
        )

    def _refresh_controller(self) -> None:
        """Recarrega o controlador (útil quando os testes trocam o fake).

        Controle injetado explicitamente no construtor é imutável (hermético);
        sem injeção, acompanha o singleton (permite troca por fakes em testes).
        """
        if self._injected:
            return
        try:
            current = computer_controller.get_system_controller()
        except Exception:  # noqa: BLE001
            return
        if current is not self._controller:
            self._controller = current
            self._capabilities = None

    # ---------------------------------------------------------------- observe
    def observe(self, *, include_processes: bool = False, max_processes: int = 30) -> PerceptionResult:
        if not self.available():
            return PerceptionResult.unavailable("percepção disponível apenas no Windows")
        self._refresh_controller()
        caps = self.discover_capabilities()
        obs = ComputerObservation(capabilities=caps, active_window=_windows_active_window())

        stats: dict = {}
        if caps.processes:
            try:
                stats = dict(self._controller.system_stats())
                stats.pop("boot_time", None)
                obs.system_stats = stats
            except SystemControllerError as exc:
                obs.errors.append(f"system_stats: {exc}")

        if include_processes and caps.processes:
            try:
                obs.processes = self._controller.list_processes(limit=max_processes)
            except SystemControllerError as exc:
                obs.errors.append(f"processes: {exc}")

        return PerceptionResult.ok(obs)

    # -------------------------------------------------------------- screenshot
    def capture_screenshot(self) -> ScreenshotMetadata | None:
        """Captura screenshot MANUAL apenas se a biblioteca estiver presente.

        O binário é gerado apenas para hash/metadata; nunca entra em logs ou
        eventos. Sem biblioteca → None (capacidade honesta = False).
        """
        if not self.available():
            raise PerceptionError("screenshot disponível apenas no Windows")
        try:
            import io

            import mss
            from PIL import Image

            with mss.mss() as sct:
                region = sct.monitors[1]
                raw = sct.grab(region)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
            width, height = img.size
            return ScreenshotMetadata.from_pixels(data, width, height)
        except Exception as exc:  # noqa: BLE001
            logger.debug("screenshot indisponível: %s", exc)
            return None


def get_perception_provider() -> PerceptionProvider:
    """Provider singleton reflexivo: o mais adequado ao ambiente detectado."""
    provider = WindowsPerceptionProvider()
    if provider.available():
        return provider
    return UnavailablePerceptionProvider()


class UnavailablePerceptionProvider(PerceptionProvider):
    """Fallback: sistema sem provider de percepção disponível (honesto)."""

    name = "unavailable"

    def available(self) -> bool:
        return False

    def discover_capabilities(self) -> ComputerCapabilities:
        return ComputerCapabilities(
            os=platform.system() or None,
            os_version=platform.release() or None,
            platform=sys.platform,
            detail="nenhum provider de percepção disponível neste ambiente",
        )

    def observe(self, *, include_processes: bool = False, max_processes: int = 30) -> PerceptionResult:
        return PerceptionResult.unavailable("percepção indisponível neste ambiente")
