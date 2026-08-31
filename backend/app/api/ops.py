"""Central de Operações (Fase 11) — endpoints de observabilidade."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from app.ai.registry import get_ai_router
from app.ai.providers.base import AIProviderStatus
from app.db.session import get_db
from app.schemas.ops import (
    ExecutionEventOut,
    OpsOverview,
    OpsProviderLive,
)
from app.services import ops as ops_service

router = APIRouter(prefix="/api/ops", tags=["ops"])


@router.get("/overview", response_model=OpsOverview)
def overview(db: OrmSession = Depends(get_db)) -> OpsOverview:
    """Quadro geral do JARVIS: AI Core, provedores, memória, tarefas, tools, Atlas."""
    return ops_service.build_overview(db)


@router.get("/providers", response_model=list[OpsProviderLive])
def providers() -> list[OpsProviderLive]:
    """Estado dos provedores de IA registrados no AI Router (sem rede)."""
    router = get_ai_router()
    rows: list[OpsProviderLive] = []
    for status in router.statuses():
        rows.append(
            OpsProviderLive(
                name=status.provider,
                model=status.model,
                configured=status.status == "ok",
                status=status.status,
                detail=status.detail,
            )
        )
    return rows


@router.get("/providers/{name}/health", response_model=AIProviderStatus)
async def provider_health(name: str) -> AIProviderStatus:
    """Healthcheck ao vivo do provedor escolhido (faz chamada real quando possível)."""
    router = get_ai_router()
    provider = next((p for p in router.all() if p.name == name), None)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Provedor '{name}' não registrado")
    return await provider.health_check()


@router.get("/providers/{name}/select", response_model=dict)
def select_provider(name: str) -> dict:
    """Reporta a preferência do provedor no AI Router (ordem configurada)."""
    router = get_ai_router()
    provider = next((p for p in router.all() if p.name == name), None)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Provedor '{name}' não registrado")
    return router.preferencia_estado(provider)


@router.get("/events", response_model=list[ExecutionEventOut])
def events(
    session_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    db: OrmSession = Depends(get_db),
) -> list[ExecutionEventOut]:
    """Eventos observáveis recentes (sanitizados — sem conteúdo/secrets)."""
    rows = ops_service.list_events(
        db, session_id=session_id, event_type=event_type, limit=min(limit, 200)
    )
    return [ops_service.to_out(e) for e in rows]