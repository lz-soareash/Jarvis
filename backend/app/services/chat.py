"""Serviço de chat: sessões, persistência, contexto básico e resposta da IA."""

import json
import logging
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider, AIProviderError
from app.core.config import settings
from app.models import Message, Session, utcnow
from app.schemas.ai import AIMessage
from app.schemas.chat import MessageOut, SessionOut

logger = logging.getLogger("jarvis.chat")

DEFAULT_TITLE = "Nova sessão"


# ---------------------------------------------------------------------------
# Sessões
# ---------------------------------------------------------------------------

def create_session(db: OrmSession, title: str | None = None) -> Session:
    session = Session(title=(title or DEFAULT_TITLE).strip() or DEFAULT_TITLE)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def list_sessions(db: OrmSession, limit: int = 50) -> list[Session]:
    stmt = select(Session).order_by(Session.updated_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def get_session(db: OrmSession, session_id: str) -> Session | None:
    return db.get(Session, session_id)


def delete_session(db: OrmSession, session: Session) -> None:
    db.delete(session)
    db.commit()


def to_session_out(session: Session) -> SessionOut:
    return SessionOut(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(session.messages),
    )


# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------

def _autotitle(db: OrmSession, session: Session, content: str) -> None:
    if session.title == DEFAULT_TITLE:
        session.title = content.strip()[:60] or DEFAULT_TITLE
        db.commit()


def add_user_message(db: OrmSession, session: Session, content: str) -> Message:
    _autotitle(db, session, content)
    message = Message(session_id=session.id, role="user", content=content)
    db.add(message)
    session.updated_at = utcnow()
    db.commit()
    db.refresh(message)
    db.refresh(session)
    return message


def build_ai_messages(
    db: OrmSession, session_id: str, limit: int | None = None
) -> list[AIMessage]:
    """Monta o contexto enviado à IA: a janela das últimas mensagens da sessão.

    Contexto básico da Fase 1 — janela simples e recente. O Context Engine
    (Fase 2) evoluirá este método sem mudar o contrato aqui.
    """
    limit = limit or settings.max_context_messages
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    rows = list(reversed(db.scalars(stmt).all()))
    return [
        AIMessage(role=row.role, content=row.content)
        for row in rows
        if row.role in ("user", "assistant")
    ]


def list_messages(db: OrmSession, session_id: str) -> list[Message]:
    stmt = select(Message).where(Message.session_id == session_id).order_by(Message.id)
    return list(db.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Geração de resposta
# ---------------------------------------------------------------------------

async def generate_reply(db: OrmSession, session_id: str, provider: AIProvider) -> Message:
    """Resposta completa (não-stream): persiste a resposta do assistente."""
    history = build_ai_messages(db, session_id)
    response = await provider.generate(history)
    message = Message(session_id=session_id, role="assistant", content=response.text)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


async def stream_reply(
    session_id: str,
    history: list[AIMessage],
    provider: AIProvider,
    db: OrmSession,
) -> AsyncIterator[str]:
    """Resposta em streaming (SSE), com persistência da resposta ao final."""
    yield sse_event({"type": "start"})
    parts: list[str] = []
    try:
        async for chunk in provider.stream(history):
            parts.append(chunk)
            yield sse_event({"type": "chunk", "text": chunk})
    except AIProviderError as exc:
        logger.warning("Chat de stream abortado: %s", exc)
        yield sse_event({"type": "error", "detail": str(exc)})
        return
    except Exception:
        logger.exception("Erro não tratado no streaming do chat")
        yield sse_event({"type": "error", "detail": "Erro interno ao gerar resposta."})
        return

    text = "".join(parts)
    session = db.get(Session, session_id)
    message = Message(session_id=session_id, role="assistant", content=text)
    db.add(message)
    if session is not None:
        session.updated_at = utcnow()
    db.commit()
    db.refresh(message)

    yield sse_event({"type": "done", "message": MessageOut.model_validate(message).model_dump(mode="json")})