"""Testes da Fase 12.4-R1 — hardening corretivo de concorrência e consistência.

Cobre os quatro pilares do hardening sobre a retomada de `RemoteCommand`
pós-aprovação (Fase 12.4):

1. Fecho de concorrência no claim ATÔMICO (`PENDING_APPROVAL → EXECUTING`
   via `UPDATE ... WHERE status IN (...)`: só UM executor vence; a outra
   tentativa recebe erro sem re-execução).
2. UNIQUE em `RemoteCommand.approval_id` (1:1 ApprovalRequest ↔ RemoteCommand).
3. Consistência ApprovalRequest ↔ RemoteCommand — decisões concorrentes
   {A:APPROVE + B:APPROVE} só têm UMA decisão efetiva; `applied` só após a
   retomada bem sucedida (nunca `applied=True` com comando PENDING_APPROVAL).
4. Recuperação segura de `EXECUTING` preso → `INTERRUPTED` (resultado
   desconhecido) SEM re-execução automática de operações não idempotentes.
"""

import asyncio
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


def _paired_device(db, name="Hardening"):
    from app.remote.pairing import create_pairing, submit_code

    _, code = create_pairing(db)
    device, token = submit_code(db, code=code, device_name=name)
    return device.id, token


async def _queue_l2_command(db, device_id, command_id):
    """Registra um comando L2 via executor (cria approval + PENDING_APPROVAL)."""
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


def _command(db, device_id, command_id):
    from app.models.remote import RemoteCommand

    return db.scalars(
        select(RemoteCommand).where(
            RemoteCommand.command_id == command_id,
            RemoteCommand.device_id == device_id,
        )
    ).first()


# ---------------------------------------------------------------------------
# 1. Claim atômico — uma única execução sob tentativas concorrentes
# ---------------------------------------------------------------------------

def test_claim_atomic_rejects_second_directly(db):
    """O claim condicional só deixa UMA transição PENDING_APPROVAL → EXECUTING."""
    from app.remote import remote_commands as cmd_service

    dev_id, _token = _paired_device(db)
    cmd = cmd_service.register_command(
        db, device_id=dev_id, command_id="c1",
        cmd_type="command", payload={"tool": "write_file", "arguments": {}},
    )
    cmd_service.mark_pending_approval(db, cmd)

    claimed = cmd_service._claim_for_execution(db, dev_id, "c1")
    assert claimed.status == RemoteCommandStatus.EXECUTING.value

    with pytest.raises(cmd_service.DuplicateCommandError):
        cmd_service._claim_for_execution(db, dev_id, "c1")

    # NÃO re-executa: permanece EXECUTING.
    assert _command(db, dev_id, "c1").status == RemoteCommandStatus.EXECUTING.value


async def test_concurrent_resume_single_execution(db):
    """A:APPROVE + B:APPROVE na retomada → exatamente uma execução de tool."""
    from app.models import ApprovalRequest
    from app.models.remote import Device
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.services import approvals as approval_service
    from app.tools import ToolResult

    dev_id, _token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, "cmd-race")
    approval = db.get(ApprovalRequest, result["approval_id"])
    approval_service.mark_decided(db, approval, approved=True)

    device = db.get(Device, dev_id)
    jarvis_session_id = get_or_create_jarvis_session(db, device)
    cmd = _command(db, dev_id, "cmd-race")

    calls = 0

    async def fake_run_tool(_db, _sid, _provider, _call):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return ToolResult.success("ok")

    with mock.patch("app.remote.resume._run_tool", new=fake_run_tool):
        outs = await asyncio.gather(
            resume_service.resume_remote_command(
                db, command=cmd, device=device, jarvis_session_id=jarvis_session_id,
                approval_status="approved",
            ),
            resume_service.resume_remote_command(
                db, command=cmd, device=device, jarvis_session_id=jarvis_session_id,
                approval_status="approved",
            ),
            return_exceptions=True,
        )

    successes = [o for o in outs if isinstance(o, dict) and o.get("status") == "executed"]
    errors = [e for e in outs if isinstance(e, resume_service.RemoteResumeError)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert calls == 1
    assert _command(db, dev_id, "cmd-race").status == RemoteCommandStatus.EXECUTED.value


async def test_approval_not_double_decided(db):
    """mark_decided é atômico: só UMA decisão concorrente efetiva (A ou B)."""
    from app.models import ApprovalRequest
    from app.services import approvals as approval_service

    dev_id, _token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, "cmd-dec")
    approval = db.get(ApprovalRequest, result["approval_id"])

    _, decided_a = approval_service.mark_decided(db, approval, approved=True)
    _, decided_b = approval_service.mark_decided(db, approval, approved=False)

    assert decided_a is True
    assert decided_b is False  # segunda decisão não compete
    db.refresh(approval)
    assert approval.status == ApprovalStatus.APPROVED.value


