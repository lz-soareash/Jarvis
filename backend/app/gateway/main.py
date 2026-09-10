"""Gateway WAN (Fase 23) — aplicação FastAPI separada e deployável.

Deploy: ``python backend/scripts/run_gateway.py`` (ou uvicorn app.gateway.main:app
com backend no path). Endpoints:

- ``GET /health``        — estado sanitizado do relé (healthcheck p/ VPS/cloud);
- ``WS /api/remote/ws``  — conexão de peers (Core e móveis).

O Gateway NÃO usa o banco nem os Settings do Core. Papéis e identidade de
dispositivos são decididos pelo Core via trust relay; este processo só roteia.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket

from .config import get_settings
from .hub import RelayHub
from .limits import GatewayLimits
from .registry import GatewayRegistry
from .ops import gateway_snapshot

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _version() -> str:
    try:
        version = (_REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        return version or "0.22.0"
    except OSError:
        return "0.22.0"


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        _app.state.gateway_hub = RelayHub(
            registry=GatewayRegistry(),
            limits=GatewayLimits(),
        )
        logging.getLogger("jarvis.gateway").info(
            "Gateway WAN pronto (ws=%s)", settings.ws_path
        )
        yield
        _app.state.gateway_hub = None

    app = FastAPI(
        title=settings.name,
        version=_version(),
        description="VEGA Gateway WAN — relé WebSocket (trust relay ao Core).",
        lifespan=_lifespan,
    )

    @app.get("/", tags=["meta"])
    async def root() -> dict:
        return {"app": settings.name, "version": _version()}

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        hub = getattr(app.state, "gateway_hub", None)
        if hub is None:
            return {"status": "starting", "version": _version()}
        payload = gateway_snapshot(hub)
        payload["status"] = "ok"
        return payload

    @app.websocket(settings.ws_path)
    async def remote_ws(websocket: WebSocket) -> None:
        from .server import remote_ws_endpoint

        await remote_ws_endpoint(websocket)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.gateway.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )