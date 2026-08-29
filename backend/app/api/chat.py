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
from app.services import chat as chat_service
from app.services.chat import build_ai_messages, sse_event

from .deps import get_ai_provider

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

    if body.stream:
        history = build_ai_messages(db, session_id)
        generator = chat_service.stream_reply(session_id, history, provider, db)
        return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)

    if not provider.is_configured:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY não configurada (arquivo .env)",
        )

    try:
        reply = await chat_service.generate_reply(db, session_id, provider)
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ChatResponse(session_id=session_id, message=MessageOut.model_validate(reply))