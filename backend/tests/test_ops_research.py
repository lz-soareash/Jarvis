"""Testes da Central de Operações com o bloco de Research (Fase 14)."""

import pytest

from app.research.evidence import Evidence


@pytest.fixture
def db_fx():
    from app.db.session import SessionLocal

    session = SessionLocal()
    yield session
    session.close()
from app.research.knowledge import KnowledgeCandidate, KnowledgeStore
from app.services import ops as ops_service


def test_overview_exposes_research_block(db_fx):
    overview = ops_service.build_overview(db_fx)
    assert "research" in overview.model_dump()
    research = overview.research
    assert "enabled" in research
    assert "runs_total" in research
    assert "records_total" in research


def test_research_stats_count_records(db_fx):
    store = KnowledgeStore(db_fx)
    cand = KnowledgeCandidate(claim="Fato para a central.", sources=[Evidence(source_url="https://exemplo.com/x")])
    store.record_candidates([cand])
    overview = ops_service.build_overview(db_fx)
    assert overview.research["records_total"] == 1
    assert overview.research["knowledge_by_status"].get("validated") == 1


def test_knowledge_op_event_recorded(db_fx):
    from app.models import KnowledgeRecord
    from app.research.evidence import Evidence

    store = KnowledgeStore(db_fx)
    cand = KnowledgeCandidate(claim="Fato com evento.", sources=[Evidence(source_url="https://exemplo.com/y")])
    store.record_candidates([cand], research_id=None)
    # O evento knowledge.recorded é gerado pelo research_service; aqui validamos o vocabulário.
    ops_service.record_event(
        db_fx,
        event_type=ops_service.EVENT_KNOWLEDGE_RECORDED,
        status="recorded",
        meta={"records": 1},
    )
    events = ops_service.list_events(db_fx, event_type=ops_service.EVENT_KNOWLEDGE_RECORDED)
    assert len(events) == 1
    assert KnowledgeRecord is not None