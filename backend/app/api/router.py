from fastapi import APIRouter

from .approvals import router as approvals_router
from .atlas import router as atlas_router
from .audit import router as audit_router
from .chat import router as chat_router
from .device import router as device_router
from .health import router as health_router
from .memory import router as memory_router
from .ops import router as ops_router
from .permissions import router as permissions_router
from app.proactive.api import router as proactive_router
from .remote import router as remote_router
from .research import router as research_router
from .system import router as system_router
from .tts import router as tts_router
from .workspace import router as workspace_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(chat_router)
api_router.include_router(memory_router)
api_router.include_router(approvals_router)
api_router.include_router(permissions_router)
api_router.include_router(audit_router)
api_router.include_router(system_router)
api_router.include_router(tts_router)
api_router.include_router(device_router)
api_router.include_router(atlas_router)
api_router.include_router(ops_router)
api_router.include_router(proactive_router)
api_router.include_router(remote_router)
api_router.include_router(workspace_router)
api_router.include_router(research_router)