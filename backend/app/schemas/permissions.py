from typing import Literal

from app.schemas.base import APIModel


class ToolPermissionOut(APIModel):
    """Visão de uma ferramenta sob a política de permissões (Fase 4)."""

    tool_name: str
    description: str
    permission_level: int
    risk: str
    requires_confirmation: bool
    blocked_by_default: bool
    source: Literal["default", "override"]


class ToolPermissionUpdate(APIModel):
    """Ajuste do nível de permissão de uma ferramenta."""

    permission_level: int