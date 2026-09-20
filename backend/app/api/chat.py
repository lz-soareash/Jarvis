import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from app.ai import core as ai_core
from app.ai.providers.base import AIProvider, AIProviderError
from app.db.session import get_db
from app.remote.auth import AuthenticatedDevice
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    MessageOut,
    SessionCreate,
    SessionOut,
)
from app.services import chat as chat_service

from .deps import get_ai_provider
from .remote_deps import device_anchor_session, optional_remote_device

logger = logging.getLogger("jarvis.api")

router = APIRouter(prefix="/api", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _remote_scope_owns(
    db: OrmSession, authed: AuthenticatedDevice | None, session_id: str
) -> bool:
    """Cliente remoto autenticado só acessa a PRÓPRIA âncora de sessão.

    `authed is None` = caminho local (SPA/Desktop/LAN) → comportamento atual.
    """
    if authed is None:
        return True
    return session_id == device_anchor_session(db, authed)


@router.post("/sessions", response_model=SessionOut, status_code=201)
def create_session(
    payload: SessionCreate = SessionCreate(),
    db: OrmSession = Depends(get_db),
) -> SessionOut:
    session = chat_service.create_session(db, payload.title)
    return chat_service.to_session_out(session)


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    db: OrmSession = Depends(get_db),
    authed: AuthenticatedDevice | None = Depends(optional_remote_device),
) -> list[SessionOut]:
    """Lista sessões. Com identidade remota: apenas a âncora do device."""
    sessions = chat_service.list_sessions(db)
    if authed is not None:
        anchor = device_anchor_session(db, authed)
        sessions = [s for s in sessions if s.id == anchor]
    return [chat_service.to_session_out(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(
    session_id: str,
    db: OrmSession = Depends(get_db),
    authed: AuthenticatedDevice | None = Depends(optional_remote_device),
) -> SessionOut:
    session = chat_service.get_session(db, session_id)
    if session is None or not _remote_scope_owns(db, authed, session_id):
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return chat_service.to_session_out(session)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    db: OrmSession = Depends(get_db),
    authed: AuthenticatedDevice | None = Depends(optional_remote_device),
) -> None:
    session = chat_service.get_session(db, session_id)
    if session is None or not _remote_scope_owns(db, authed, session_id):
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    chat_service.delete_session(db, session)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def list_messages(
    session_id: str,
    db: OrmSession = Depends(get_db),
    authed: AuthenticatedDevice | None = Depends(optional_remote_device),
) -> list[MessageOut]:
    """Histórico da sessão. Com identidade remota: só a âncora do device."""
    session = chat_service.get_session(db, session_id)
    if session is None or not _remote_scope_owns(db, authed, session_id):
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
    authed: AuthenticatedDevice | None = Depends(optional_remote_device),
):
    session = chat_service.get_session(db, session_id)
    if session is None or not _remote_scope_owns(db, authed, session_id):
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    chat_service.add_user_message(db, session, body.content)

    # Fase 11 — AI Core orquestra provedor (AI Router) + caminho + observabilidade.
    turn = await ai_core.handle_message(
        db,
        session_id=session_id,
        content=body.content,
        tools=body.tools,
        stream=body.stream,
        requested=provider,
    )

    if turn.kind in ("atlas", "agent", "stream") and turn.generator is not None:
        return StreamingResponse(
            turn.generator, media_type="text/event-stream", headers=SSE_HEADERS
        )

    if turn.kind in ("unconfigured",):
        raise HTTPException(status_code=503, detail=turn.error)

    if turn.kind == "provider_error":
        raise HTTPException(status_code=502, detail=turn.error) from None

    # turn.kind == "reply"
    return ChatResponse(
        session_id=session_id, message=MessageOut.model_validate(turn.message)
    )