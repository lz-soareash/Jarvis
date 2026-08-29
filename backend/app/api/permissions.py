from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from app.core.enums import PermissionLevel
from app.db.session import get_db
from app.schemas.permissions import ToolPermissionOut, ToolPermissionUpdate
from app.services import permissions as permission_service

router = APIRouter(prefix="/api", tags=["permissions"])


@router.get("/permissions", response_model=list[ToolPermissionOut])
def list_permissions(db: OrmSession = Depends(get_db)) -> list[ToolPermissionOut]:
    """Catálogo de ferramentas com a política de permissão efetiva."""
    return permission_service.list_tool_permissions(db)


@router.put("/permissions/{tool_name}", response_model=ToolPermissionOut)
def set_permission(
    tool_name: str,
    payload: ToolPermissionUpdate,
    db: OrmSession = Depends(get_db),
) -> ToolPermissionOut:
    """Ajusta o nível de permissão de uma ferramenta (persistente)."""
    try:
        level = PermissionLevel(payload.permission_level)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Nível inválido (use 0 a 3)"
        ) from None
    permission_service.set_policy(db, tool_name, permission_level=level.value)
    out = permission_service.get_tool_permission(db, tool_name)
    if out is None:
        raise HTTPException(
            status_code=404, detail=f"Ferramenta '{tool_name}' não registrada"
        )
    return out


@router.delete("/permissions/{tool_name}", status_code=204)
def reset_permission(tool_name: str, db: OrmSession = Depends(get_db)) -> None:
    """Remove o override e volta ao nível declarado pela ferramenta."""
    if permission_service.get_tool_permission(db, tool_name) is None:
        raise HTTPException(
            status_code=404, detail=f"Ferramenta '{tool_name}' não registrada"
        )
    permission_service.clear_policy(db, tool_name)