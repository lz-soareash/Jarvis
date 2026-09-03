"""Testes da Fase 12.4 — retomada de comandos remotos pós-aprovação.

Cobre: ligação ApprovalRequest ↔ RemoteCommand (approval_id), retomada transacional
(aprovado executa via pipeline; negado não executa), independência da conexão
WebSocket, entrega best-effort à Gateway, e consulta do resultado persistido.
"""

import json
from unittest import mock

import pytest
from sqlalchemy import select

from app.core.enums import ApprovalStatus, RemoteCommandStatus


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _paired_device(db, name="Resume"):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name=name)
    return device.id, token


async def _queue_l2_command(db, device_id, token, command_id="cmd-appr-1"):
    """Registra um comando L2 via executor (cria approval + PENDING_APPROVAL)."""
    from app.models.remote import Credential
    from app.remote.executor import execute_remote_command
    from app.remote.jarvis_session import get_or_create_jarvis_session

    device = db.get(__import__("app.models.remote", fromlist=["Device"]).Device, device_id)
    cred = db.scalars(select(Credential).where(Credential.device_id == device_id)).first()
    sid = get_or_create_jarvis_session(db, device)

    result = await execute_remote_command(
        db,
        device=device,
        credential=cred,
        session=None,
        jarvis_session_id=sid,
        command_id=command_id,
        type_name="command",
        payload={"tool": "write_file", "arguments": {"path": "demo.txt", "content": "hi"}},
    )
    return result  # {"status": "approval_required", "approval_id": ...}


def _command(db, device_id, command_id):
    from app.models.remote import RemoteCommand

    return db.scalars(
        select(RemoteCommand).where(
            RemoteCommand.command_id == command_id,
            RemoteCommand.device_id == device_id,
        )
    ).first()


# ---------------------------------------------------------------------------
# Ligação ApprovalRequest ↔ RemoteCommand (approval_id)
# ---------------------------------------------------------------------------

async def test_executor_links_approval_to_command(db):
    dev_id, token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, token, "cmd-appr-1")

    assert result["status"] == "approval_required"
    approval_id = result["approval_id"]

    cmd = _command(db, dev_id, "cmd-appr-1")
    assert cmd is not None
    assert cmd.status == RemoteCommandStatus.PENDING_APPROVAL.value
    assert cmd.approval_id == approval_id  # ligação Fase 12.4

    from app.remote import remote_commands as cmd_service

    found = cmd_service.get_command_by_approval(db, approval_id)
    assert found is not None
    assert found.command_id == "cmd-appr-1"


# ---------------------------------------------------------------------------
# Retomada: aprovado executa / negado não executa
# ---------------------------------------------------------------------------

async def test_resume_approved_executes_and_persists(db):
    from app.models import ApprovalRequest, AuditLog
    from app.models.remote import Device
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.services import approvals as approval_service
    from app.tools import ToolResult

    dev_id, token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, token, "cmd-appr-1")
    approval_id = result["approval_id"]

    device = db.get(Device, dev_id)
    jarvis_session_id = get_or_create_jarvis_session(db, device)

    approval = db.get(ApprovalRequest, approval_id)
    approval_service.mark_decided(db, approval, approved=True)

    cmd = _command(db, dev_id, "cmd-appr-1")
    with mock.patch("app.remote.resume._run_tool", new_callable=mock.AsyncMock) as rt:
        rt.return_value = ToolResult.success("conteúdo salvo")

        out = await resume_service.resume_remote_command(
            db,
            command=cmd,
            device=device,
            jarvis_session_id=jarvis_session_id,
            approval_status=approval.status,
        )

    assert out["status"] == "executed"
    assert out["ok"] is True
    rt.assert_awaited_once()
    # A tool executada foi write_file, na sessão JARVIS do device.
    assert rt.await_args.args[2] is None  # provider None (sem loop de agente)
    assert rt.await_args.args[1] == jarvis_session_id

    db.refresh(cmd)
    assert cmd.status == RemoteCommandStatus.EXECUTED.value

    rows = [a for a in db.scalars(select(AuditLog)).all() if a.action == "remote.command.approved"]
    assert any(a.tool == "write_file" and a.allowed is True for a in rows)


async def test_resume_denied_does_not_execute(db):
    from app.models import ApprovalRequest, AuditLog
    from app.models.remote import Device
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.services import approvals as approval_service

    dev_id, token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, token, "cmd-appr-1")
    approval_id = result["approval_id"]

    device = db.get(Device, dev_id)
    jarvis_session_id = get_or_create_jarvis_session(db, device)

    approval = db.get(ApprovalRequest, approval_id)
    approval_service.mark_decided(db, approval, approved=False)

    cmd = _command(db, dev_id, "cmd-appr-1")
    out = await resume_service.resume_remote_command(
        db,
        command=cmd,
        device=device,
        jarvis_session_id=jarvis_session_id,
        approval_status=approval.status,
    )

    assert out["status"] == "denied"
    assert out["ok"] is False
    db.refresh(cmd)
    assert cmd.status == RemoteCommandStatus.FAILED.value

    # NÃO houve execution de tool; a auditoria registra a negação.
    exec_rows = [a for a in db.scalars(select(AuditLog)).all() if a.action == "tool.execute"]
    assert not any(r.tool == "write_file" for r in exec_rows)
    denied = [a for a in db.scalars(select(AuditLog)).all() if a.action == "remote.command.denied"]
    assert any(a.tool == "write_file" and a.allowed is False for a in denied)


