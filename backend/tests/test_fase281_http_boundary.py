"""Fase 28.1 — fronteira HTTP endurecida (matriz de segurança).

Cobre o fecho dos gaps da auditoria da fase:

- `/api/remote/operations*` — endpoint STRICT: sem Bearer → 401; token inválido
  → 401; token de OUTRO device não revela nem opera (list/get/cancel → 404);
  sessão explicitamente de outro device → 403.
- `/api/approvals/{id}/respond` — aprovação ancorada ao JARVIS de um device só
  pode ser decidida pelo PRÓPRIO device (token de outro → 403; sem token → 401).
- `/api/sessions*` + mensagens — com identidade, o acesso fica escopado à âncora
  do próprio device (sessões alheias → 404); sem token, o fluxo local é intacto.
- `Cache-Control: no-store` nos prefixos sensíveis.
- Guard de payload: teto físico também para corpo CHUNKED (sem Content-Length)
  → 413; no limite tolerável → passa íntegro.
- TTL de continuidade: `DeviceContext` velho não emite claim de continuidade.

Hermético: ENV=test, REMOTE_ENABLED=false, sem rede, controller fake.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from app.computer import controller as computer_module
from app.models.session import utcnow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pair(db, name):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name=name)
    return device.id, token


def _anchor(db, device_id):
    from app.models.remote import Device
    from app.remote.jarvis_session import get_or_create_jarvis_session

    return get_or_create_jarvis_session(db, db.get(Device, device_id))


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class FakePcController:
    def __init__(self):
        self.opened_apps: list[str] = []
        self.closed_apps: list[str] = []

    def open_app(self, target):
        self.opened_apps.append(target)
        return f"App iniciado: {target}"

    def close_app(self, process_name):
        self.closed_apps.append(process_name)
        return f"App fechado: {process_name}"

    def system_stats(self):
        return {"cpu_percent": 1.0}

    def system_status(self):
        return {"cpu_percent": 1.0, "hostname": "teste"}


def _l2_op(operation="fechar chrome"):
    return {
        "operation": operation,
        "steps": [{"action": "CLOSE_APP", "target": "pc", "params": {"process_name": "chrome"}}],
    }


# ---------------------------------------------------------------------------
# 1. /api/remote/operations* — strict + ownership
# ---------------------------------------------------------------------------


def test_operations_invalid_token_401(client, monkeypatch):
    controller = FakePcController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)
    res = client.post(
        "/api/remote/operations",
        json={"steps": [{"action": "OPEN_APP", "target": "pc", "params": {"target": "chrome"}}]},
        headers=_auth("token-que-nao-existe"),
    )
    assert res.status_code == 401
    assert controller.opened_apps == []


def test_operations_other_device_hidden(client, monkeypatch):
    from app.db.session import SessionLocal

    controller = FakePcController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)

    db = SessionLocal()
    try:
        dev_a, token_a = _pair(db, "DevA")
        _dev_b, token_b = _pair(db, "DevB")
        anchor_a = _anchor(db, dev_a)
    finally:
        db.close()

    headers_a = _auth(token_a)
    headers_b = _auth(token_b)

    created = client.post(
        "/api/remote/operations",
        json={"steps": [{"action": "OPEN_APP", "target": "pc", "params": {"target": "chrome"}}]},
        headers=headers_a,
    ).json()
    op_id = created["operation"]["id"]

    # Outro device não vê a operação (404, sem revelar existência).
    assert client.get("/api/remote/operations", headers=headers_b).json() == []
    assert client.get(f"/api/remote/operations/{op_id}", headers=headers_b).status_code == 404
    assert (
        client.post(f"/api/remote/operations/{op_id}/cancel", headers=headers_b).status_code == 404
    )

    # Sessão explicitamente de outro device → 403.
    res = client.post(
        "/api/remote/operations",
        json={**_l2_op(), "session_id": anchor_a},
        headers=headers_b,
    )
    assert res.status_code == 403


def test_operations_stale_after_revoke(client, monkeypatch):
    from app.db.session import SessionLocal
    from app.remote import devices as devices_svc

    controller = FakePcController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)

    db = SessionLocal()
    try:
        dev_a, token_a = _pair(db, "DevRev")
    finally:
        db.close()

    headers = _auth(token_a)
    created = client.post(
        "/api/remote/operations",
        json={"steps": [{"action": "OPEN_APP", "target": "pc", "params": {"target": "chrome"}}]},
        headers=headers,
    ).json()
    op_id = created["operation"]["id"]
    assert controller.opened_apps == ["chrome"]

    db = SessionLocal()
    try:
        devices_svc.revoke_device(db, dev_a)
    finally:
        db.close()

    # Token revogado → 401 em qualquer endpoint strict.
    assert client.get(f"/api/remote/operations/{op_id}", headers=headers).status_code == 401


# ---------------------------------------------------------------------------
# 2. Aprovações — vínculo de device
# ---------------------------------------------------------------------------


def test_approvals_other_device_403(client, monkeypatch):
    from app.db.session import SessionLocal

    controller = FakePcController()
    monkeypatch.setattr(computer_module, "get_system_controller", lambda: controller)

    db = SessionLocal()
    try:
        _dev_a, token_a = _pair(db, "DevA")
        _dev_b, token_b = _pair(db, "DevB")
    finally:
        db.close()

    created = client.post(
        "/api/remote/operations", json=_l2_op(), headers=_auth(token_a)
    ).json()
    assert created["requires_confirmation"] is True
    approval_id = created["approvals"][0]["id"]
    op_id = created["operation"]["id"]

    # Outro device NÃO decide (403), nada é executado, approval segue pendente.
    res = client.post(
        f"/api/approvals/{approval_id}/respond",
        json={"approved": True},
        headers=_auth(token_b),
    )
    assert res.status_code == 403
    assert controller.closed_apps == []

    from sqlalchemy import select

    from app.models import ApprovalRequest

    db = SessionLocal()
    try:
        anchor_sid = db.scalars(
            select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
        ).one().session_id
    finally:
        db.close()

    pending = client.get(f"/api/approvals/pending?session_id={anchor_sid}").json()
    assert any(a["arguments"].get("operation_id") == op_id for a in pending)
    assert all(a["status"] == "pending" for a in pending)

    # Sem token também não decide (401).
    res = client.post(
        f"/api/approvals/{approval_id}/respond", json={"approved": True}
    )
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# 3. Sessões/mensagens — escopo limitado à âncora do device
# ---------------------------------------------------------------------------


def test_sessions_scoped_to_own_anchor(client):
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        dev_a, token_a = _pair(db, "DevSess")
        dev_b, token_b = _pair(db, "DevSessB")
        anchor_a = _anchor(db, dev_a)
        _anchor_b = _anchor(db, dev_b)
    finally:
        db.close()

    headers_a = _auth(token_a)
    headers_b = _auth(token_b)

    # Própria âncora acessível; sessão alheia → 404 (sem revelar).
    assert client.get(f"/api/sessions/{anchor_a}", headers=headers_a).status_code == 200
    assert client.get(f"/api/sessions/{anchor_a}", headers=headers_b).status_code == 404
    assert client.get(f"/api/sessions/{anchor_a}/messages", headers=headers_b).status_code == 404
    assert (
        client.post(
            f"/api/sessions/{anchor_a}/messages",
            json={"content": "oi"},
            headers=headers_b,
        ).status_code == 404
    )

    # Lista com identidade → só a própria âncora.
    listed = client.get("/api/sessions", headers=headers_a).json()
    assert [s["id"] for s in listed] == [anchor_a]

    # Sem token → fluxo local preservado (200).
    assert client.get("/api/sessions").status_code == 200


# ---------------------------------------------------------------------------
# 4. Cache-Control: no-store nos prefixos sensíveis
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/sessions",
        "/api/remote/operations",
    ],
)
def test_no_store_headers_sensitive_prefixes(client, path):
    res = client.get(path, headers=_auth("token-invalido")) if path.startswith("/api/remote/") else client.get(path)
    assert "cache-control" in {k.lower() for k, _ in res.headers.items()}
    assert res.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------------------
# 5. Guard de payload — teto físico também para corpo chunked
# ---------------------------------------------------------------------------


def _make_receive(chunks: list[bytes]):
    index = {"n": 0}

    async def receive():
        if index["n"] >= len(chunks):
            return {"type": "http.disconnect"}
        msg = {
            "type": "http.request",
            "body": chunks[index["n"]],
            "more_body": index["n"] < len(chunks) - 1,
        }
        index["n"] += 1
        return msg

    return receive


def _asgi_scope(path="/api/remote/operations"):
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
        "http_version": "1.1",
    }


def test_payload_guard_chunked_over_limit_413():
    from app.core.config import settings
    from app.main import RemotePayloadGuardMiddleware

    inner_called = {"value": False}

    async def inner(scope, receive, send):
        inner_called["value"] = True

    outer = {}

    async def record(message):
        if message["type"] == "http.request":
            return
        if outer.get("started"):
            outer["body"] = outer.get("body", b"") + message.get("body", b"")
        else:
            outer.update(message)
            outer["started"] = True

    middleware = RemotePayloadGuardMiddleware(inner)
    payload = b"x" * (settings.remote_max_payload_bytes + 100)
    chunks = [payload[i : i + 5000] for i in range(0, len(payload), 5000)]

    async def run():
        await middleware(_asgi_scope(), _make_receive(chunks), record)

    asyncio.run(run())

    assert inner_called["value"] is False
    assert outer.get("status") == 413
    err = json.loads(outer["body"])
    assert err["type"] == "error"
    assert err["code"] == "PAYLOAD_TOO_LARGE"


def test_payload_guard_chunked_within_limit_passes():
    from app.main import RemotePayloadGuardMiddleware

    received = {"body": b""}

    async def inner(scope, receive, send):
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                break
            received["body"] += msg.get("body", b"")
            if not msg.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    outer = {}

    async def record(message):
        if message["type"] == "http.request":
            return
        if outer.get("started"):
            outer["body"] = outer.get("body", b"") + message.get("body", b"")
        else:
            outer.update(message)
            outer["started"] = True

    middleware = RemotePayloadGuardMiddleware(inner)
    body = b'{"operation":"t","steps":[]}'
    chunks = [body[i : i + 3] for i in range(0, len(body), 3)]

    async def run():
        await middleware(_asgi_scope(), _make_receive(chunks), record)

    asyncio.run(run())

    assert outer.get("status") == 200
    assert received["body"] == body  # corpo chegou íntegro ao downstream


# ---------------------------------------------------------------------------
# 6. TTL de continuidade — DeviceContext velho não emite claim
# ---------------------------------------------------------------------------


def _device_ctx(updated_at: str, status: str = "success"):
    from app.ai.turn_context import DeviceContext, TurnContext

    return TurnContext(
        device=DeviceContext(
            id="d1",
            name="Galaxy A15",
            capability="SCREEN",
            status=status,
            transport="wan",
            summary="URL aberta",
            updated_at=updated_at,
        )
    )


def test_context_fresh_emits_continuity():
    from app.ai.turn_context import render_context_blocks

    ctx = _device_ctx(utcnow().isoformat())
    blocks = " ".join(render_context_blocks(ctx))
    assert "[Continuidade de dispositivo]" in blocks
    assert "expirou" not in blocks


def test_context_stale_suppresses_continuity():
    from app.ai.turn_context import render_context_blocks

    stale = (utcnow() - timedelta(seconds=900)).isoformat()
    ctx = _device_ctx(stale)
    blocks = " ".join(render_context_blocks(ctx))
    assert "[Continuidade de dispositivo]" not in blocks
    assert "expirou" in blocks
    assert "não assuma continuidade automática" in blocks