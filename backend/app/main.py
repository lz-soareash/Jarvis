from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.router import api_router
from app.core.config import REPO_ROOT, settings
from app.core.logging import configure_logging
from app.db.session import init_db
from app.remote.runtime import start_remote_gateway, stop_remote_gateway

FRONTEND_DIR = REPO_ROOT / "frontend"

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Fase 12.2: identidade remota — registra/valida o root device a partir da
    # configuração (no-op quando REMOTE_ENABLED=false ou sem device_id/token).
    from app.db.session import SessionLocal
    from app.remote.registrar import bootstrap_root_device

    with SessionLocal() as db:
        bootstrap_root_device(db)
    # Fase 12.1: transporte remoto (outbound) — sem worker/conexão se REMOTE_ENABLED=false.
    gateway = await start_remote_gateway()
    if gateway is not None:
        app.state.remote_gateway = gateway
    try:
        yield
    finally:
        await stop_remote_gateway()
        if hasattr(app.state, "remote_gateway"):
            del app.state.remote_gateway


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="JARVIS Core — orquestração de IA, memória operacional, ferramentas e permissões.",
        lifespan=lifespan,
    )
    app.include_router(api_router)

    # Frontend Vanilla/PWA servido pelo próprio Core na mesma porta (8100).
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    else:
        @app.get("/", tags=["meta"])
        async def root() -> dict:
            return {"app": settings.app_name, "version": __version__, "docs": "/docs"}

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)