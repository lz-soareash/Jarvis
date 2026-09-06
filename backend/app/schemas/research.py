"""Schemas da Fase 14 — Web Research & Knowledge Integration."""

from datetime import datetime

from app.schemas.base import APIModel


class ResearchCreate(APIModel):
    """Início de uma pesquisa web (objetivo + limites opcionais)."""

    objective: str
    max_queries: int | None = None
    max_pages: int | None = None


class ResearchSourceOut(APIModel):
    """Uma fonte coletada (só URLs reais, nunca inventadas)."""

    source_url: str
    title: str = ""
    excerpt: str = ""
    source_provider: str = ""
    content_hash: str = ""
    confidence: str = "unverified"
    retrieved_at: str | None = None


class ResearchCitationOut(APIModel):
    """Citação da resposta (fonte real + trecho)."""

    source_url: str
    title: str = ""
    excerpt: str = ""
    kind: str = "fact"  # fact | inference | uncertainty


class KnowledgeCandidateOut(APIModel):
    """Candidato a conhecimento (não enviado automaticamente ao Atlas)."""

    id: str
    claim: str
    confidence: str
    source_url: str | None = None
    source_provider: str | None = None
    content_hash: str = ""
    project: str | None = None
    status: str
    created_at: datetime | None = None


class ResearchRunOut(APIModel):
    """Resultado persistido de uma pesquisa."""

    id: str
    session_id: str | None = None
    objective: str
    status: str
    queries: list[str] = []
    answer: str = ""
    facts: list[dict] = []
    inferences: list[dict] = []
    uncertainties: list[dict] = []
    citations: list[ResearchCitationOut] = []
    sources: list[ResearchSourceOut] = []
    provider: str | None = None
    model: str | None = None
    fallback_used: str | None = None
    error: str | None = None
    duration_ms: int = 0
    created_at: datetime | None = None


class KnowledgePromoteIn(APIModel):
    """Confirmação de candidatos (modos suggest / user_confirmed)."""

    candidate_ids: list[str] = []
    project: str | None = None