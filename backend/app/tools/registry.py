"""Registro central de ferramentas do Tool Engine.

Apenas tools registradas aqui são executáveis — o modelo só pode propor
chamadas que existem neste catálogo (nunca comandos arbitrários).
"""

from app.core.enums import PermissionLevel, RiskLevel
from app.schemas.ai import ToolDeclaration

from .base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Ferramenta sem nome")
        if tool.name in self._tools:
            raise ValueError(f"Ferramenta já registrada: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def declarations(self) -> list[ToolDeclaration]:
        return [tool.declaration() for tool in self._tools.values()]

    def permission_for(self, name: str) -> PermissionLevel | None:
        tool = self._tools.get(name)
        return tool.permission_level if tool is not None else None

    def risk_for(self, name: str) -> RiskLevel | None:
        tool = self._tools.get(name)
        return tool.risk if tool is not None else None


_default_registry: ToolRegistry | None = None


def build_default_registry() -> ToolRegistry:
    from .builtins import GetSystemInfo, GetTime, RecallMemory, StoreMemory
    from .computer import (
        CloseApplication,
        GetSystemStats,
        GetSystemStatus,
        KillProcess,
        ListProcesses,
        LockComputer,
        Mute,
        NextTrack,
        OpenApplication,
        OpenFile,
        OpenFolder,
        OpenUrl,
        PauseMedia,
        PlayMedia,
        PreviousTrack,
        RestartComputer,
        RunAllowedCommand,
        ShutdownComputer,
        SleepComputer,
        StopProcess,
        VolumeDown,
        VolumeUp,
    )
    from .developer import DevDiagnostics, DevGetConfig, DevGetToolSchema, DevListTools
    from .filesystem import DeletePath, ListDir, MakeDir, ReadFile, WriteFile
    from .mobile import (
        MobileBatteryStatus,
        MobileDeviceInfo,
        MobileMediaStatus,
        MobileNetworkStatus,
        MobileOpenApp,
        MobileOpenUrl,
        MobileSetBrightness,
        MobileSetVolume,
        MobileVibrate,
    )
    from .web import WebFetch, WebSearch
    from .wol import WakeOnLan
    from app.perception.tool import ObserveComputer
    from app.action.tool import ComputerActionTool
    from app.computer_agent.tool import ComputerUseTool

    registry = ToolRegistry()
    # Builtin tools
    registry.register(GetTime())
    registry.register(GetSystemInfo())
    registry.register(StoreMemory())
    registry.register(RecallMemory())
    # Computer tools — LEVEL 0
    registry.register(GetSystemStats())
    registry.register(GetSystemStatus())
    registry.register(ListProcesses())
    registry.register(PlayMedia())
    registry.register(PauseMedia())
    registry.register(NextTrack())
    registry.register(PreviousTrack())
    registry.register(VolumeUp())
    registry.register(VolumeDown())
    registry.register(Mute())
    # Computer tools — LEVEL 1
    registry.register(OpenApplication())
    registry.register(OpenUrl())
    registry.register(OpenFile())
    registry.register(OpenFolder())
    # Computer tools — LEVEL 2
    registry.register(CloseApplication())
    registry.register(StopProcess())
    registry.register(RunAllowedCommand())
    # Computer tools — LEVEL 3
    registry.register(KillProcess())
    registry.register(LockComputer())
    registry.register(SleepComputer())
    registry.register(RestartComputer())
    registry.register(ShutdownComputer())
    # Filesystem tools
    registry.register(ListDir())
    registry.register(ReadFile())
    registry.register(WriteFile())
    registry.register(MakeDir())
    registry.register(DeletePath())
    # Developer tools
    registry.register(DevListTools())
    registry.register(DevGetToolSchema())
    registry.register(DevGetConfig())
    registry.register(DevDiagnostics())
    # Wake-on-LAN (Fase 12.6)
    registry.register(WakeOnLan())
    # Perception (Fase 15) — LEVEL_0, leitura segura do computador
    registry.register(ObserveComputer())
    # Computer Action Layer (Fase 18) — LEVEL_1, ações controladas e auditadas
    registry.register(ComputerActionTool())
    # Computer Agent (Fase 19) — LEVEL_2, delega objetivos de Computer Use
    registry.register(ComputerUseTool())
    # Web Research (Fase 14)
    registry.register(WebSearch())  # LEVEL_0 — busca
    registry.register(WebFetch())  # LEVEL_1 — fetch (confirmação)
    # VEGA Mobile Agent Orchestration (Fase 26) — leituras LEVEL_0
    registry.register(MobileDeviceInfo())
    registry.register(MobileBatteryStatus())
    registry.register(MobileNetworkStatus())
    registry.register(MobileMediaStatus())
    # VEGA Mobile Agent Orchestration (Fase 26) — ajustes LEVEL_1 (risco LOW)
    registry.register(MobileOpenUrl())
    registry.register(MobileVibrate())
    registry.register(MobileSetVolume())
    registry.register(MobileSetBrightness())
    registry.register(MobileOpenApp())
    return registry


def get_tool_registry() -> ToolRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry