"""Pesquisa Web e conhecimento (Fase 14).

`ResearchRun` persiste uma execução de pesquisa (objetivo, consultas, resposta
com citações e metadados de proveniência) — é a fonte dos endpoints
`GET /api/research/{id}` e `/sources`. `KnowledgeRecord` é o *ledger* local de
conhecimento validado (dedup por conteúdo + origem, proveniência e status do
Atlas) — NÃO é uma base vetorial: a dedup semântica permanece sob a alçada do
Atlas; aqui garantimos não-duplicação por hash/origem e rastreio.
"""

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.session import utcnow


class ResearchRun(Base):
    """Uma execução de Web Research (Fase 14)."""

    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    queries_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str] = mapped_column(Text, default="")
    facts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    inferences_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertainties_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fallback_used: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def queries(self) -> list[str]:
        return _load_list(self.queries_json)

    @property
    def facts(self) -> list[dict]:
        return _load_list(self.facts_json)

    @property
    def inferences(self) -> list[dict]:
        return _load_list(self.inferences_json)

    @property
    def uncertainties(self) -> list[dict]:
        return _load_list(self.uncertainties_json)

    @property
    def citations(self) -> list[dict]:
        return _load_list(self.citations_json)


class KnowledgeRecord(Base):
    """Ledger local de conhecimento validado (Fase 14).

    Unicidade por `content_hash` dentro da mesma origem evita duplicatas; o
    `status` documenta o ciclo (suggested → validated → persisted | rejected;
    conflict quando duas fontes divergem). Nenhum dado sensível vive aqui —
    os textos são truncados/sanitizados antes da persistência.
    """

    __tablename__ = "knowledge_records"
    __table_args__ = (
        UniqueConstraint("content_hash", "source_url", name="uq_knowledge_content_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    research_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_runs.id"), nullable=True, index=True
    )
    claim: Mapped[str] = mapped_column(Text)
    facts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="unverified")
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    project: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="suggested", index=True)
    atlas_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    atlas_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    @property
    def facts(self) -> list[dict]:
        return _load_list(self.facts_json)


def _load_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []