from fastapi import APIRouter

from .chat import router as chat_router
from .health import router as health_router
from .memory import router as memory_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(chat_router)
api_router.include_router(memory_router)