# ---------------------------------------------------------------------------
# 2. UNIQUE em approval_id — 1:1 ApprovalRequest ↔ RemoteCommand
# ---------------------------------------------------------------------------

async def test_unique_approval_id_rejects_second_command(db):
    from sqlalchemy.exc import IntegrityError

    from app.remote import remote_commands as cmd_service

    dev_id, _token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, "cmd-own")
    approval_id = result["approval_id"]

    owner = _command(db, dev_id, "cmd-own")
    assert owner.approval_id == approval_id

    other = cmd_service.register_command(
        db, device_id=dev_id, command_id="cmd-other",
        cmd_type="command", payload={"tool": "write_file", "arguments": {}},
    )
    other.approval_id = approval_id
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---------------------------------------------------------------------------
# 3. Consistência Approval ↔ Command (applied só após retomada bem sucedida)
# ---------------------------------------------------------------------------

async def test_failed_resume_leaves_approval_unapplied(db):
    """Se a retomada falhar (comando sem tool válida), `applied` fica False."""
    from app.models import ApprovalRequest
    from app.models.remote import Device
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.services import approvals as approval_service

    dev_id, _token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, "cmd-cons")
    approval = db.get(ApprovalRequest, result["approval_id"])
    device = db.get(Device, dev_id)
    jarvis_session_id = get_or_create_jarvis_session(db, device)

    approval_service.mark_decided(db, approval, approved=True)

    # Falha estrutural: payload sem tool válida → RemoteResumeError.
    cmd = _command(db, dev_id, "cmd-cons")
    cmd.payload_json = "{}"
    db.commit()

    with pytest.raises(resume_service.RemoteResumeError):
        await resume_service.resume_remote_command(
            db, command=_command(db, dev_id, "cmd-cons"), device=device,
            jarvis_session_id=jarvis_session_id, approval_status="approved",
        )

    db.refresh(approval)
    assert approval.applied is False  # nunca applied + comando não concluído


# ---------------------------------------------------------------------------
# 4. Recuperação segura de EXECUTING preso → INTERRUPTED (sem re-exec)
# ---------------------------------------------------------------------------

def test_stuck_executing_recovered_without_reexec(db):
    from app.remote import remote_commands as cmd_service

    dev_id, _token = _paired_device(db)
    cmd = cmd_service.register_command(
        db, device_id=dev_id, command_id="stuck",
        cmd_type="command", payload={"tool": "write_file", "arguments": {}},
    )
    cmd_service.mark_pending_approval(db, cmd)
    claimed = cmd_service._claim_for_execution(db, dev_id, "stuck")
    assert claimed.status == RemoteCommandStatus.EXECUTING.value

    stuck = cmd_service.find_stuck_executing(db, device_id=dev_id)
    assert any(c.command_id == "stuck" for c in stuck)

    recovered = cmd_service.recover_executing(db, dev_id, "stuck", reason="crash")
    db.refresh(recovered)
    assert recovered.status == RemoteCommandStatus.INTERRUPTED.value

    # Estado terminal NÃO é sobrescrito por mark_interrupted (fecho de segurança).
    with pytest.raises(cmd_service.RemoteCommandError):
        cmd_service.mark_interrupted(db, dev_id, "stuck", reason="x")


