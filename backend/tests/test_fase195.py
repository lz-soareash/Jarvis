"""Fase 19.5 — VEGA Identity, Memory & Experience (testes herméticos).

Cobre: MEMORY (sanitização/classificação/metadata/TTL), ATLAS opcional (VEGA
funciona sem Atlas, indisponibilidade cai p/ fallback local), PERSONALITY
(identidade não altera permissões), STATE (presença real + transições), PRIVACY
(nunca secrets em storage/contexto) e contexto compartilhado.
Tudo hermético: banco in-memory, ATLAS_ENABLED=false, sem rede.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.enums import AtlasWriteMode, KnowledgeConfidence
from app.core.config import settings
from app.research.evidence import Evidence
from app.research.knowledge import KnowledgeCandidate, KnowledgeStore


@pytest.fixture(autouse=True)
def _reset_presence_cache():
    from app.services import presence

    presence._transitions_cached["state"] = None
    presence._transitions_cached["recorded_at"] = 0.0
    yield


def _evidence(url: str) -> Evidence:
    return Evidence(source_url=url, title="Título", excerpt="trecho")


def _mk_session(db):
    from app.services.chat import create_session

    return create_session(db)


def _mk_agent_task(db, session_id, status="running"):
    from app.models import AgentTask

    task = AgentTask(session_id=session_id, objective="tarefa", status=status)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _mk_pending_approval(db, session_id):
    from app.models import ApprovalRequest

    req = ApprovalRequest(
        session_id=session_id,
        tool_name="store_memory",
        permission_level=2,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


def _record_event(db, event_type, status=None):
    from app.services import ops

    ops.record_event(db, event_type=event_type, status=status)


def _store_memory(db, content, *, kind=None, project=None, expires_at=None):
    from app.services import memory as memory_service

    return asyncio.run(
        memory_service.create_memory(
            db, content=content, kind=kind, project=project, expires_at=expires_at
        )
    )


# ---------------------------------------------------------------------------
# MEMORY — write policy, metadata, TTL, classificação
# ---------------------------------------------------------------------------

class TestMemoryWritePolicy:
    def test_create_memory_sanitizes_secrets_via_api(self, client, fake_ai):
        res = client.post(
            "/api/memories",
            json={
                "content": "api_key=abc123def, senha=supersecret1 e password=outrasecret2",
                "kind": "fact",
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert "abc123def" not in body["content"]
        assert "supersecret1" not in body["content"]
        assert "outrasecret2" not in body["content"]
        assert "[REDACTED]" in body["content"]

    def test_classification_deterministic(self):
        from app.services import memory as memory_service

        assert memory_service.infer_memory_kind("Prefiro café preto.").value == "preference"
        assert memory_service.infer_memory_kind("Decidimos usar FastAPI.").value == "decision"
        assert memory_service.infer_memory_kind("Estamos no projeto Jarvis.").value == "project"
        assert memory_service.infer_memory_kind("A temperatura é 22 graus.").value == "fact"

    def test_kind_none_infers_preference(self, db_session):
        from app.services import memory as memory_service

        m = _store_memory(db_session, "Prefiro café")
        assert m.kind == "preference"

    def test_metadata_persists_and_filters_project(self, client, fake_ai):
        res = client.post(
            "/api/memories",
            json={
                "content": "stack do projeto backend",
                "kind": "project",
                "project": "jarvis",
                "confidence": "user_confirmed",
                "source": "chat",
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        )
        assert res.status_code == 201
        item = res.json()
        assert item["project"] == "jarvis"
        assert item["confidence"] == "user_confirmed"
        assert item["source"] == "chat"
        assert item["expires_at"] is not None

        listed = client.get("/api/memories", params={"project": "jarvis"}).json()
        assert len(listed) == 1
        assert listed[0]["project"] == "jarvis"

    def test_expired_memory_excluded_from_list_and_search(self, db_session):
        from app.services import memory as memory_service

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        m = _store_memory(db_session, "gosto de café", kind="preference", expires_at=past)
        assert memory_service.get_memory(db_session, m.id) is not None
        assert memory_service.list_memories(db_session) == []
        assert memory_service.list_memories(db_session, include_expired=True) != []
        hits = asyncio.run(
            memory_service.search_memories(db_session, query="café", include_global=True)
        )
        assert hits == []

    def test_search_filters_project(self, db_session):
        from app.services import memory as memory_service

        _store_memory(db_session, "prefiro git", project="jarvis")
        _store_memory(db_session, "prefiro git", project="outro")
        hits = asyncio.run(
            memory_service.search_memories(
                db_session, query="git", include_global=True, project="jarvis"
            )
        )
        assert len(hits) == 1
        assert hits[0][0].project == "jarvis"


class TestMemoryKnowledge:
    def test_knowledge_validated_injected_in_system_prompt(self, db_session, fake_ai):
        from app.services.chat import build_system_prompt

        store = KnowledgeStore(db_session)
        cand = KnowledgeCandidate(
            claim="Paris é a capital da França.",
            facts=["capital europeia"],
            sources=[_evidence("https://a.com")],
        )
        (record,), _ = store.record_candidates([cand])
        store.promote([record.id])
        assert record.status == "confirmed"

        sid = _mk_session(db_session).id
        system = asyncio.run(
            build_system_prompt(db_session, sid, fake_ai, query="qual a capital?")
        )
        assert "[Conhecimento validado]" in system
        assert "Paris é a capital da França." in system

    def test_knowledge_suggested_never_enters_context(self, db_session, fake_ai):
        from app.services.chat import build_system_prompt

        store = KnowledgeStore(db_session, mode=AtlasWriteMode.SUGGEST)
        cand = KnowledgeCandidate(
            claim="Rumor não verificado sobre X.",
            confidence=KnowledgeConfidence.MODEL_INFERRED,
            sources=[_evidence("https://a.com")],
        )
        store.record_candidates([cand])
        sid = _mk_session(db_session).id
        system = asyncio.run(
            build_system_prompt(db_session, sid, fake_ai, query="rumor sobre X?")
        )
        assert "[Conhecimento validado]" not in system
        assert "Rumor não verificado" not in system

    def test_memory_secret_never_reaches_context(self, db_session, fake_ai):
        from app.services.chat import build_system_prompt

        _store_memory(db_session, "chave api_key=12345e7a e token=ghp_0123456789abcdef")
        sid = _mk_session(db_session).id
        system = asyncio.run(
            build_system_prompt(db_session, sid, fake_ai, query="chave do banco")
        )
        assert "12345e7a" not in system
        assert "ghp_0123456789abcdef" not in system
        assert "[REDACTED]" in system


# ---------------------------------------------------------------------------
# ATLAS opcional — VEGA funciona SEM Atlas
# ---------------------------------------------------------------------------

class TestAtlasOptional:
    def test_atlas_disabled_is_not_a_dependency(self):
        from app.services.atlas_router import route as atlas_route
        from app.services.atlas_router import should_route_to_atlas

        assert settings.atlas_enabled is False
        assert should_route_to_atlas() is False
        assert atlas_route(None, "sess", "oi") is None

    def test_chat_works_when_atlas_disabled(self, client, fake_ai):
        s = client.post("/api/sessions", json={})
        assert s.status_code == 201
        res = client.post(
            f"/api/sessions/{s.json()['id']}/messages",
            json={"content": "oi", "stream": False},
        )
        assert res.status_code == 200

    def test_knowledge_promote_local_only_without_atlas(self, db_session):
        store = KnowledgeStore(db_session, mode=AtlasWriteMode.SUGGEST)
        cand = KnowledgeCandidate(
            claim="Fato local.", sources=[_evidence("https://a.com")]
        )
        (record,), _ = store.record_candidates([cand])
        results = store.promote([record.id], atlas=None)
        assert results[0]["status"] == "confirmed"
        assert results[0]["atlas_status"] == "suggested"

    def test_atlas_unavailable_falls_back_to_local(self, client, monkeypatch):
        import httpx

        from app.ai.core import handle_message
        from app.db.session import SessionLocal
        from app.services.atlas_client import AtlasClient, reset_atlas_client, set_atlas_client
        from app.services.chat import create_session

        monkeypatch.setattr(settings, "atlas_enabled", True)
        monkeypatch.setattr(settings, "atlas_email", "a@b.com")
        monkeypatch.setattr(settings, "atlas_password", "segredo")

        transport = httpx.MockTransport(lambda request: httpx.Response(502, json={"detail": "down"}))
        set_atlas_client(AtlasClient(base_url="http://atlas.test", transport=transport))
        try:
            db = SessionLocal()
            try:
                s = create_session(db)
                turn = asyncio.run(handle_message(db, s.id, "resuma o projeto", tools=False, stream=False))
                assert turn.kind == "reply"
            finally:
                db.close()
        finally:
            reset_atlas_client()
            monkeypatch.setattr(settings, "atlas_enabled", False)


# ---------------------------------------------------------------------------
# IDENTITY / PERSONALITY — separada de permissões/segurança
# ---------------------------------------------------------------------------

class TestIdentityPersonality:
    def test_identity_block_contains_vega(self):
        from app.identity import identity_system_block

        assert "VEGA" in identity_system_block()

    def test_identity_api(self, client):
        res = client.get("/api/vega/identity")
        assert res.status_code == 200
        body = res.json()
        assert body["name"] == (settings.assistant_name or "VEGA")
        assert body["internal_name"] == "JARVIS"
        assert "tone" in body

    def test_personality_config_never_changes_security(self, client, monkeypatch):
        from app.services.chat import BASE_SYSTEM_PROMPT

        baseline_tools = BASE_SYSTEM_PROMPT
        monkeypatch.setattr(settings, "assistant_tone", "provocative")
        monkeypatch.setattr(settings, "assistant_verbosity", "verbose")
        monkeypatch.setattr(settings, "assistant_humor", "sarcastic")

        assert BASE_SYSTEM_PROMPT == baseline_tools
        res = client.get("/api/permissions")
        assert res.status_code == 200
        tools = {p["tool_name"]: p for p in res.json()}
        assert tools["store_memory"]["permission_level"] == 1

    def test_memory_works_without_provider(self, db_session):
        from app.services import memory as memory_service

        m = _store_memory(db_session, "gosto de café", kind="preference")
        hits = asyncio.run(
            memory_service.search_memories(db_session, query="café", provider=None)
        )
        assert hits[0][0].id == m.id


# ---------------------------------------------------------------------------
# STATE / PRESENÇA — derivada de sinais reais
# ---------------------------------------------------------------------------

class TestPresenceState:
    def test_state_endpoint_clean_idle(self, client):
        res = client.get("/api/vega/state")
        assert res.status_code == 200
        body = res.json()
        assert body["online"] is True
        assert body["state"] in ("idle", "thinking")
        assert body["label"]

    def test_thinking_after_chat_started(self, db_session):
        from app.services import presence

        assert presence.compute_presence(db_session)["state"] == "idle"
        _record_event(db_session, "chat.started")
        assert presence.compute_presence(db_session)["state"] == "thinking"

    def test_working_when_agent_task_active(self, db_session):
        from app.services import presence

        sid = _mk_session(db_session).id
        _mk_agent_task(db_session, sid, status="running")
        assert presence.compute_presence(db_session)["state"] == "working"

    def test_waiting_when_pending_approval(self, db_session):
        from app.services import presence

        sid = _mk_session(db_session).id
        _mk_pending_approval(db_session, sid)
        assert presence.compute_presence(db_session)["state"] == "waiting_confirmation"

    def test_error_after_recent_failure(self, db_session):
        from app.services import presence

        _record_event(db_session, "chat.failed", status="failed")
        assert presence.compute_presence(db_session)["state"] == "error"

    def test_transition_event_recorded_only_on_change(self, db_session):
        from app.services import ops
        from app.services import presence

        presence.compute_presence(db_session)
        presence.compute_presence(db_session)
        events = ops.list_events(db_session, event_type="vega.state.changed")
        assert len(events) == 1

    def test_state_labels_endpoint(self, client):
        res = client.get("/api/vega/state/labels")
        assert res.status_code == 200
        assert "idle" in res.json()["states"]
        assert res.json()["enabled"] is True


# ---------------------------------------------------------------------------
# PRIVACY — nada de secrets em eventos/auditoria
# ---------------------------------------------------------------------------

class TestPrivacy:
    def test_no_secret_in_events_or_audit_after_memory_write(self, client, fake_ai):
        from app.db.session import SessionLocal
        from app.models import AuditLog
        from app.services import ops

        res = client.post(
            "/api/memories",
            json={"content": "acesso token=abc123def456ghi api_key=zzz999", "kind": "fact"},
        )
        assert res.status_code == 201
        assert "abc123def456ghi" not in res.json()["content"]

        db = SessionLocal()
        try:
            joined_audit = " ".join((r.detail or "") for r in db.query(AuditLog).all())
            meta_joined = " ".join((e.meta_json or "") for e in ops.list_events(db, limit=50))
        finally:
            db.close()
        assert "abc123def456ghi" not in joined_audit
        assert "zzz999" not in meta_joined