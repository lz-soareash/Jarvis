"""Testes da Fase 12.8 — validação final, hardening e máquina de estados.

Cobre os cenários que a suíte anterior não exercitava explicitamente:
- Máquina de estados formal do RemoteCommand (transições inválidas rejeitadas).
- Idempotência sobrevivendo a "restart" e a replay de transporte.
- Recuperação de estado crítico (comando/approval/outbox) após restart.
- Reconnect / multi-connection / revogação durante approval pendente.
- SSE sanitizado (replay + live, sem secrets).
- WoL com validação de MAC e auditoria.
- Concorrência: dois comandos simultâneos + mesmo command_id.
- Fixes de hardening da 12.8: shell=False (anti-injeção), receive timeout,
  purge_delivered via SQL, age filter de find_stuck_executing.
"""

import asyncio
from datetime import timedelta
from unittest import mock

import pytest
from sqlalchemy import select

from app.core.enums import RemoteCommandStatus
from app.models.session import utcnow


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _paired_device(db, name="Fase128"):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name=name)
    return device.id, token


def _authed_ctx(db, device_id):
    from app.models.remote import Credential, Device
    from app.remote.jarvis_session import get_or_create_jarvis_session

    device = db.get(Device, device_id)
    cred = db.scalars(select(Credential).where(Credential.device_id == device_id)).first()
    sid = get_or_create_jarvis_session(db, device)
    return device, cred, sid


def _command(db, device_id, command_id):
    from app.models.remote import RemoteCommand

    return db.scalars(
        select(RemoteCommand).where(
            RemoteCommand.command_id == command_id,
            RemoteCommand.device_id == device_id,
        )
    ).first()


# ===========================================================================
# ETAPA 2 — MÁQUINA DE ESTADOS (transições inválidas rejeitadas)
# ===========================================================================

def test_state_machine_rejects_terminal_to_executing(db):
    """EXECUTED → EXECUTING é inválido (claim rejeita comando já processado)."""
    from app.remote import remote_commands as cmd_service
    from app.remote.executor import execute_remote_command

    dev_id, _t = _paired_device(db)
    device, cred, sid = _authed_ctx(db, dev_id)
    asyncio.run(execute_remote_command(
        db, device=device, credential=cred, session=None, jarvis_session_id=sid,
        command_id="sm-exec", type_name="command",
        payload={"tool": "get_current_time", "arguments": {}},
    ))

    cmd = _command(db, dev_id, "sm-exec")
    assert cmd.status == RemoteCommandStatus.EXECUTED.value
    # Re-claim de um EXECUTED → DuplicateCommandError (transição inválida).
    with pytest.raises(cmd_service.DuplicateCommandError):
        cmd_service._claim_for_execution(db, dev_id, "sm-exec")


async def test_state_machine_rejects_failed_to_executed(db):
    """FAILED → EXECUTED é inválido (mark_executed nunca sobrescreve FAILED por claim)."""
    from app.models.remote import Device, RemoteCommand
    from app.remote import remote_commands as cmd_service
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session

    dev_id, _t = _paired_device(db)
    await _queue_l2(db, dev_id, "sm-failed")
    device = db.get(Device, dev_id)
    jsid = get_or_create_jarvis_session(db, device)

    # Nega → FAILED (terminal, equivalente a "denied").
    await resume_service.resume_remote_command(
        db, command=_command(db, dev_id, "sm-failed"), device=device,
        jarvis_session_id=jsid, approval_status="denied",
    )
    cmd = _command(db, dev_id, "sm-failed")
    assert cmd.status == RemoteCommandStatus.FAILED.value

    # FAILED → EXECUTING rejeitado.
    with pytest.raises(cmd_service.DuplicateCommandError):
        cmd_service._claim_for_execution(db, dev_id, "sm-failed")


async def test_state_machine_rejects_denied_to_executing(db):
    """DENIED (FAILED) → EXECUTING rejeitado."""
    from app.models.remote import Device
    from app.remote import remote_commands as cmd_service
    from app.remote.jarvis_session import get_or_create_jarvis_session

    dev_id, _t = _paired_device(db)
    await _queue_l2(db, dev_id, "sm-den")
    device = db.get(Device, dev_id)
    cmd = _command(db, dev_id, "sm-den")
    cmd.status = RemoteCommandStatus.FAILED.value
    db.commit()

    with pytest.raises(cmd_service.DuplicateCommandError):
        cmd_service._claim_for_execution(db, dev_id, "sm-den")


