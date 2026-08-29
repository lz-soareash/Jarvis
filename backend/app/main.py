from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app import __version__
from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import init_db

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="JARVIS Core — orquestração de IA, memória operacional, ferramentas e permissões.",
        lifespan=lifespan,
    )
    app.include_router(api_router)

    @app.get("/", tags=["meta"])
    async def root() -> dict:
        return {
            "app": settings.app_name,
            "version": __version__,
            "status": "online",
            "docs": "/docs",
            "health": "/health",
            "health_ai": "/health/ai",
        }

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)