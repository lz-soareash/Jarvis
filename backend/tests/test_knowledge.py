"""Testes de Knowledge (Fase 14) — candidatos, dedup, conflito, política do Atlas."""

import pytest

from app.core.enums import AtlasWriteMode, KnowledgeConfidence
from app.research.evidence import Evidence
from app.research.knowledge import (
    KnowledgeCandidate,
    KnowledgeStore,
    classify_confidence,
    sanitize_text,
)


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    yield session
    session.close()


def _evidence(url: str, excerpt: str = "trecho relevante") -> Evidence:
    return Evidence(source_url=url, title="Título", excerpt=excerpt)


def test_sanitize_masks_secrets():
    text = "token=abc123 password=segredo Authorization: Bearer xyz789 apikey=kkk"
    clean = sanitize_text(text)
    assert "abc123" not in clean
    assert "segredo" not in clean
    assert "xyz789" not in clean
    assert "kkk" not in clean
    assert "[REDACTED]" in clean


def test_candidate_validity_and_confidence():
    good = KnowledgeCandidate(claim="Paris é a capital.", facts=["facto"], sources=[_evidence("https://a.com")])
    assert good.is_valid()
    bad = KnowledgeCandidate(claim="", sources=[_evidence("https://a.com")])
    assert not bad.is_valid()
    unsourced = KnowledgeCandidate(claim="fato", sources=[])
    assert not unsourced.is_valid()
    assert good.confidence == KnowledgeConfidence.SOURCE_CONFIRMED


def test_classify_confidence_multi_source():
    low = classify_confidence([_evidence("https://a.com")])
    high = classify_confidence([_evidence("https://a.com"), _evidence("https://b.com")])
    inferred = classify_confidence([], model_inferred=True)
    assert low == KnowledgeConfidence.SOURCE_CONFIRMED
    assert high == KnowledgeConfidence.MULTI_SOURCE_CONFIRMED
    assert inferred == KnowledgeConfidence.MODEL_INFERRED


def test_dedupe_by_content_hash_on_ledger(db):
    store = KnowledgeStore(db)
    cand = KnowledgeCandidate(claim="O céu é azul.", facts=["f1"], sources=[_evidence("https://a.com")])
    first, dups = store.record_candidates([cand])
    second, dups2 = store.record_candidates([cand])
    assert len(first) == 1 and dups == []
    assert second == [] and len(dups2) == 1


def test_conflicts_detected_without_override(db):
    store = KnowledgeStore(db)
    original = KnowledgeCandidate(claim="R1 versão pública.", facts=["v1"], sources=[_evidence("https://a.com")])
    store.record_candidates([original])
    revision = KnowledgeCandidate(claim="R1 versão pública.", facts=["v2 revisada"], sources=[_evidence("https://b.com")])
    conflicts = store.find_conflicts([revision])
    assert len(conflicts) == 1
    # A revisão NÃO sobrescreve a original silenciosamente:
    records = store.list_records()
    assert len(records) >= 1 and records[0].claim == original.claim


def test_promote_in_suggest_mode_writes_nothing(db):
    store = KnowledgeStore(db, mode=AtlasWriteMode.SUGGEST)
    cand = KnowledgeCandidate(claim="Fato confirmado.", sources=[_evidence("https://a.com")])
    (record,), _ = store.record_candidates([cand])
    assert record.status == "validated"
    calls = []

    class FakeAtlas:
        def store_knowledge(self, **kwargs):
            calls.append(kwargs)
            return {"id": "atlas-1"}

    results = store.promote([record.id], atlas=FakeAtlas())
    assert results[0]["atlas_status"] == "suggested"
    assert calls == [], "modo suggest NUNCA escreve no Atlas"


def test_promote_in_auto_mode_writes_to_atlas(db):
    store = KnowledgeStore(db, mode=AtlasWriteMode.AUTO)
    cand = KnowledgeCandidate(claim="Fato auto.", sources=[_evidence("https://a.com")])
    (record,), _ = store.record_candidates([cand])
    calls = []

    class FakeAtlas:
        def store_knowledge(self, *, claim, facts, sources, confidence, project=None):
            calls.append((claim, sources))
            return {"id": "atlas-x"}

    results = store.promote([record.id], atlas=FakeAtlas())
    assert results[0]["atlas_status"] == "synced"
    assert results[0]["status"] == "confirmed"
    assert calls[0][0] == "Fato auto."
    assert calls[0][1] == ["https://a.com"]


def test_promote_atlas_failure_isolated(db):
    store = KnowledgeStore(db, mode=AtlasWriteMode.AUTO)
    cand = KnowledgeCandidate(claim="Fato falho.", sources=[_evidence("https://a.com")])
    (record,), _ = store.record_candidates([cand])

    class BrokenAtlas:
        def store_knowledge(self, **kwargs):
            raise RuntimeError("atlas fora do ar")

    results = store.promote([record.id], atlas=BrokenAtlas())
    # Falha do Atlas não derruba o fluxo: status relevante fica no ledger.
    assert results[0]["atlas_status"] == "error"
    assert "fora do ar" in (store.list_records()[0].atlas_error or "")


def test_candidates_sanitized_before_persist(db):
    cand = KnowledgeCandidate(claim="Config password=admin123 para o serviço.", sources=[_evidence("https://a.com")])
    (record,), _ = KnowledgeStore(db).record_candidates([cand])
    assert "admin123" not in record.claim
    assert "[REDACTED]" in record.claim