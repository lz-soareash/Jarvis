"""Testes da Fase 12.2 — API remota: gasto 503 quando desabilitada e fluxos completos."""

import pytest

from app.core.config import settings


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "remote_enabled", True)


# ---------------------------------------------------------------------------
# Desabilitada (padrão): 503 em tudo, exceto status
# ---------------------------------------------------------------------------

def test_status_when_disabled(client):
    data = client.get("/api/remote/status").json()
    assert data["enabled"] is False
    assert data["configured"] is False


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/remote/pairings", None),
        ("get", "/api/remote/pairings", None),
        ("post", "/api/remote/pairings/validate", {"code": "0000000000", "device_name": "X"}),
        ("get", "/api/remote/devices", None),
        ("get", "/api/remote/credentials", None),
        ("get", "/api/remote/sessions", None),
        ("post", "/api/remote/auth", {"token": "abc"}),
    ],
)
def test_remote_endpoints_503_when_disabled(client, method, path, body):
    if body is not None:
        res = getattr(client, method)(path, json=body)
    else:
        res = getattr(client, method)(path)
    assert res.status_code == 503


# ---------------------------------------------------------------------------
# Habilitada: fluxos end-to-end via API
# ---------------------------------------------------------------------------

def test_pairing_flow_via_api(client, monkeypatch):
    _enable(monkeypatch)

    created = client.post("/api/remote/pairings")
    assert created.status_code == 201, created.text
    payload = created.json()
    code = payload["code"]
    assert code.isdigit() and len(code) == settings.remote_pairing_code_length
    assert "pairing_id" in payload and "ttl_seconds" in payload

    validated = client.post(
        "/api/remote/pairings/validate",
        json={"code": code, "device_name": "Meu Phone", "device_type": "mobile"},
    )
    assert validated.status_code == 201, validated.text
    result = validated.json()
    assert result["device"]["status"] == "active"
    assert result["token"] and len(result["token"]) >= 40

    # Código single-use: segunda validação falha.
    again = client.post(
        "/api/remote/pairings/validate",
        json={"code": code, "device_name": "Outro"},
    )
    assert again.status_code == 400


def test_crud_endpoints_via_api(client, monkeypatch, fake_ai):
    _enable(monkeypatch)

    created = client.post("/api/remote/pairings").json()
    validated = client.post(
        "/api/remote/pairings/validate",
        json={"code": created["code"], "device_name": "Desk", "device_type": "desktop"},
    ).json()
    device_id = validated["device_id"]

    devices = client.get("/api/remote/devices").json()
    assert any(d["id"] == device_id for d in devices)

    creds = client.get(f"/api/remote/credentials?device_id={device_id}").json()
    assert len(creds) == 1 and creds[0]["active"] is True

    # Auth com o token → sessão.
    auth = client.post("/api/remote/auth", json={"token": validated["token"]}).json()
    assert auth["session_id"]
    sessions = client.get("/api/remote/sessions?device_id=" + device_id).json()
    assert any(s["id"] == auth["session_id"] for s in sessions)

    # Revogar a credencial invalida o token.
    revoked = client.post(f"/api/remote/credentials/{creds[0]['id']}/revoke")
    assert revoked.status_code == 200
    auth2 = client.post("/api/remote/auth", json={"token": validated["token"]})
    assert auth2.status_code == 401


def _pair(client):
    created = client.post("/api/remote/pairings").json()
    validated = client.post(
        "/api/remote/pairings/validate",
        json={"code": created["code"], "device_name": "Desk"},
    ).json()
    return validated


def test_revoke_device_via_api_revokes_later_auth(client, monkeypatch, fake_ai):
    _enable(monkeypatch)

    validated = _pair(client)
    res = client.post(f"/api/remote/devices/{validated['device_id']}/revoke")
    assert res.status_code == 200
    assert client.post(
        "/api/remote/auth", json={"token": validated["token"]}
    ).status_code == 401


def test_status_endpoint_hides_secrets(client, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "remote_device_id", "root-pc")
    monkeypatch.setattr(settings, "remote_device_token", "super-secreto-token")
    data = client.get("/api/remote/status").json()
    assert "super-secreto-token" not in str(data)
    assert "token" not in data


def test_spoofed_device_id_rejected_via_api(client, monkeypatch, fake_ai):
    """Anti-spoofing na camada HTTP: claim de device_id divergente é negado."""
    _enable(monkeypatch)

    desk = _pair(client)
    other = _pair(client)
    assert desk["device_id"] != other["device_id"]

    # Token legítimo do "Desk" alegando ser outro device → 401.
    res = client.post(
        "/api/remote/auth",
        json={"token": desk["token"], "claimed_device_id": other["device_id"]},
    )
    assert res.status_code == 401


def test_list_pairings_exposes_no_code(client, monkeypatch):
    _enable(monkeypatch)
    created = client.post("/api/remote/pairings").json()
    rows = client.get("/api/remote/pairings").json()
    assert any(r["id"] == created["pairing_id"] for r in rows)
    raw = str(rows)
    assert created["code"] not in raw
    assert "sha256:" not in raw