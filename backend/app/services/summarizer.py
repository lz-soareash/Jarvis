"""Compactação de conversas longas em um resumo rolante por sessão.

Quando o número de mensagens da sessão passa do limite (`summarize_after_messages`),
os trechos antigos ainda não resumidos são comprimidos num resumo incremental
(`Session.summary`), mantendo o contexto vivo sem estourar a janela de tokens.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider
from app.core.config import settings
from app.models import Message, Session

logger = logging.getLogger("jarvis.summarizer")

SUMMARY_INSTRUCTION = (
    "Resuma a conversa abaixo em poucas frases objetivas, mantendo fatos, preferências, "
    "decisões e tarefas pendentes. Se houver um resumo anterior, incorpore-o ao novo."
)


async def summarize_chunk(
    db: OrmSession,
    *,
    session_id: str,
    provider: AIProvider,
) -> bool:
    """Gera/atualiza o resumo rolante da sessão. Retorna True se resumiu."""
    total = db.scalar(
        select(func.count(Message.id)).where(Message.session_id == session_id)
    ) or 0
    session = db.get(Session, session_id)
    if session is None:
        return False

    unsummarized = total - session.summarized_count
    if total < settings.summarize_after_messages or unsummarized < settings.summary_chunk:
        return False

    rows = list(
        db.scalars(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.id)
            .offset(session.summarized_count)
            .limit(settings.summary_chunk)
        ).all()
    )
    if not rows:
        return False

    previous = f"Resumo anterior:\n{session.summary}\n\n" if session.summary else ""
    dialogue = "\n".join(f"{m.role}: {m.content}" for m in rows)
    response = await provider.analyze(previous + dialogue, instruction=SUMMARY_INSTRUCTION)
    if not response.text:
        return False

    session.summary = response.text.strip()
    session.summarized_count = session.summarized_count + len(rows)
    db.commit()
    logger.info("Sessão %s resumida: +%d mensagens", session_id, len(rows))
    return True