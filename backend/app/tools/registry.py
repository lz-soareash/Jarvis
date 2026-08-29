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
    from .computer import GetSystemStats, KillProcess, ListProcesses, OpenApp

    registry = ToolRegistry()
    registry.register(GetTime())
    registry.register(GetSystemInfo())
    registry.register(StoreMemory())
    registry.register(RecallMemory())
    registry.register(GetSystemStats())
    registry.register(ListProcesses())
    registry.register(OpenApp())
    registry.register(KillProcess())
    return registry


def get_tool_registry() -> ToolRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry