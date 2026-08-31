"""Endpoints de status/observabilidade da integração JARVIS ↔ Atlas (Fase 10).

Não expõe credenciais nem tokens — apenas configuração e disponibilidade.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.services.atlas_router import should_route_to_atlas

logger = logging.getLogger("jarvis.api")

router = APIRouter(prefix="/api", tags=["atlas"])


@router.get("/atlas/status")
def atlas_status() -> dict:
    """Estado da integração com o Atlas (sem expor segredos)."""
    enabled = bool(settings.atlas_enabled)
    configured = should_route_to_atlas()
    return {
        "enabled": enabled,
        "configured": configured,
        "base_url": settings.atlas_base_url if enabled else None,
        "detail": (
            "pronto para roteamento (habilitado e configurado)"
            if configured
            else (
                "habilitado mas sem credenciais (ATLAS_EMAIL/ATLAS_PASSWORD)"
                if enabled
                else "desabilitado (ATLAS_ENABLED=false)"
            )
        ),
    }


@router.get("/atlas/health")
def atlas_health() -> dict:
    """Healthcheck ativo contra o Atlas (login + status da API)."""
    from app.services.atlas_client import (
        AtlasAuthError,
        AtlasUnavailable,
        get_atlas_client,
    )

    if not should_route_to_atlas():
        raise HTTPException(status_code=503, detail="Atlas não está habilitado/configurado.")

    try:
        client = get_atlas_client()
        # Um login bem-sucedido valida URL + credenciais (ping leve ao vivo).
        client.auth_test()
        return {"status": "ok", "base_url": settings.atlas_base_url}
    except AtlasAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AtlasUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
