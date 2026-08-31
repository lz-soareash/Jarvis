import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider, AIProviderError
from app.db.session import get_db
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    MessageOut,
    SessionCreate,
    SessionOut,
)
from app.services import agent, chat as chat_service
from app.services import summarizer
from app.services.atlas_router import route as atlas_route
from app.services.chat import build_context, sse_event

from .deps import get_ai_provider

logger = logging.getLogger("jarvis.api")

router = APIRouter(prefix="/api", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@router.post("/sessions", response_model=SessionOut, status_code=201)
def create_session(
    payload: SessionCreate = SessionCreate(),
    db: OrmSession = Depends(get_db),
) -> SessionOut:
    session = chat_service.create_session(db, payload.title)
    return chat_service.to_session_out(session)


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: OrmSession = Depends(get_db)) -> list[SessionOut]:
    return [chat_service.to_session_out(s) for s in chat_service.list_sessions(db)]


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: str, db: OrmSession = Depends(get_db)) -> SessionOut:
    session = chat_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return chat_service.to_session_out(session)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, db: OrmSession = Depends(get_db)) -> None:
    session = chat_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    chat_service.delete_session(db, session)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: str, db: OrmSession = Depends(get_db)) -> list[MessageOut]:
    session = chat_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return [
        MessageOut.model_validate(m) for m in chat_service.list_messages(db, session_id)
    ]


@router.post("/sessions/{session_id}/messages", response_model=ChatResponse)
async def send_message(
    session_id: str,
    body: ChatRequest,
    db: OrmSession = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
):
    session = chat_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    chat_service.add_user_message(db, session, body.content)

    try:
        await summarizer.summarize_chunk(db, session_id=session_id, provider=provider)
    except Exception:  # noqa: BLE001 — resumo é best-effort
        logger.exception("Resumo periódico falhou (best-effort)")

    # Fase 10 — Atlas como camada de inteligência externa.
    # Tenta delegar ao Atlas ANTES do fluxo local; se indisponível/não
    # configurado, cai no fallback local atual (não quebra nada).
    atlas_response = atlas_route(db, session_id, body.content)
    if atlas_response is not None:
        generator = _atlas_sse(session_id, db, atlas_response)
        return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)

    if body.tools:
        generator = agent.run_agent(session_id, provider, db, body.content)
        return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)

    if body.stream:
        history, system = await build_context(db, session_id, provider, query=body.content)
        generator = chat_service.stream_reply(
            session_id, history, provider, db, system=system or None
        )
        return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)

    if not provider.is_configured:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY não configurada (arquivo .env)",
        )

    try:
        reply = await chat_service.generate_reply(db, session_id, provider, query=body.content)
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ChatResponse(session_id=session_id, message=MessageOut.model_validate(reply))


def _atlas_meta(atlas_response) -> dict:
    """Metadados da resposta do Atlas persistidos junto à mensagem."""
    return {
        "source": "atlas",
        "provider": atlas_response.provider,
        "classification": (
            atlas_response.classification.model_dump()
            if atlas_response.classification is not None
            else None
        ),
        "sources": [s.model_dump() for s in atlas_response.sources],
        "proposals": [p.model_dump() for p in atlas_response.proposals],
        "agent_run": (
            atlas_response.agent_run.model_dump()
            if atlas_response.agent_run is not None
            else None
        ),
        "semantic_available": atlas_response.semantic_available,
    }


def _atlas_sse(session_id: str, db: OrmSession, atlas_response):
    """Emite a resposta do Atlas como SSE compatível com o frontend do JARVIS.

    Persiste a resposta como mensagem do assistente com metadados (`source=atlas`)
    e reproduz o contrato SSE (`start`/`chunk`/`done`) que o frontend já consome,
    sem exigir flags do lado do usuário.
    """
    import json

    text = atlas_response.answer or ""

    if not text:
        yield sse_event({"type": "done"})
        return

    message = chat_service.add_atlas_reply(
        db,
        session_id,
        text,
        _atlas_meta(atlas_response),
    )

    yield sse_event({"type": "start"})
    chunk_size = 120
    for i in range(0, len(text), chunk_size):
        yield sse_event({"type": "chunk", "text": text[i : i + chunk_size]})
    yield sse_event(
        {
            "type": "done",
            "message": json.loads(MessageOut.model_validate(message).model_dump_json()),
        }
    )