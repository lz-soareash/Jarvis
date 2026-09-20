from contextlib import asynccontextmanager
import asyncio
import json
import re
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.router import api_router
from app.core.config import REPO_ROOT, settings
from app.core.logging import configure_logging
from app.db.session import init_db
from app.remote.errors import RemoteError
from app.remote.runtime import start_remote_gateway, stop_remote_gateway

FRONTEND_DIR = REPO_ROOT / "frontend"

configure_logging()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_REMOTE_PREFIX = "/api/remote"


def _request_id_from(request: Request) -> str:
    raw = request.headers.get("x-request-id", "").strip()
    return raw if raw and _UUID_RE.match(raw) else str(uuid.uuid4())


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
    # Fase 23: Core Link WAN (relé WebSocket outbound, peer `core`). No-op
    # quando REMOTE_GATEWAY_ENABLED=false (default) — sem conexão/thread.
    from app.remote.link_runtime import start_remote_link, stop_remote_link

    link = await start_remote_link()
    if link is not None:
        app.state.remote_gateway_link = link
    # Fase 17: worker proativo — apenas quando alguma capacidade está ligada
    # (default dos dois é False; sem thread/loop infinita no padrão).
    app.state.proactive_worker = None
    if settings.proactive_enabled or settings.proactive_scheduler_enabled:
        from app.proactive.engine import worker_loop

        task = asyncio.create_task(worker_loop())
        app.state.proactive_worker = task
        if settings.proactive_enabled:
            emit_proactive("system.ready", source="system")
    try:
        yield
    finally:
        if app.state.proactive_worker is not None:
            app.state.proactive_worker.cancel()
            try:
                await app.state.proactive_worker
            except asyncio.CancelledError:
                pass
            app.state.proactive_worker = None
        if settings.proactive_enabled:
            emit_proactive("system.shutdown", source="system")
        await stop_remote_gateway()
        if hasattr(app.state, "remote_gateway"):
            del app.state.remote_gateway
        await stop_remote_link()
        if hasattr(app.state, "remote_gateway_link"):
            del app.state.remote_gateway_link


def emit_proactive(event_type: str, *, source: str = "system") -> None:
    """Publica um evento proativo sem nunca derrubar o startup/shutdown."""
    try:
        from app.proactive.events import emit

        emit(event_type, source=source)
    except Exception:  # noqa: BLE001 — eventos best-effort no lifecycle
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="JARVIS Core — orquestração de IA, memória operacional, ferramentas e permissões.",
        lifespan=lifespan,
    )

    # Fase 16 — apenas quando o acesso remoto está HABILITADO E origins foram
    # configuradas explicitamente. Padrão: sem CORS (restritivo). `*` nunca é
    # aceito por `remote_cors_origin_list()`.
    from starlette.middleware.cors import CORSMiddleware

    from app.remote.config import remote_cors_origin_list

    cors_origins = remote_cors_origin_list() if settings.remote_enabled else []
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
        )

    app.include_router(api_router)

    # Fase 16 — fronteira remota: erros estruturados (nunca stack trace) e teto
    # de payload físico nos endpoints `/api/remote/*` (413 antes do AI Core).
    app.add_middleware(RemotePayloadGuardMiddleware)
    app.add_middleware(RemoteErrorHandlerMiddleware)

    # Fase 28.1 — APIs sensíveis (remotas, sessões, aprovações, ops) respondem
    # com `Cache-Control: no-store` (mais externo: cobre também os erros).
    app.add_middleware(NoStoreHeadersMiddleware)

    # Frontend Vanilla/PWA servido pelo próprio Core na mesma porta (8100).
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    else:
        @app.get("/", tags=["meta"])
        async def root() -> dict:
            return {"app": settings.app_name, "version": __version__, "docs": "/docs"}

    return app


class RemotePayloadGuardMiddleware:
    """Rejeita payloads acima do teto nos endpoints remotos (Fase 16, seção 22).

    Dupla proteção em `POST/PUT/PATCH` de `/api/remote/*`:
    - `Content-Length` declarado acima do teto → 413 imediato (sem ler o corpo);
    - corpo sem `Content-Length` confiável (chunked) → o middleware BUFFERS os
      bytes reais e corta no teto (413) antes do AI Core. Fase 28.1 fecha o gap
      em que uma chamada chunked poderia ultrapassar o limite físico.
    O contrato de erro é o mesmo do protocolo.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        guarded = (
            scope["type"] == "http"
            and scope.get("path", "").startswith(_REMOTE_PREFIX)
            and scope.get("method") in ("POST", "PUT", "PATCH")
        )
        if not guarded:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw_length = headers.get(b"content-length")
        declared = int(raw_length) if raw_length and raw_length.isdigit() else None
        if declared is not None and declared > settings.remote_max_payload_bytes:
            await self._send_too_large(scope, send)
            return

        limit = settings.remote_max_payload_bytes
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"") or b""
            total += len(chunk)
            if total > limit:
                await self._send_too_large(scope, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        canned = False

        async def replayed_receive():
            nonlocal canned
            if not canned:
                canned = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replayed_receive, send)

    async def _send_too_large(self, scope, send) -> None:
        request = Request(scope)
        request_id = _request_id_from(request)
        body = json.dumps(
            {
                "type": "error",
                "request_id": request_id,
                "code": "PAYLOAD_TOO_LARGE",
                "message": "payload acima do limite permitido",
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"x-request-id", request_id.encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


_SENSITIVE_API_PREFIXES = (
    "/api/remote/",
    "/api/sessions",
    "/api/approvals",
    "/api/ops",
)


class NoStoreHeadersMiddleware:
    """`Cache-Control: no-store` nas respostas de APIs sensíveis (Fase 28.1).

    Histórico de sessão, aprovações, observabilidade e toda a fronteira remota
    não podem ser servidos de cache (proxies/CDN/imobolitan rede). As demais
    rotas (front-end estático) passam sem alteração.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not path.startswith(_SENSITIVE_API_PREFIXES):
            await self.app(scope, receive, send)
            return

        async def send_wrapped(message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapped)


class RemoteErrorHandlerMiddleware:
    """Converte `RemoteError` no contrato `{"type":"error"...}` (Fase 16).

    Mantém o excesso (stack traces, paths) fora da resposta; a mensagem já é
    sanitizada. `request_id` é lido do header ou gerado e ecoado.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        request_id = _request_id_from(request)
        try:
            await self.app(scope, receive, send)
        except RemoteError as exc:
            body = json.dumps(exc.to_body(request_id)).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": exc.http_status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"x-request-id", request_id.encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})


app = create_app()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)