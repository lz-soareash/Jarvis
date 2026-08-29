"""Política de permissões (Fase 4): o nível EFETIVO de cada ferramenta.

O nível base vem da declaração da ferramenta; `ToolPolicy` permite ao usuário
restringir/liberar por ferramenta de forma persistente. O agente consulta sempre
o nível efetivo aqui — nunca a declaração em bruto.
"""

from sqlalchemy.orm import Session as OrmSession

from app.core.enums import PermissionLevel
from app.models import ToolPolicy
from app.schemas.permissions import ToolPermissionOut
from app.tools import registry as tool_registry


def effective_level(db: OrmSession, tool_name: str) -> PermissionLevel:
    """Nível efetivo da ferramenta: override persistente ou o declarado."""
    policy = db.get(ToolPolicy, tool_name)
    if policy is not None:
        try:
            return PermissionLevel(policy.permission_level)
        except ValueError:
            return PermissionLevel.LEVEL_3
    tool = tool_registry.get_tool_registry().get(tool_name)
    return tool.permission_level if tool is not None else PermissionLevel.LEVEL_3


def list_tool_permissions(db: OrmSession) -> list[ToolPermissionOut]:
    """Catálogo de ferramentas com a política efetiva (default ou override)."""
    tools = tool_registry.get_tool_registry().all()
    out: list[ToolPermissionOut] = []
    for tool in tools:
        level = effective_level(db, tool.name)
        policy = db.get(ToolPolicy, tool.name)
        out.append(
            ToolPermissionOut(
                tool_name=tool.name,
                description=tool.description,
                permission_level=level.value,
                risk=tool.risk.value,
                requires_confirmation=level.requires_confirmation,
                blocked_by_default=level.blocked_by_default,
                source="override" if policy is not None else "default",
            )
        )
    return out


def get_tool_permission(db: OrmSession, tool_name: str) -> ToolPermissionOut | None:
    tool = tool_registry.get_tool_registry().get(tool_name)
    if tool is None:
        return None
    level = effective_level(db, tool_name)
    policy = db.get(ToolPolicy, tool_name)
    return ToolPermissionOut(
        tool_name=tool.name,
        description=tool.description,
        permission_level=level.value,
        risk=tool.risk.value,
        requires_confirmation=level.requires_confirmation,
        blocked_by_default=level.blocked_by_default,
        source="override" if policy is not None else "default",
    )


def set_policy(db: OrmSession, tool_name: str, permission_level: int) -> ToolPolicy:
    policy = db.get(ToolPolicy, tool_name)
    if policy is None:
        policy = ToolPolicy(tool_name=tool_name, permission_level=permission_level)
        db.add(policy)
    else:
        policy.permission_level = permission_level
    db.commit()
    db.refresh(policy)
    return policy


def clear_policy(db: OrmSession, tool_name: str) -> bool:
    policy = db.get(ToolPolicy, tool_name)
    if policy is None:
        return False
    db.delete(policy)
    db.commit()
    return True