async def test_resume_does_not_reexecute_interrupted(db):
    from app.models.remote import Device
    from app.remote import remote_commands as cmd_service
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session

    dev_id, _token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, "cmd-int")
    device = db.get(Device, dev_id)
    jarvis_session_id = get_or_create_jarvis_session(db, device)

    cmd_service._claim_for_execution(db, dev_id, "cmd-int")
    cmd_service.mark_interrupted(db, dev_id, "cmd-int", reason="crash")

    cmd = _command(db, dev_id, "cmd-int")
    assert cmd.status == RemoteCommandStatus.INTERRUPTED.value

    with mock.patch("app.remote.resume._run_tool", new_callable=mock.AsyncMock) as rt:
        with pytest.raises(resume_service.RemoteResumeError):
            await resume_service.resume_remote_command(
                db, command=cmd, device=device, jarvis_session_id=jarvis_session_id,
                approval_status="approved",
            )
    rt.assert_not_awaited()


async def test_resume_after_failed_no_reexec(db):
    from app.models.remote import Device
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session

    dev_id, _token = _paired_device(db)
    await _queue_l2_command(db, dev_id, "cmd-fx")
    device = db.get(Device, dev_id)
    jarvis_session_id = get_or_create_jarvis_session(db, device)

    # Nega → comando vira FAILED.
    neg = await resume_service.resume_remote_command(
        db, command=_command(db, dev_id, "cmd-fx"), device=device,
        jarvis_session_id=jarvis_session_id, approval_status="denied",
    )
    assert neg["status"] == "denied"

    # Tentar aprovar depois (comando já FAILED) → erro, sem re-execução.
    with mock.patch("app.remote.resume._run_tool", new_callable=mock.AsyncMock) as rt:
        with pytest.raises(resume_service.RemoteResumeError):
            await resume_service.resume_remote_command(
                db, command=_command(db, dev_id, "cmd-fx"), device=device,
                jarvis_session_id=jarvis_session_id, approval_status="approved",
            )
    rt.assert_not_awaited()


# ---------------------------------------------------------------------------
# Endpoint: respond decide comando remoto e só aplica após sucesso
# ---------------------------------------------------------------------------

async def test_endpoint_respond_remote_dicts_applied_only_on_success(db, client):
    from app.models import ApprovalRequest
    from app.models.remote import Device
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.tools import ToolResult

    dev_id, token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, "cmd-ep")
    approval_id = result["approval_id"]
    device = db.get(Device, dev_id)
    get_or_create_jarvis_session(db, device)

    headers = {"Authorization": f"Bearer {token}"}
    with mock.patch("app.remote.resume._run_tool", new_callable=mock.AsyncMock) as rt:
        rt.return_value = ToolResult.success("ok")
        resp = client.post(
            f"/api/approvals/{approval_id}/respond", json={"approved": True}, headers=headers
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "decided"
    assert body["command_id"] == "cmd-ep"

    cmd = _command(db, dev_id, "cmd-ep")
    db.refresh(cmd)
    assert cmd.status == RemoteCommandStatus.EXECUTED.value

    approval = db.get(ApprovalRequest, approval_id)
    assert approval.applied is True  # applied apenas após sucesso


async def test_endpoint_respond_remote_concurrent_second_not_decided(db, client):
    """Decisão concorrente (B) sobre comando remoto não cai no loop local."""
    from app.models import ApprovalRequest
    from app.models.remote import Device
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.tools import ToolResult

    dev_id, token = _paired_device(db)
    result = await _queue_l2_command(db, dev_id, "cmd-cc")
    approval_id = result["approval_id"]
    device = db.get(Device, dev_id)
    get_or_create_jarvis_session(db, device)

    headers = {"Authorization": f"Bearer {token}"}
    with mock.patch("app.remote.resume._run_tool", new_callable=mock.AsyncMock) as rt:
        rt.return_value = ToolResult.success("ok")

        # Primeira decisão (efetiva).
        first = client.post(
            f"/api/approvals/{approval_id}/respond", json={"approved": True}, headers=headers
        )
        # Segunda decisão concorrente: comando já processado → 409 (não re-executa).
        second = client.post(
            f"/api/approvals/{approval_id}/respond", json={"approved": True}, headers=headers
        )

    assert first.status_code == 200
    assert first.json()["command_id"] == "cmd-cc"
    assert second.status_code == 409
    # A tool rodou exatamente UMA vez (a primeira decisão).
    assert rt.await_count == 1

    approval = db.get(ApprovalRequest, approval_id)
    db.refresh(approval)
    assert approval.applied is True
