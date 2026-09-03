"""Testes da Fase 12.3 — Remote Agent: conexão persistente, comandos, idempotência,
reconexão, revogação e integração com o pipeline de segurança do JARVIS.

Cobertura: executor (reuso do Tool Registry/Permission Engine/AuditLog),
idempotência atômica, approval para L2/L3, mapeamento device→Sessão JARVIS,
reconexão com backoff, revogação, e fluxo de integração Gateway→Agent→Executor.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.session import utcnow


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _paired_device(db, name="Remote"):
    """Cria um device pairing completo → (device.id, token)."""
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name=name)
    return device.id, token


# ---------------------------------------------------------------------------
# Sessão JARVIS por device
# ---------------------------------------------------------------------------

def test_device_gets_stable_jarvis_session(db):
    from app.models import Session as JarvisSession
    from app.models.remote import Device
    from app.remote.jarvis_session import get_or_create_jarvis_session

    dev_id, token = _paired_device(db)
    device = db.get(Device, dev_id)

    s1 = get_or_create_jarvis_session(db, device)
    s2 = get_or_create_jarvis_session(db, device)

    assert s1 == s2
    assert db.get(JarvisSession, s1) is not None
    db.refresh(device)
    assert device.jarvis_session_id == s1


# ---------------------------------------------------------------------------
# Executor — reuso do pipeline (Tool Registry → Permission → Audit)
# ---------------------------------------------------------------------------

def _authed_ctx(db, device_id, token):
    from app.models.remote import Credential, Device
    from app.remote.jarvis_session import get_or_create_jarvis_session

    device = db.get(Device, device_id)
    cred = db.scalars(select(Credential).where(Credential.device_id == device_id)).first()
    sid = get_or_create_jarvis_session(db, device)
    return device, cred, sid


async def test_executor_runs_L0_tool_and_audits(db):
    from app.remote.executor import execute_remote_command

    dev_id, token = _paired_device(db)
    device, cred, sid = _authed_ctx(db, dev_id, token)

    result = await execute_remote_command(
        db,
        device=device,
        credential=cred,
        session=None,
        jarvis_session_id=sid,
        command_id="cmd-l0-1",
        type_name="command",
        payload={"tool": "get_current_time", "arguments": {}},
    )

    assert result["status"] == "executed"
    assert result["ok"] is True
    assert "result" in result

    # Persistido (idempotência).
    from app.models.remote import RemoteCommand
    from app.core.enums import RemoteCommandStatus

    cmd = db.scalars(
        select(RemoteCommand).where(RemoteCommand.command_id == "cmd-l0-1")
    ).first()
    assert cmd is not None
    assert cmd.status == RemoteCommandStatus.EXECUTED.value

    # Auditoria registrada via o MESMO AuditLog.
    from app.models import AuditLog

    rows = [a for a in db.scalars(select(AuditLog)).all() if a.tool == "get_current_time"]
    assert any(a.action == "tool.execute" and a.allowed is True for a in rows)


async def test_executor_rejects_duplicate_command_atomically(db):
    from app.remote.executor import RemoteExecutionError, execute_remote_command

    dev_id, token = _paired_device(db)
    device, cred, sid = _authed_ctx(db, dev_id, token)

    await execute_remote_command(
        db, device=device, credential=cred, session=None, jarvis_session_id=sid,
        command_id="cmd-dup", type_name="command",
        payload={"tool": "get_current_time", "arguments": {}},
    )
    with pytest.raises(RemoteExecutionError):
        await execute_remote_command(
            db, device=device, credential=cred, session=None, jarvis_session_id=sid,
            command_id="cmd-dup", type_name="command",
            payload={"tool": "get_current_time", "arguments": {}},
        )


async def test_executor_rejects_unregistered_tool(db):
    from app.remote.executor import execute_remote_command

    dev_id, token = _paired_device(db)
    device, cred, sid = _authed_ctx(db, dev_id, token)

    result = await execute_remote_command(
        db, device=device, credential=cred, session=None, jarvis_session_id=sid,
        command_id="cmd-x", type_name="command",
        payload={"tool": "tool_inexistente", "arguments": {}},
    )
    assert result["status"] == "failed"  # Registry rejeita, sem bypass


async def test_executor_L2_creates_approval_not_executes(db):
    """WriteFile é nível 2 → cria ApprovalRequest e NÃO executa."""
    from app.models import ApprovalRequest, AuditLog
    from app.remote.executor import execute_remote_command

    dev_id, token = _paired_device(db)
    device, cred, sid = _authed_ctx(db, dev_id, token)

    result = await execute_remote_command(
        db, device=device, credential=cred, session=None, jarvis_session_id=sid,
        command_id="cmd-write", type_name="command",
        payload={"tool": "write_file", "arguments": {"path": "x", "content": "y"}},
    )
    assert result["status"] == "approval_required"
    assert result["permission_level"] == 2

    approval = db.get(ApprovalRequest, result["approval_id"])
    assert approval is not None
    assert approval.session_id == sid
    assert approval.status == "pending"

    # NÃO executou: apenas approval_requested na auditoria (sem tool.execute p/ escrita).
    exec_rows = [a for a in db.scalars(select(AuditLog)).all() if a.action == "tool.execute"]
    assert not any(r.tool == "write_file" for r in exec_rows)


async def test_executor_L3_creates_approval(db):
    from app.remote.executor import execute_remote_command

    dev_id, token = _paired_device(db)
    device, cred, sid = _authed_ctx(db, dev_id, token)

    result = await execute_remote_command(
        db, device=device, credential=cred, session=None, jarvis_session_id=sid,
        command_id="cmd-kill", type_name="command",
        payload={"tool": "kill_process", "arguments": {"pid": 1}},
    )
    assert result["status"] == "approval_required"
    assert result["permission_level"] == 3


async def test_executor_payload_over_limit_rejected(db, monkeypatch):
    from app.core.config import settings
    from app.remote.executor import RemoteExecutionError, execute_remote_command

    monkeypatch.setattr(settings, "remote_command_max_payload", 10)
    dev_id, token = _paired_device(db)
    device, cred, sid = _authed_ctx(db, dev_id, token)

    with pytest.raises(RemoteExecutionError):
        await execute_remote_command(
            db, device=device, credential=cred, session=None, jarvis_session_id=sid,
            command_id="cmd-big", type_name="command",
            payload={"tool": "get_current_time", "arguments": {"x": "y" * 20}},
        )


# ---------------------------------------------------------------------------
# Reconexão / backoff / revogação
# ---------------------------------------------------------------------------

async def test_backoff_grows_and_respects_max():
    from app.remote.agent import RemoteAgent

    agent = RemoteAgent(
        device_id="d", device_token="t",
        connection_factory=lambda: _stub_conn(),
        min_reconnect_delay=0.5, max_reconnect_delay=8.0, jitter=0.0,
    )
    d = 0.5
    seen = []
    for _ in range(6):
        d = agent._backoff(d)
        seen.append(d)
    assert seen[0] == 1.0
    assert seen[1] == 2.0
    assert seen[2] == 4.0
    assert seen[3] == 8.0
    assert seen[4] == 8.0  # teto respeitado


def _stub_conn():
    from app.remote.connection import InMemoryConnection, MemoryPipe

    return InMemoryConnection(MemoryPipe())


async def test_authenticate_revoked_returns_terminal(db):
    from app.remote import devices as devices_svc
    from app.remote.agent import RemoteAgent

    dev_id, token = _paired_device(db)
    devices_svc.revoke_device(db, dev_id)

    agent = RemoteAgent(
        device_id=dev_id, device_token=token,
        connection_factory=lambda: _stub_conn(),
    )
    assert await agent._authenticate() is False  # terminal (não reconecta infinito)
    assert agent.snapshot()["revocation"] == "revoked"


async def test_authenticate_ok_sets_jarvis_session(db):
    from app.remote.agent import RemoteAgent

    dev_id, token = _paired_device(db)
    agent = RemoteAgent(device_id=dev_id, device_token=token, connection_factory=lambda: _stub_conn())
    assert await agent._authenticate() is True
    assert agent.snapshot()["authenticated"] is True


# ---------------------------------------------------------------------------
# Integração: Gateway → Agent → Executor → resultado
# ---------------------------------------------------------------------------

async def _agent_server_pair(db, heartbeat=0.2):
    from tests.remote_fakes import FakeGateway

    from app.remote.agent import RemoteAgent
    from app.remote.connection import InMemoryConnection, create_duplex

    dev_id, token = _paired_device(db)
    pipe_a, pipe_b = create_duplex()
    server = FakeGateway(pipe_a)
    await server.start()
    agent = RemoteAgent(
        device_id=dev_id, device_token=token,
        connection_factory=lambda: InMemoryConnection(pipe_b),
        heartbeat_interval=heartbeat,
        min_reconnect_delay=0.05, max_reconnect_delay=0.2,
    )
    return agent, server, token


async def test_agent_receives_and_runs_command_integration(db):
    from app.db.session import SessionLocal
    from app.models import AuditLog, Session as JarvisSession

    agent, server, _token = await _agent_server_pair(db)
    await agent.start()
    try:
        from tests.remote_fakes import wait_for

        await wait_for(lambda: agent.snapshot()["authenticated"])

        await server.push_command(
            "cmd-int-1", {"tool": "get_current_time", "arguments": {}}
        )
        await wait_for(
            lambda: any(
                e.type.name == "COMMAND_RESULT" and e.command_id == "cmd-int-1"
                for e in server.received
            )
        )
        result_ev = next(
            e for e in server.received
            if e.type.name == "COMMAND_RESULT" and e.command_id == "cmd-int-1"
        )
        assert result_ev.payload["status"] == "executed"

        with SessionLocal() as s:
            assert s.query(JarvisSession).count() >= 1
            arows = s.query(AuditLog).all()
            assert all(_token not in str(getattr(a, "detail", "")) for a in arows)
    finally:
        await agent.stop()
        await server.stop()


async def test_agent_duplicate_command_not_rerun(db):
    agent, server, _token = await _agent_server_pair(db)
    await agent.start()
    try:
        from tests.remote_fakes import wait_for

        await wait_for(lambda: agent.snapshot()["authenticated"])
        await server.push_command("cmd-dup-1", {"tool": "get_current_time", "arguments": {}})
        await wait_for(
            lambda: any(
                e.type.name == "COMMAND_RESULT" and e.command_id == "cmd-dup-1"
                for e in server.received
            )
        )
        before = _command_count(db, "cmd-dup-1")
        await server.push_command("cmd-dup-1", {"tool": "get_current_time", "arguments": {}})
        await wait_for(
            lambda: any(
                e.type.name == "COMMAND_RESULT" and e.command_id == "cmd-dup-1"
                and e.payload.get("status") == "error"
                for e in server.received
            )
        )
        assert _command_count(db, "cmd-dup-1") == before
    finally:
        await agent.stop()
        await server.stop()


async def test_agent_revoked_mid_command_rejected(db):
    from app.db.session import SessionLocal
    from app.models import Credential
    from app.remote import credentials as cred_svc

    agent, server, token = await _agent_server_pair(db)
    await agent.start()
    try:
        from tests.remote_fakes import wait_for

        await wait_for(lambda: agent.snapshot()["authenticated"])
        with SessionLocal() as s:
            cred = s.query(Credential).first()
            cred_svc.revoke_credential(s, cred.id)
        await server.push_command("cmd-rv", {"tool": "get_current_time", "arguments": {}})
        await wait_for(
            lambda: any(
                e.type.name == "COMMAND_RESULT" and e.command_id == "cmd-rv"
                and e.payload.get("status") == "revoked"
                for e in server.received
            )
        )
    finally:
        await agent.stop()
        await server.stop()


def _command_count(db, command_id):
    from app.models.remote import RemoteCommand

    return len(db.scalars(select(RemoteCommand).where(RemoteCommand.command_id == command_id)).all())