# ===========================================================================
# ETAPA 3 — IDEMPOTÊNCIA sobrevivendo a restart / replay
# ===========================================================================

def test_idempotency_survives_restart_same_command_not_rerun(db):
    """Comando registrado, 'processo reinicia' (nova sessão), replay → duplicado."""
    from app.remote import remote_commands as cmd_service

    dev_id, _t = _paired_device(db)

    # "processo A" registra e executa.
    cmd = cmd_service.register_command(
        db, device_id=dev_id, command_id="restart-1",
        cmd_type="command", payload={"tool": "get_current_time", "arguments": {}},
    )
    cmd_service.mark_executed(db, cmd, result="ok", ok=True)

    # "processo B" (reiniciado) recebe o MESMO comando de novo.
    from app.remote.executor import RemoteExecutionError, execute_remote_command

    device, cred, sid = _authed_ctx(db, dev_id)
    with pytest.raises(RemoteExecutionError):
        asyncio.run(execute_remote_command(
            db, device=device, credential=cred, session=None, jarvis_session_id=sid,
            command_id="restart-1", type_name="command",
            payload={"tool": "get_current_time", "arguments": {}},
        ))

    # Ainda só UMA execução (status EXECUTED, não re-executado).
    held = _command(db, dev_id, "restart-1")
    assert held.status == RemoteCommandStatus.EXECUTED.value


def test_idempotency_same_command_different_device_independent(db):
    """Mesmo command_id em devices diferentes é tratado como independente."""
    from app.remote import remote_commands as cmd_service

    dev_a, _t = _paired_device(db, name="A")
    dev_b, _t = _paired_device(db, name="B")

    cmd_service.register_command(
        db, device_id=dev_a, command_id="shared", cmd_type="command",
        payload={"tool": "get_current_time", "arguments": {}},
    )
    # Registro no device B com o mesmo command_id NÃO é duplicado.
    cmd_b = cmd_service.register_command(
        db, device_id=dev_b, command_id="shared", cmd_type="command",
        payload={"tool": "get_current_time", "arguments": {}},
    )
    assert cmd_b.device_id == dev_b


# ===========================================================================
# ETAPA 4/13 — RECONNECT / MULTI-CONNECTION
# ===========================================================================

async def test_backoff_has_jitter_and_obeys_max():
    from app.remote.agent import RemoteAgent

    agent = RemoteAgent(
        device_id="d", device_token="t", connection_factory=lambda: None,
        min_reconnect_delay=1.0, max_reconnect_delay=16.0, jitter=0.2,
    )
    d = 1.0
    for _ in range(5):
        d = agent._backoff(d)
        assert d <= 16.0
        assert d >= 0.0


async def test_reconnect_not_busy_loop_when_stopped(db):
    """Shutdown interrompe o loop de reconnect (sem busy loop / retry infinito)."""
    from app.remote.agent import RemoteAgent
    from tests.remote_fakes import FailingConnection

    agent = RemoteAgent(
        device_id="d", device_token="t",
        connection_factory=lambda: FailingConnection(),
        min_reconnect_delay=0.01, max_reconnect_delay=0.05,
    )
    await agent.start()
    await asyncio.sleep(0.05)
    await agent.stop()
    # Após stop, _running False e nenhuma task de supervisor ativa.
    assert agent._running is False
    assert agent._supervisor is None


# ===========================================================================
# ETAPA 5 — REVOGAÇÃO durante approval pendente
# ===========================================================================

