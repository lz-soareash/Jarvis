"""Testes da Fase 2 — memória de longo prazo (CRUD + busca semântica/lexical)."""


def create_session(client):
    res = client.post("/api/sessions", json={})
    assert res.status_code == 201
    return res.json()


def create_memory(client, content, kind="fact", session_id=None):
    body = {"content": content, "kind": kind}
    if session_id:
        body["session_id"] = session_id
    res = client.post("/api/memories", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_create_get_list_delete(client, fake_ai):
    data = create_memory(client, "gosto de café", kind="preference")
    assert data["id"]
    assert data["kind"] == "preference"
    assert data["session_id"] is None
    assert fake_ai.embed_calls == 1

    got = client.get(f"/api/memories/{data['id']}")
    assert got.status_code == 200
    assert got.json()["content"] == "gosto de café"

    listed = client.get("/api/memories").json()
    assert any(m["id"] == data["id"] for m in listed)

    assert client.delete(f"/api/memories/{data['id']}").status_code == 204
    assert client.get(f"/api/memories/{data['id']}").status_code == 404


def test_create_memory_unknown_session_404(client, fake_ai):
    res = client.post("/api/memories", json={"content": "x", "session_id": "nao-existe"})
    assert res.status_code == 404


def test_memory_kind_filter(client, fake_ai):
    create_memory(client, "fato A", kind="fact")
    create_memory(client, "pref B", kind="preference")
    res = client.get("/api/memories", params={"kind": "preference"}).json()
    assert len(res) == 1
    assert res[0]["kind"] == "preference"


def test_semantic_search_finds_relevant_and_empty_for_unrelated(client, fake_ai):
    create_memory(client, "gosto de café", kind="preference")
    create_memory(client, "odeio música", kind="preference")

    found = client.get("/api/memories", params={"query": "café"}).json()
    assert len(found) == 1
    assert "café" in found[0]["content"]
    assert found[0]["score"] is not None and found[0]["score"] > 0

    empty = client.get("/api/memories", params={"query": "python"}).json()
    assert empty == []


def test_lexical_fallback_without_provider_key(client):
    # Sem override -> provider default não configurado -> busca lexical local.
    create_memory(client, "gosto de café", kind="preference")
    found = client.get("/api/memories", params={"query": "café"}).json()
    assert len(found) == 1
    assert "café" in found[0]["content"]
    assert found[0]["score"] is not None


def test_list_scoped_to_session(client, fake_ai):
    sid = create_session(client)["id"]
    create_memory(client, "global", session_id=None)
    create_memory(client, "da sessão", session_id=sid)

    scoped = client.get("/api/memories", params={"session_id": sid}).json()
    assert len(scoped) == 1
    assert scoped[0]["content"] == "da sessão"

    all_m = client.get("/api/memories").json()
    all_contents = {m["content"] for m in all_m}
    assert {"global", "da sessão"} <= all_contents


def test_chat_injects_relevant_memories_in_system_prompt(client, fake_ai):
    create_memory(client, "gosto de café", kind="preference")
    sid = create_session(client)["id"]
    res = client.post(
        f"/api/sessions/{sid}/messages",
        json={"content": "o que você sabe sobre café?", "stream": False},
    )
    assert res.status_code == 200
    system = fake_ai.last_generate_kwargs["system"] or ""
    assert "[Memórias relevantes]" in system
    assert "gosto de café" in system