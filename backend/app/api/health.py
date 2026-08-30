import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.ai.providers.base import AIProvider
from app.core.config import settings
from app.db.session import check_database
from app.schemas.health import AIHealthResponse, HealthResponse

from .deps import get_ai_provider

router = APIRouter(tags=["health"])

_AI_CACHE_TTL = 30.0
_ai_health_cache: tuple[float, AIHealthResponse] | None = None


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
    global _ai_health_cache
    now = time.monotonic()
    if _ai_health_cache is not None and now - _ai_health_cache[0] < _AI_CACHE_TTL:
        return _ai_health_cache[1]
    result = await provider.health_check()
    response = AIHealthResponse(**result.model_dump())
    _ai_health_cache = (now, response)
    return response