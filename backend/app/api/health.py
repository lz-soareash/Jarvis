from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.ai.providers.base import AIProvider
from app.core.config import settings
from app.db.session import check_database
from app.schemas.health import AIHealthResponse, HealthResponse

from .deps import get_ai_provider

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.version,
        env=settings.env,
        database="ok" if check_database() else "error",
        api_port=settings.port,
        ws_port=settings.ws_port,
        time=datetime.now(timezone.utc),
    )


@router.get("/health/ai", response_model=AIHealthResponse)
async def health_ai(
    provider: AIProvider = Depends(get_ai_provider),
) -> AIHealthResponse:
    result = await provider.health_check()
    return AIHealthResponse(**result.model_dump())