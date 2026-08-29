from fastapi import APIRouter, Depends, HTTPException, Query

from app.computer import SystemController, SystemControllerError
from app.computer.controller import get_system_controller

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/system/stats")
def system_stats(
    controller: SystemController = Depends(get_system_controller),
) -> dict:
    """Métricas atuais do computador (CPU, memória, disco, boot) — somente leitura."""
    try:
        return controller.system_stats()
    except SystemControllerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/system/processes")
def system_processes(
    limit: int = Query(50, ge=1, le=200),
    controller: SystemController = Depends(get_system_controller),
) -> list[dict]:
    """Processos em execução no computador — somente leitura."""
    try:
        return controller.list_processes(limit=limit)
    except SystemControllerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc