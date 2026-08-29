def test_root_returns_app_metadata(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "JARVIS"
    assert data["status"] == "online"
    assert "version" in data


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
    # Sem GEMINI_API_KEY no ambiente de teste, o provider reporta "unconfigured"
    # sem realizar nenhuma chamada à API real.
    response = client.get("/health/ai")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unconfigured"
    assert data["provider"] == "gemini"