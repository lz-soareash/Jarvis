"""Fase 14 — API de Web Research & Knowledge.

`POST /api/research` executa a pesquisa em streaming SSE (reutilizando o
contrato existente `data: {json}\\n\\n`). Consulta get-by-id e fontes vêm do
ledger persistido (`ResearchRun`); `promote` aplica a política de escrita do
Atlas (`AtlasWriteMode`, default `suggest` = não escreve sem confirmação).
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider
from app.db.session import get_db
from app.models import ResearchRun
from app.schemas.research import (
    KnowledgeCandidateOut,
    KnowledgePromoteIn,
    ResearchCitationOut,
    ResearchCreate,
    ResearchRunOut,
    ResearchSourceOut,
)
from app.services import chat as chat_service
from app.services.research_service import run_web_research

from .deps import get_ai_provider

router = APIRouter(prefix="/api/research", tags=["research"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _sources_from_citations(citations: list[dict]) -> list[ResearchSourceOut]:
    seen: set[str] = set()
    sources: list[ResearchSourceOut] = []
    for c in citations:
        url = c.get("source_url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(
            ResearchSourceOut(
                source_url=url,
                title=c.get("title") or "",
                excerpt=(c.get("excerpt") or "")[:300],
                source_provider="",
                content_hash="",
                confidence="",
                retrieved_at=None,
            )
        )
    return sources


def _to_out(run: ResearchRun) -> ResearchRunOut:
    citations = run.citations
    return ResearchRunOut(
        id=run.id,
        session_id=run.session_id,
        objective=run.objective,
        status=run.status,
        queries=run.queries,
        answer=run.answer,
        facts=run.facts,
        inferences=run.inferences,
        uncertainties=run.uncertainties,
        citations=[ResearchCitationOut(**c) for c in citations if isinstance(c, dict) and c.get("source_url")],
        sources=_sources_from_citations(citations),
        provider=run.provider,
        model=run.model,
        fallback_used=run.fallback_used,
        error=run.error,
        duration_ms=run.duration_ms,
        created_at=run.created_at,
    )


@router.post("", response_class=StreamingResponse)
async def create_research(
    session_id: str,
    body: ResearchCreate,
    db: OrmSession = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
):
    """Executa uma pesquisa web (coleta local + síntese com citações) em SSE."""
    if chat_service.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if not (body.objective or "").strip():
        raise HTTPException(status_code=422, detail="objective é obrigatório")

    async def generator():
        from app.services.chat import sse_event as sse

        yield sse({"type": "research.start", "objective": body.objective, "session_id": session_id})
        events: list[str] = []

        async def sink(payload: dict) -> None:
            events.append(sse(payload))

        outcome = await run_web_research(
            db,
            session_id,
            provider,
            body.objective,
            sink=sink,
            max_queries=body.max_queries,
            max_pages=body.max_pages,
        )
        for ev in events:
            yield ev
        final = {
            "type": "research.result",
            "run_id": outcome.run_id,
            "status": outcome.status,
            "answer": outcome.answer,
            "facts": outcome.facts,
            "inferences": outcome.inferences,
            "uncertainties": outcome.uncertainties,
            "citations": outcome.citations,
            "provider": outcome.provider,
            "model": outcome.model,
            "fallback_used": outcome.fallback_used,
            "prompt_injection_suspected": outcome.prompt_injection_suspected,
            "duration_ms": outcome.duration_ms,
        }
        if outcome.error:
            final["error"] = outcome.error
        yield sse(final)
        yield sse({"type": "done"})

    return StreamingResponse(generator(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/{run_id}", response_model=ResearchRunOut)
def get_research(run_id: str, db: OrmSession = Depends(get_db)) -> ResearchRunOut:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    return _to_out(run)


@router.get("/{run_id}/sources", response_model=list[ResearchSourceOut])
def get_research_sources(run_id: str, db: OrmSession = Depends(get_db)) -> list[ResearchSourceOut]:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    return _sources_from_citations(run.citations)


@router.post("/{run_id}/knowledge/promote", response_model=list[KnowledgeCandidateOut])
def promote_knowledge(
    run_id: str,
    body: KnowledgePromoteIn,
    db: OrmSession = Depends(get_db),
) -> list[KnowledgeCandidateOut]:
    """Confirma candidatos (política AtlasWriteMode). default `suggest` não escreve."""
    from app.research.knowledge import KnowledgeStore
    from app.services import ops as ops_service

    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    if not body.candidate_ids:
        raise HTTPException(status_code=422, detail="candidate_ids é obrigatório")

    from app.services.atlas_client import get_atlas_client

    atlas = get_atlas_client() if _atlas_usable_for_write() else None
    store = KnowledgeStore(db)
    results = store.promote(body.candidate_ids, atlas=atlas, project=body.project or "atlas")
    synced = sum(1 for r in results if r.get("atlas_status") == "synced")
    errored = sum(1 for r in results if r.get("atlas_status") == "error")
    ops_service.record_event(
        db,
        session_id=run.session_id,
        event_type=ops_service.EVENT_KNOWLEDGE_ATLAS,
        status="synced" if synced else ("error" if errored else "suggested"),
        meta=ops_service.json_safe_meta(
            run_id=run_id,
            mode=store.mode.value if hasattr(store.mode, "value") else str(store.mode),
            candidates=len(results),
            synced=synced,
            errors=errored,
        ),
    )
    records = [r for r in store.list_records(limit=200) if r.id in body.candidate_ids]
    return [_candidate_out(r) for r in records]


def _candidate_out(record) -> KnowledgeCandidateOut:
    return KnowledgeCandidateOut(
        id=record.id,
        claim=record.claim,
        confidence=record.confidence,
        source_url=record.source_url,
        source_provider=record.source_provider,
        content_hash=record.content_hash,
        project=record.project,
        status=record.status,
        created_at=record.created_at,
    )


def _atlas_usable_for_write() -> bool:
    from app.core.config import settings

    return bool(settings.atlas_enabled and settings.atlas_email and settings.atlas_password)