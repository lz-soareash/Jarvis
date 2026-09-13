from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Normaliza datetimes lidos do SQLite (que voltam naive) como UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Session(Base):
    """Uma conversa do usuário (memória operacional de sessão).

    `summary` e `summarized_count` alimentam o resumo rolante (Fase 2):
    quando a conversa cresce além da janela de contexto, os trechos antigos
    são comprimidos no resumo e o prompt passa a usar resumo + janela recente.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(200), default="Nova sessão")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summarized_count: Mapped[int] = mapped_column(Integer, default=0)
    # Fase 27 — contexto multi-turn determinístico da sessão (JSON): guarda o
    # `device` em uso (nome/capability/status/última ação) para o Core resolver
    # continuidade ("agora pesquisa FIAP") sem depender do LLM. NUNCA contém
    # tokens/segredos/IDs de hardware; o name amigável é a única parte injetada.
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )
    memories: Mapped[list["Memory"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Memory.created_at",
    )
    approvals: Mapped[list] = relationship(
        "ApprovalRequest",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ApprovalRequest.created_at",
    )


class Message(Base):
    """Uma troca individual (user | assistant) dentro de uma sessão."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    # Metadados opcionais da resposta (Fase 10): { "source": "atlas", ... }.
    # Guarda origem (atlas/local) e, quando aplicável, fontes/proposals do Atlas.
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[Session] = relationship(back_populates="messages")