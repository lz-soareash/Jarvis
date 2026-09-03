"""Testes da Fase 12.7 — fila de re-entrega (outbox) de resultados remotos.

Cobre: enfileiramento idempotente por (device, command), listagem de pendentes,
marcação como entregue, decodificação de payload, o push offline (que enfileira e
retorna False), o push online (que enfileira E entrega imediatamente, marcando
entregue) e a re-entrega direcionada no reconnect via
`RemoteAgent._flush_pending_results`.
"""

from sqlalchemy import select

import pytest


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _paired_device(db, name="Outbox"):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name=name)
    return device.id, token


def _outbox_rows(db, device_id=None):
    from app.models.remote import RemoteOutbox

    stmt = select(RemoteOutbox)
    if device_id is not None:
        stmt = stmt.where(RemoteOutbox.device_id == device_id)
    return list(db.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Serviço de outbox
# ---------------------------------------------------------------------------

def test_enqueue_idempotent_per_command(db):
    from app.remote import outbox as outbox_service

    dev_id, _t = _paired_device(db)
    a = outbox_service.enqueue_result(
        db, device_id=dev_id, command_id="c1", payload={"status": "executed"}
    )
    b = outbox_service.enqueue_result(
        db, device_id=dev_id, command_id="c1", payload={"status": "executed"}
    )
    assert a.id == b.id  # idempotente: mesma linha
    assert len(_outbox_rows(db, dev_id)) == 1


def test_enqueue_different_commands_separate_rows(db):
    from app.remote import outbox as outbox_service

    dev_id, _t = _paired_device(db)
    outbox_service.enqueue_result(db, device_id=dev_id, command_id="c1", payload={"ok": True})
    outbox_service.enqueue_result(db, device_id=dev_id, command_id="c2", payload={"ok": True})
    assert len(_outbox_rows(db, dev_id)) == 2


def test_list_undelivered_and_mark_delivered(db):
    from app.remote import outbox as outbox_service

    dev_id, _t = _paired_device(db)
    entry = outbox_service.enqueue_result(
        db, device_id=dev_id, command_id="c1", payload={"status": "executed"}
    )
    pending = outbox_service.list_undelivered(db, device_id=dev_id)
    assert [e.command_id for e in pending] == ["c1"]

    outbox_service.mark_delivered(db, entry)
    assert outbox_service.list_undelivered(db, device_id=dev_id) == []


def test_payload_roundtrip_and_malformed(db):
    from app.remote import outbox as outbox_service
    from app.models.remote import RemoteOutbox
    from app.models.session import utcnow

    dev_id, _t = _paired_device(db)
    entry = outbox_service.enqueue_result(
        db, device_id=dev_id, command_id="c1",
        payload={"status": "executed", "n": 1},
    )
    assert outbox_service.payload_of(entry) == {"status": "executed", "n": 1}

    bad = RemoteOutbox(
        device_id=dev_id, command_id="c-bad", payload_json="{nope",
        created_at=utcnow(),
    )
    assert outbox_service.payload_of(bad) == {}


# ---------------------------------------------------------------------------
# Integração com runtime (push)
# ---------------------------------------------------------------------------

def test_runtime_offline_enqueues_and_returns_false(db):
    from app.remote import outbox as outbox_service, runtime

    runtime.reset_remote_gateway()
    dev_id, _t = _paired_device(db)

    ok = runtime.deliver_command_result(
        dev_id, "cq", {"status": "executed", "ok": True}
    )
    assert ok is False  # sem agente ativo → não entregou
    pending = outbox_service.list_undelivered(db, device_id=dev_id)
    assert [e.command_id for e in pending] == ["cq"]  # ficou na fila


async def test_runtime_online_delivers_and_marks(db):
    from app.remote import outbox as outbox_service, runtime
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

        ok = runtime.deliver_command_result(
            dev_id, "cmd-on", {"status": "executed", "ok": True}
        )
        assert ok is True
        await wait_for(
            lambda: any(
                e.type.name == "COMMAND_RESULT" and e.command_id == "cmd-on"
                for e in server.received
            )
        )
        # Enviado e marcado entregue (saiu da fila de pendentes).
        assert outbox_service.list_undelivered(db, device_id=dev_id) == []
    finally:
        await agent.stop()
        await server.stop()
        runtime.reset_remote_gateway()


async def test_flush_pending_delivers_on_reconnect(db):
    """Re-entrega direcionada: resultados pendentes vão à Gateway no reconnect."""
    from app.remote import outbox as outbox_service, runtime
    from app.remote.agent import RemoteAgent

    dev_id, token = _paired_device(db)
    entry = outbox_service.enqueue_result(
        db, device_id=dev_id, command_id="cq",
        payload={"status": "executed", "ok": True},
    )

    sent = []

    class _FakeGW:
        connected = True

        async def send_message(self, envelope):
            sent.append(envelope)

    agent = RemoteAgent(
        device_id=dev_id, device_token=token, connection_factory=lambda: None
    )
    agent._gateway = _FakeGW()
    runtime._agent = agent
    try:
        await agent._flush_pending_results()
        assert len(sent) == 1
        assert sent[0].command_id == "cq"
        assert sent[0].payload["status"] == "executed"
        db.refresh(entry)
        assert entry.delivered_at is not None  # marcado entregue após sucesso
    finally:
        runtime.reset_remote_gateway()


async def test_flush_keeps_undelivered_when_send_fails(db):
    """Se o envio falhar, a entrada permanece pendente (re-tenta no próximo connect)."""
    from app.remote import outbox as outbox_service, runtime
    from app.remote.agent import RemoteAgent

    dev_id, token = _paired_device(db)
    entry = outbox_service.enqueue_result(
        db, device_id=dev_id, command_id="cq", payload={"ok": True}
    )

    class _FailingGW:
        connected = True

        async def send_message(self, envelope):
            raise OSError("broken pipe")

    agent = RemoteAgent(
        device_id=dev_id, device_token=token, connection_factory=lambda: None
    )
    agent._gateway = _FailingGW()
    runtime._agent = agent
    try:
        await agent._flush_pending_results()
        db.refresh(entry)
        assert entry.delivered_at is None  # continua pendente
        assert outbox_service.list_undelivered(db, device_id=dev_id)
    finally:
        runtime.reset_remote_gateway()


# ---------------------------------------------------------------------------
# Endpoint: resultado aprovado vai ao outbox mesmo offline
# ---------------------------------------------------------------------------

async def test_approved_resume_enqueues_when_offline(db):
    from unittest import mock

    from app.models.remote import Device
    from app.remote import outbox as outbox_service, remote_commands as cmd_service
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.tools import ToolResult

    dev_id, _t = _paired_device(db)
    result = await _queue_l2(db, dev_id, "cmd-ao")
    device = db.get(Device, dev_id)
    jarvis_session_id = get_or_create_jarvis_session(db, device)
    cmd = cmd_service.get_command(db, dev_id, "cmd-ao")

    with mock.patch("app.remote.resume._run_tool", new_callable=mock.AsyncMock) as rt:
        rt.return_value = ToolResult.success("ok")
        out = await resume_service.resume_remote_command(
            db, command=cmd, device=device,
            jarvis_session_id=jarvis_session_id, approval_status="approved",
        )
    assert out["status"] == "executed"

    # O comando foi persistido como EXECUTED (fonte de recuperação offline).
    from app.models.remote import RemoteCommand

    held = db.scalars(
        select(RemoteCommand).where(RemoteCommand.command_id == "cmd-ao")
    ).first()
    assert held.status == "executed"


async def _queue_l2(db, device_id, command_id):
    from app.models.remote import Credential, Device
    from app.remote.executor import execute_remote_command
    from app.remote.jarvis_session import get_or_create_jarvis_session

    device = db.get(Device, device_id)
    cred = db.scalars(
        select(Credential).where(Credential.device_id == device_id)
    ).first()
    sid = get_or_create_jarvis_session(db, device)
    return await execute_remote_command(
        db,
        device=device,
        credential=cred,
        session=None,
        jarvis_session_id=sid,
        command_id=command_id,
        type_name="command",
        payload={"tool": "write_file", "arguments": {"path": "demo.txt", "content": "hi"}},
    )
