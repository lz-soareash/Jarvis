import json


def create_session(client, title=None):
    body = {} if title is None else {"title": title}
    res = client.post("/api/sessions", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def parse_sse(raw: str) -> list[dict]:
    lines = [ln for ln in raw.strip().splitlines() if ln.startswith("data: ")]
    return [json.loads(ln[6:]) for ln in lines]


def test_create_session_with_title(client):
    data = create_session(client, title="Meu projeto")
    assert data["id"]
    assert data["title"] == "Meu projeto"
    assert data["message_count"] == 0


def test_create_session_default_title(client):
    data = create_session(client)
    assert data["title"] == "Nova sessão"


def test_list_sessions_returns_created(client):
    create_session(client, title="A")
    res = client.get("/api/sessions")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_get_unknown_session_404(client):
    res = client.get("/api/sessions/nao-existe")
    assert res.status_code == 404


def test_delete_session(client):
    data = create_session(client)
    res = client.delete(f"/api/sessions/{data['id']}")
    assert res.status_code == 204
    assert client.get(f"/api/sessions/{data['id']}").status_code == 404


def test_chat_non_stream_persists_and_responds(client, fake_ai):
    session = create_session(client)
    res = client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"content": "olá, quem é você?", "stream": False},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == session["id"]
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"] == "resposta fake"
    assert fake_ai.generate_calls == 1

    messages = client.get(f"/api/sessions/{session['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_chat_without_ai_key_503(client):
    # Sem override -> provider default com GEMINI_API_KEY vazia (conftest).
    session = create_session(client)
    res = client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"content": "oi", "stream": False},
    )
    assert res.status_code == 503


def test_chat_auto_title_from_first_message(client, fake_ai):
    session = create_session(client)
    client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"content": "preparar ambiente", "stream": False},
    )
    data = client.get(f"/api/sessions/{session['id']}").json()
    assert data["title"] == "preparar ambiente"
    assert data["message_count"] == 2


def test_chat_stream_sse(client, fake_ai):
    session = create_session(client)
    with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/messages",
        json={"content": "conte uma história", "stream": True},
    ) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        raw = b"".join(res.iter_bytes()).decode()

    events = parse_sse(raw)
    types = [e["type"] for e in events]
    assert types == ["start", "chunk", "chunk", "done"]

    chunks = "".join(e["text"] for e in events if e["type"] == "chunk")
    assert chunks == "resposta fake"
    assert events[-1]["message"]["role"] == "assistant"
    assert events[-1]["message"]["content"] == "resposta fake"

    messages = client.get(f"/api/sessions/{session['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == "resposta fake"


def test_send_message_unknown_session_404(client, fake_ai):
    res = client.post("/api/sessions/xyz/messages", json={"content": "oi"})
    assert res.status_code == 404