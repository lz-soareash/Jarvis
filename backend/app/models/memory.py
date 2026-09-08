from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import MemoryKind
from app.db.base import Base
from app.models.session import utcnow


class Memory(Base):
    """Memória estruturada de longo prazo (facto, preferência, nota, resumo).

    `session_id` nulo = memória global/pessoal; preenchido = memória da sessão.
    `embedding` guarda o vetor semântico (JSON) para busca por similaridade.

    Fase 19.5 — metadata da política de escrita: `project` (associação a um
    projeto), `confidence` (proveniência), `source` (origem, ex.: `chat`,
    `tool`, `api`) e `expires_at` (TTL — memórias efêmeras são honoradas na
    busca; `None` = sem expiração). `content` é sanitizado ANTES da persistência.
    """

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default=MemoryKind.FACT.value, index=True)
    content: Mapped[str] = mapped_column(Text)
    project: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    confidence: Mapped[str] = mapped_column(String(40), default="unverified")
    source: Mapped[str | None] = mapped_column(String(60), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    session: Mapped["Session"] = relationship(back_populates="memories")