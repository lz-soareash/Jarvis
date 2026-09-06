"""Orquestração de Web Research com observabilidade (Fase 14).

Wrapper único usado pela API (`/api/research`) e pelo Agentic Core (passo
`kind="research"`): constrói o Research Agent com Search Provider + Fetcher
(SSRF) + Router de IA, conecta o `event_sink` ao pipeline de eventos
operacionais (`ops.record_event`) e à auditoria (`audit.log_action`) — tudo
sanitizado, sem secrets nem conteúdo sensível.
"""

import logging

from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider
from app.research.agent import ResearchAgent
from app.research.fetcher import WebFetcher
from app.services import audit as audit_service
from app.services import ops as ops_service
from app.websearch.registry import get_search_registry

logger = logging.getLogger("jarvis.research_service")


async def run_web_research(
    db: OrmSession,
    session_id: str | None,
    provider: AIProvider,
    objective: str,
    *,
    sink=None,
    atlas=None,
    search_provider=None,
    fetcher=None,
    max_queries: int | None = None,
    max_pages: int | None = None,
) -> "ResearchOutcome":
    """Executa a pesquisa emitindo eventos SSE *e* ops/audit (sanitizados).

    `search_provider`/`fetcher` permitem injeção em testes (mocks/fakes) —
    produção usa o provider do registry e o Fetcher SSRF padrão.
    """
    from app.ai.registry import get_ai_router
    from app.research.agent import ResearchOutcome

    agent = ResearchAgent(
        search_provider=search_provider or get_search_registry().get(),
        fetcher=fetcher if fetcher is not None else WebFetcher(),
        ai_provider=provider,
        ai_router=get_ai_router(),
        db=db,
        atlas=atlas,
        session_id=session_id,
        event_sink=_make_sink(db, session_id, sink),
        max_queries=max_queries,
        max_pages=max_pages,
    )
    outcome = await agent.run(objective)
    _record_knowledge_events(db, session_id, outcome)
    return outcome


def _make_sink(db: OrmSession, session_id: str | None, user_sink):
    async def sink(payload: dict):
        _record_research_ops(db, session_id, payload)
        if user_sink is not None:
            await user_sink(payload)

    return sink


def _record_research_ops(db: OrmSession, session_id: str | None, payload: dict) -> None:
    event_type = payload.get("type") or ""
    if event_type == "research.started":
        ops_service.record_event(
            db,
            session_id=session_id,
            event_type=ops_service.EVENT_RESEARCH_STARTED,
            status="started",
            meta=ops_service.json_safe_meta(objective=str(payload.get("objective") or "")[:300]),
        )
        audit_service.log_action(
            db,
            action="research.started",
            session_id=session_id,
            allowed=True,
            detail=f"objetivo: {(payload.get('objective') or '')[:200]}",
        )
    elif event_type == "research.failed":
        ops_service.record_event(
            db,
            session_id=session_id,
            event_type=ops_service.EVENT_RESEARCH_FAILED,
            status="failed",
            meta=ops_service.json_safe_meta(error=str(payload.get("error") or "")[:300]),
        )
        audit_service.log_action(
            db,
            action="research.failed",
            session_id=session_id,
            allowed=False,
            detail=str(payload.get("error") or "")[:300],
        )
    elif event_type == "research.completed":
        ops_service.record_event(
            db,
            session_id=session_id,
            event_type=ops_service.EVENT_RESEARCH_COMPLETED,
            status=str(payload.get("status") or "completed"),
            latency_ms=payload.get("duration_ms"),
            meta=ops_service.json_safe_meta(run_id=payload.get("run_id")),
        )
    elif event_type == "research.synthesized":
        ops_service.record_event(
            db,
            session_id=session_id,
            event_type=ops_service.EVENT_RESEARCH_COMPLETED,
            provider=payload.get("provider"),
            status=payload.get("fallback_used") and "fallback" or "primary",
            meta=ops_service.json_safe_meta(facts=payload.get("facts"), citations=payload.get("citations")),
        )
    # demais (collected/search/page) ficam apenas nos logs de debug p/ não poluir.


def _record_knowledge_events(db: OrmSession, session_id: str | None, outcome: "ResearchOutcome") -> None:
    recorded = getattr(outcome, "knowledge_records", 0) or 0
    conflicts = getattr(outcome, "knowledge_conflicts", 0) or 0
    dups = getattr(outcome, "knowledge_duplicates", 0) or 0
    if recorded or dups or conflicts:
        ops_service.record_event(
            db,
            session_id=session_id,
            event_type=ops_service.EVENT_KNOWLEDGE_RECORDED,
            status="recorded",
            meta=ops_service.json_safe_meta(
                run_id=outcome.run_id, recorded=recorded, duplicates=dups, conflicts=conflicts
            ),
        )
    if conflicts:
        ops_service.record_event(
            db,
            session_id=session_id,
            event_type=ops_service.EVENT_KNOWLEDGE_CONFLICT,
            status="conflict",
            meta=ops_service.json_safe_meta(run_id=outcome.run_id, conflicts=conflicts),
        )
        audit_service.log_action(
            db,
            action="knowledge.conflict",
            session_id=session_id,
            allowed=False,
            detail=f"run {outcome.run_id}: {conflicts} conflito(s) sem sobrescrita silenciosa",
        )