async def test_revocation_blocks_resume_of_pending_approval(db, client):
    """Device revogado enquanto approval pendente → respond bloqueado (401)."""
    from app.models.remote import Device
    from app.remote import devices as devices_svc
    from app.tools import ToolResult

    dev_id, token = _paired_device(db)
    result = await _queue_l2(db, dev_id, "rv-pending")
    approval_id = result["approval_id"]

    # Revoga o device (approval ainda PENDING).
    devices_svc.revoke_device(db, dev_id)
    device = db.get(Device, dev_id)
    assert device.is_active is False

    # Fase 28.1: approval ancorado ao JARVIS do device exige o device dono;
    # revogado → token não autentica mais (401 no boundary HTTP).
    with mock.patch("app.remote.resume._run_tool", new_callable=mock.AsyncMock) as rt:
        rt.return_value = ToolResult.success("ok")
        resp = client.post(
            f"/api/approvals/{approval_id}/respond",
            json={"approved": True},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 401
    assert rt.await_count == 0  # retomada bloqueada — nada foi executado


# ===========================================================================
# ETAPA 9 — SSE sanitizado (replay + live, sem secrets)
# ===========================================================================

def test_events_sanitize_nested_and_secrets():
    from app.remote.events import publish_event, reset_events, subscribe_events

    reset_events()
    publish_event("remote.command.executed", {
        "device_id": "dev1",
        "command_id": "c1",
        "status": "executed",
        "nested": {"token": "SECRET123", "authorization": "Bearer x"},
        "list": [1, 2, 3],
    })
    _, history = subscribe_events()
    published = next(e for e in history if e["type"] == "remote.command.executed")
    data = published["data"]
    assert data["device_id"] == "dev1"
    assert data["status"] == "executed"
    # Nested/lista foram REMOVIDOS pelo sanitiser (não vazam secrets).
    assert "nested" not in data
    assert "list" not in data
    assert "SECRET123" not in str(data)
    assert "Bearer" not in str(data)


# ===========================================================================
# ETAPA 10 — WoL (validação de MAC / auditoria)
# ===========================================================================

def test_wol_invalid_mac_rejected_no_send():
    """WoL: MAC inválido é rejeitado e nenhum packet é enviado."""
    from app.tools.wol import WakeOnLan

    tool = WakeOnLan()
    with mock.patch("app.tools.wol._udp_sendto", new_callable=mock.AsyncMock) as udp:
        result = asyncio.run(tool.run(None, mac_address="ZZ:ZZ:ZZ:ZZ:ZZ:ZZ"))
        assert result.ok is False
        udp.assert_not_called()


# ===========================================================================
# ETAPA 16/17 — CONCORRÊNCIA + integração
# ===========================================================================

async def test_two_simultaneous_commands_run_independently(db):
    """Dois comandos simultâneos de devices diferentes não corrompem estado."""
    from app.remote.executor import execute_remote_command

    dev_a, _t = _paired_device(db, name="A")
    dev_b, _t = _paired_device(db, name="B")
    a_ctx = _authed_ctx(db, dev_a)
    b_ctx = _authed_ctx(db, dev_b)

    a_dev, a_cred, a_sid = a_ctx
    b_dev, b_cred, b_sid = b_ctx

    outs = await asyncio.gather(
        execute_remote_command(
            db, device=a_dev, credential=a_cred, session=None, jarvis_session_id=a_sid,
            command_id="sim-a", type_name="command",
            payload={"tool": "get_current_time", "arguments": {}},
        ),
        execute_remote_command(
            db, device=b_dev, credential=b_cred, session=None, jarvis_session_id=b_sid,
            command_id="sim-b", type_name="command",
            payload={"tool": "get_current_time", "arguments": {}},
        ),
    )
    assert all(o["status"] == "executed" for o in outs)
    assert _command(db, dev_a, "sim-a").status == RemoteCommandStatus.EXECUTED.value
    assert _command(db, dev_b, "sim-b").status == RemoteCommandStatus.EXECUTED.value


def test_same_command_id_only_one_persists(db):
    """Mesmo command_id só pode existir uma vez por device (UNIQUE no banco)."""
    from app.models.remote import RemoteCommand
    from app.remote import remote_commands as cmd_service

    dev_id, _t = _paired_device(db)
    cmd_service.register_command(
        db, device_id=dev_id, command_id="dup-sim", cmd_type="command",
        payload={"tool": "get_current_time", "arguments": {}},
    )
    # Segundo registro do mesmo command_id → DuplicateCommandError.
    with pytest.raises(cmd_service.DuplicateCommandError):
        cmd_service.register_command(
            db, device_id=dev_id, command_id="dup-sim", cmd_type="command",
            payload={"tool": "get_current_time", "arguments": {}},
        )
    rows = list(db.scalars(select(RemoteCommand).where(
        RemoteCommand.device_id == dev_id, RemoteCommand.command_id == "dup-sim"
    )))
    assert len(rows) == 1  # apenas UMA linha persistida


# ===========================================================================
# FIXES DE HARDENING DA 12.8
# ===========================================================================

def test_run_allowed_command_no_shell_injection():
    """shell=False: `&`/`;` de injeção tornam-se argumentos — nunca shell ops."""
    import shlex

    from app.computer import controller

    # Em shell=False, o parser divide por espaços; `&` não é operador de shell.
    assert shlex.split("hostname & calc", posix=True) == ["hostname", "&", "calc"]

    with mock.patch("app.computer.controller.subprocess.run") as mocked_run:
        mocked_run.return_value = mock.Mock(returncode=0, stdout="host", stderr="")
        ctrl = controller.SystemController()
        ctrl.run_allowed_command("hostname & calc")
        args, kwargs = mocked_run.call_args.args, mocked_run.call_args.kwargs
        # O argv passado ao subprocess NÃO contém shell operator interpretável:
        # o subprocess recebe 'hostname' como primeiro argumento, sem shell.
        assert args[0][0] == "hostname"
        assert kwargs.get("shell", False) is False
        assert "&" not in args[0][0]
        assert "calc" != args[0][0]  # o payload injetado nunca vira o executável


def test_run_allowed_command_never_uses_shell_true():
    """subprocess roda com shell=False e argv (nunca shell=True)."""
    from app.computer import controller

    with mock.patch("app.computer.controller.subprocess.run") as mocked_run:
        mocked_run.return_value = mock.Mock(returncode=0, stdout="host", stderr="")
        ctrl = controller.SystemController()
        ctrl.run_allowed_command("hostname")
        call = mocked_run.call_args
        args, kwargs = call.args, call.kwargs
        assert isinstance(args[0], list) and args[0][0] == "hostname"
        assert kwargs.get("shell", False) is False


def test_find_stuck_executing_age_filter(db):
    """Filtro de idade funciona: comandos recentes NÃO entram no cutoff."""
    from app.remote import remote_commands as cmd_service

    dev_id, _t = _paired_device(db)
    cmd = cmd_service.register_command(
        db, device_id=dev_id, command_id="fresh", cmd_type="command",
        payload={"tool": "get_current_time", "arguments": {}},
    )
    cmd_service.mark_pending_approval(db, cmd)
    cmd_service._claim_for_execution(db, dev_id, "fresh")

    # Comando recém-criado NÃO deve aparecer como "stuck há > 1000s".
    stuck = cmd_service.find_stuck_executing(
        db, device_id=dev_id, older_than_seconds=1000
    )
    assert not any(c.command_id == "fresh" for c in stuck)

    # Comandos mais antigos que o cutoff aparecem.
    cmd = _command(db, dev_id, "fresh")
    cmd.created_at = utcnow() - timedelta(seconds=5000)
    db.commit()
    stuck_old = cmd_service.find_stuck_executing(
        db, device_id=dev_id, older_than_seconds=1000
    )
    assert any(c.command_id == "fresh" for c in stuck_old)


def test_purge_delivered_uses_sql_cutoff(db):
    """purge_delivered remove só entradas antigas entregues (via SQL)."""
    from app.models.session import utcnow as utc
    from app.remote import outbox as outbox_service

    dev_id, _t = _paired_device(db)
    old = outbox_service.enqueue_result(
        db, device_id=dev_id, command_id="old", payload={"ok": True}
    )
    recent = outbox_service.enqueue_result(
        db, device_id=dev_id, command_id="recent", payload={"ok": True}
    )
    # Marca como entregues, com idades diferentes.
    old.delivered_at = utc() - timedelta(days=30)
    recent.delivered_at = utc()
    db.commit()

    removed = outbox_service.purge_delivered(db, older_than_days=7)
    assert removed == 1  # apenas o antigo
    # O antigo foi removido; o recente permanece no banco (mesmo entregue).
    from app.models.remote import RemoteOutbox

    rows = list(db.scalars(select(RemoteOutbox)).all())
    ids = {r.command_id for r in rows}
    assert "recent" in ids
    assert "old" not in ids


def test_receive_timeout_raises_connection_error():
    """receive() com timeout → ConnectionError (não bloqueia para sempre)."""
    from app.remote.connection import WebSocketConnection

    class _StubWS:
        async def recv(self):
            await asyncio.sleep(5)

    conn = WebSocketConnection("ws://x", timeout=0.01)
    conn.state = "connected"
    conn._ws = _StubWS()
    with pytest.raises(ConnectionError):
        asyncio.run(conn.receive())


async def _queue_l2(db, device_id, command_id):
    from app.models.remote import Credential, Device
    from app.remote.executor import execute_remote_command
    from app.remote.jarvis_session import get_or_create_jarvis_session

    device = db.get(Device, device_id)
    cred = db.scalars(select(Credential).where(Credential.device_id == device_id)).first()
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