async def test_resume_requires_pending_approval(db):
    from app.models import ApprovalRequest
    from app.models.remote import Device
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.services import approvals as approval_service

    dev_id, token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, token, "cmd-appr-1")
    approval_id = result["approval_id"]
    device = db.get(Device, dev_id)
    jarvis_session_id = get_or_create_jarvis_session(db, device)

    approval = db.get(ApprovalRequest, approval_id)
    approval_service.mark_decided(db, approval, approved=True)
    approval.status = "approved"
    db.commit()

    # Executa uma vez (vira EXECUTED).
    cmd = _command(db, dev_id, "cmd-appr-1")
    with mock.patch("app.remote.resume._run_tool", new_callable=mock.AsyncMock) as rt:
        from app.tools import ToolResult

        rt.return_value = ToolResult.success("ok")
        await resume_service.resume_remote_command(
            db, command=cmd, device=device, jarvis_session_id=jarvis_session_id, approval_status="approved"
        )

    # Retomar de novo (já EXECUTED) → erro, sem re-execução.
    with pytest.raises(resume_service.RemoteResumeError):
        await resume_service.resume_remote_command(
            db, command=cmd, device=device, jarvis_session_id=jarvis_session_id, approval_status="approved"
        )


# ---------------------------------------------------------------------------
# Independência da conexão + entrega best-effort
# ---------------------------------------------------------------------------

async def test_resume_does_not_require_connection(db):
    """A retomada NÃO depende da conexão WebSocket: sem agente/gateway, funciona."""
    from app.models import ApprovalRequest
    from app.models.remote import Device
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.services import approvals as approval_service

    dev_id, token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, token, "cmd-appr-1")
    device = db.get(Device, dev_id)
    jarvis_session_id = get_or_create_jarvis_session(db, device)
    approval = db.get(ApprovalRequest, result["approval_id"])
    approval_service.mark_decided(db, approval, approved=True)

    cmd = _command(db, dev_id, "cmd-appr-1")
    with mock.patch("app.remote.resume._run_tool", new_callable=mock.AsyncMock) as rt:
        from app.tools import ToolResult

        rt.return_value = ToolResult.success("ok")
        out = await resume_service.resume_remote_command(
            db, command=cmd, device=device, jarvis_session_id=jarvis_session_id, approval_status="approved"
        )
    assert out["status"] == "executed"


def test_deliver_result_offline_is_noop(db):
    """Sem agente ativo (device offline), a entrega é silenciosa."""
    from app.remote import runtime

    runtime.reset_remote_gateway()
    ok = runtime.deliver_command_result("device-x", "cmd-1", {"status": "executed"})
    assert ok is False  # nenhum agente/com conexão


async def test_deliver_result_pushes_to_connected_gateway(db):
    from app.remote import runtime
    from app.remote.agent import RemoteAgent
    from app.remote.connection import InMemoryConnection, create_duplex
    from tests.remote_fakes import FakeGateway, wait_for

    dev_id, token = _paired_device(db)
    pipe_a, pipe_b = create_duplex()
    server = FakeGateway(pipe_a)
    await server.start()
    agent = RemoteAgent(
        device_id=dev_id, device_token=token,
        connection_factory=lambda: InMemoryConnection(pipe_b),
        heartbeat_interval=0.2, min_reconnect_delay=0.05, max_reconnect_delay=0.2,
    )
    runtime.reset_remote_gateway()
    runtime._agent = agent
    await agent.start()
    try:
        await wait_for(lambda: agent.snapshot()["authenticated"])
        assert agent._gateway is not None and agent._gateway.connected

        runtime.deliver_command_result(dev_id, "cmd-out-1", {"status": "executed", "ok": True})
        await wait_for(
            lambda: any(
                e.type.name == "COMMAND_RESULT" and e.command_id == "cmd-out-1"
                for e in server.received
            )
        )
        result_ev = next(
            e for e in server.received
            if e.type.name == "COMMAND_RESULT" and e.command_id == "cmd-out-1"
        )
        assert result_ev.payload["status"] == "executed"
    finally:
        await agent.stop()
        await server.stop()
        runtime.reset_remote_gateway()


# ---------------------------------------------------------------------------
# Consulta do resultado persistido (fonte de recuperação offline)
# ---------------------------------------------------------------------------

def test_command_detail_returns_sanitized_result(db):
    from app.remote import remote_commands as cmd_service

    dev_id, token = _paired_device(db)
    cmd = cmd_service.register_command(
        db, device_id=dev_id, command_id="cmd-q", cmd_type="command",
        payload={"tool": "write_file", "arguments": {}},
    )
    cmd_service.mark_pending_approval(db, cmd)
    cmd_service.mark_executed(db, cmd, result="conteúdo salvo", ok=True)

    detail = cmd_service.command_detail(db.get(__import__("app.models.remote", fromlist=["RemoteCommand"]).RemoteCommand, cmd.id))
    assert detail["status"] == RemoteCommandStatus.EXECUTED.value
    assert detail["result"] == "conteúdo salvo"
    assert detail["error"] is None
    # Nunca expõe payload de entrada cru desnecessário nem secrets no detail.
    assert "approval_id" not in detail or detail.get("approval_id") is not None
    blob = " ".join(f"{k}={v}" for k, v in detail.items())
    assert "token" not in blob.lower()
    assert json.dumps(detail.get("result")) is not None
