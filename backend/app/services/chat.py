"""Serviço de chat: sessões, persistência, contexto (memória + resumo) e resposta."""
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

from . import memory as memory_service

logger = logging.getLogger("jarvis.chat")

DEFAULT_TITLE = "Nova sessão"

BASE_SYSTEM_PROMPT = (
    "Você é o JARVIS, um assistente pessoal de IA executando localmente. "
    "Responda de forma clara, direta e em português, usando o contexto e as "
    "memórias fornecidas quando forem úteis. Nunca invente memórias."
)


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


def add_atlas_reply(
    db: OrmSession,
    session_id: str,
    content: str,
    meta: dict,
) -> Message:
    """Persiste a resposta do Atlas como mensagem do assistente (+ metadados).

    `meta` vira o `metadata_json` da mensagem — origem/fontes/proposals do Atlas
    ficam preservados sem alterar a experiência de chat do usuário.
    """
    import json

    session = db.get(Session, session_id)
    message = Message(
        session_id=session_id,
        role="assistant",
        content=content,
        metadata_json=json.dumps(meta, ensure_ascii=False, default=str),
    )
    db.add(message)
    if session is not None:
        session.updated_at = utcnow()
    db.commit()
    db.refresh(message)
    return message


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

    Mensagens já absorvidas pelo resumo rolante (`summarized_count`) ficam de
    fora — o trecho antigo sobrevive como resumo no prompt sistêmico.
    """
    limit = limit or settings.max_context_messages
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id.asc())
    )
    rows = list(db.scalars(stmt).all())
    session = db.get(Session, session_id)
    start = session.summarized_count if session is not None else 0
    window = rows[start:][-limit:]
    return [
        AIMessage(role=row.role, content=row.content)
        for row in window
        if row.role in ("user", "assistant")
    ]


async def build_system_prompt(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    query: str | None = None,
) -> str:
    """Contexto SISTÊMICO injetado na IA: resumo rolante + memórias relevantes.

    Memórias relevantes são buscadas pelo texto da última pergunta (semântico
    quando há embedding; lexical no fallback). Sem chave, apenas o resumo e o
    prompt base chegam ao modelo.
    """
    session = db.get(Session, session_id)
    parts = [BASE_SYSTEM_PROMPT]

    if session is not None and session.summary:
        parts.append(f"[Resumo da conversa até agora]\n{session.summary}")

    try:
        if query:
            results = await memory_service.search_memories(
                db,
                query=query,
                session_id=session_id,
                include_global=True,
                limit=settings.memory_context_limit,
                provider=provider,
            )
        else:
            results = [
                (m, 0.0) for m in memory_service.list_memories(db)[: settings.memory_context_limit]
            ]
    except Exception:  # noqa: BLE001 — memória nunca quebra o chat
        logger.exception("Falha ao montar contexto de memórias")
        results = []

    if results:
        lines = "\n".join(f"- ({m.kind}) {m.content}" for m, _ in results)
        parts.append(f"[Memórias relevantes]\n{lines}")

    return "\n\n".join(parts)


async def build_context(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    query: str | None = None,
) -> tuple[list[AIMessage], str]:
    """Histórico (janela recente) + prompt sistêmico (memória + resumo)."""
    history = build_ai_messages(db, session_id)
    system = await build_system_prompt(db, session_id, provider, query=query)
    return history, system


def list_messages(db: OrmSession, session_id: str) -> list[Message]:
    stmt = select(Message).where(Message.session_id == session_id).order_by(Message.id)
    return list(db.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Geração de resposta
# ---------------------------------------------------------------------------

async def generate_reply(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    query: str | None = None,
) -> Message:
    """Resposta completa (não-stream): persiste a resposta do assistente."""
    history, system = await build_context(db, session_id, provider, query=query)
    response = await provider.generate(history, system=system)
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
    system: str | None = None,
) -> AsyncIterator[str]:
    """Resposta em streaming (SSE), com persistência da resposta ao final."""
    yield sse_event({"type": "start"})
    parts: list[str] = []
    try:
        async for chunk in provider.stream(history, system=system):
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