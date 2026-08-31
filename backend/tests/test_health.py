def test_root_serves_frontend_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "JARVIS" in response.text


def test_serves_frontend_static_assets(client):
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/css/tokens.css").status_code == 200
    assert client.get("/js/app.js").status_code == 200
    assert client.get("/icons/favicon.svg").status_code == 200


def test_health_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == "JARVIS"
    assert data["version"]
    assert data["database"] == "ok"
    assert data["api_port"] == 8100
    assert data["ws_port"] == 8101
    assert data["env"] == "test"


def test_health_ai_unconfigured_without_key(client):
    # Sem GEMINI_API_KEY no ambiente de teste, o AI Router usa o fallback
    # determinístico (safety) — sem realizar nenhuma chamada à API real.
    response = client.get("/health/ai")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["provider"] == "deterministic"


def test_health_ai_is_cached(client):
    # O resultado é cacheado (30s) para não queimar quota a cada recarga de página.
    first = client.get("/health/ai")
    second = client.get("/health/ai")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()