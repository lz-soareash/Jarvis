"""Proactive Agent (Fase 17) — testes da camada de proatividade controlada.

Categorias exigidas pela fase: eventos/dedup/sanitização, scheduler (persistente,
restart/timezone/desabilitado), policy (quiet hours, cooldown, rate, prioridade),
delivery (Web SSE/Remote/offline), idempotência, segurança (EXECUTE nunca além
do Permission Engine), spam (100 eventos ≠ 100 notificações), LLM-off, falhas
(worker/tick isolados) e API + Central.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.models.proactive import ProactiveInboxEvent, ProactiveMessage
from app.models.remote import Device
from app.models.session import utcnow

UTC = timezone.utc


def _enable(monkeypatch, *, scheduler: bool = False, **extra) -> None:
    """Liga a capacidade proativa com defaults determinísticos para testes."""
    monkeypatch.setattr(settings, "proactive_enabled", True)
    monkeypatch.setattr(settings, "proactive_scheduler_enabled", scheduler)
    # "00:00-00:00" = start == end ⇒ nunca quiet (determinístico).
    monkeypatch.setattr(settings, "proactive_quiet_hours", "00:00-00:00")
    for key, value in extra.items():
        monkeypatch.setattr(settings, key, value)
    from app.proactive.engine import reset_engine

    reset_engine()


def _always_quiet(monkeypatch) -> None:
    # start 1320 > end 1319 ⇒ cobre todos os minutos (sempre quiet).
    monkeypatch.setattr(settings, "proactive_quiet_hours", "22:00-21:59")


def _inbox_row(db, event_id: str) -> ProactiveInboxEvent | None:
    return db.scalars(
        select(ProactiveInboxEvent).where(ProactiveInboxEvent.event_id == event_id)
    ).first()


def _message_count(db) -> int:
    return db.scalar(select(func.count()).select_from(ProactiveMessage)) or 0


# --------------------------------------------------------------------------
# Eventos: validação, sanitização e dedup
# --------------------------------------------------------------------------


def test_emit_rejects_invalid_event_type():
    from app.proactive.events import emit

    assert emit("SEM_NAMESPACE") is None
    assert emit("Caixa.Alta") is None


def test_event_type_catalog_is_open():
    from app.proactive.events import is_valid_event_type

    assert is_valid_event_type("custom.byte_stream")
    assert is_valid_event_type("remote.device_connected")


def test_emit_sanitizes_secret_payload(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.events import emit

    eid = emit(
        "system.ready",
        payload={
            "title": "T",
            "token": "abc",
            "api_key": "x",
            "password": "p",
            "nested": {"secret": "s", "ok": 1},
            "big": list(range(200)),
            "deep": {"a": {"b": {"c": {"d": 1}}}},
            "num": 7,
            "flag": True,
        },
    )
    row = _inbox_row(db_session, eid)
    assert row is not None
    payload = row.payload()
    assert "token" not in payload
    assert "api_key" not in payload
    assert "password" not in payload
    assert payload["title"] == "T"
    assert payload["num"] == 7
    assert payload["flag"] is True
    assert payload["nested"] == {"ok": 1}
    assert len(payload["big"]) <= 32
    assert payload.get("deep") == {"a": {"b": {}}} or "deep" not in payload


def test_emit_duplicate_event_id_is_deduped(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.events import emit

    eid = "dedup-1"
    first = emit("system.ready", event_id=eid)
    second = emit("system.ready", event_id=eid)
    assert first == eid
    assert second is None
    rows = db_session.scalars(
        select(ProactiveInboxEvent).where(ProactiveInboxEvent.event_id == eid)
    ).all()
    assert len(rows) == 1


def test_same_event_never_double_notifies(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.events import emit

    emit("system.ready", event_id="n-1")
    emit("system.ready", event_id="n-1")
    assert _message_count(db_session) == 1


# --------------------------------------------------------------------------
# Policy determinística
# --------------------------------------------------------------------------


def test_disabled_setting_ignores_everything(db_session):
    from app.proactive.events import emit

    assert settings.proactive_enabled is False
    emit("system.ready")
    row = db_session.scalars(select(ProactiveInboxEvent)).first()
    assert row is not None
    assert row.decision == "ignore"
    assert row.delivered is True
    assert _message_count(db_session) == 0


def test_low_priority_never_notifies(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.events import emit

    emit("system.ready", priority="low")
    row = db_session.scalars(select(ProactiveInboxEvent)).first()
    assert row.decision == "ignore"
    assert _message_count(db_session) == 0


def test_unknown_event_type_is_ignored(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.events import emit

    emit("mystery.namespace")
    row = db_session.scalars(select(ProactiveInboxEvent)).first()
    assert row.decision == "ignore"


def test_quiet_hours_defers_notifications(db_session, monkeypatch):
    _enable(monkeypatch)
    _always_quiet(monkeypatch)
    from app.proactive.events import emit

    emit("system.ready")
    row = db_session.scalars(select(ProactiveInboxEvent)).first()
    assert row.decision == "defer"
    assert row.delivered is False
    assert row.next_evaluation_at is not None
    assert _message_count(db_session) == 0


def test_quiet_hours_critical_only_with_optin_flag(db_session, monkeypatch):
    _enable(monkeypatch)
    _always_quiet(monkeypatch)
    assert settings.proactive_interrupt_on_critical is False
    from app.proactive.events import emit

    emit("system.shutdown", priority="critical")
    assert (
        db_session.scalars(select(ProactiveInboxEvent)).first().decision == "defer"
    )
    assert _message_count(db_session) == 0

    monkeypatch.setattr(settings, "proactive_interrupt_on_critical", True)
    emit("system.shutdown", priority="critical")
    assert _message_count(db_session) == 1


def test_in_quiet_hours_unit_and_timezone():
    from app.proactive.policy import in_quiet_hours

    # offset -180 (BRT): 05:00Z ⇒ 02:00 local ⇒ dentro de 22:00-07:00.
    assert in_quiet_hours(datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
                          quiet_hours="22:00-07:00", offset_minutes=-180) is True
    assert in_quiet_hours(datetime(2026, 1, 1, 14, 0, tzinfo=UTC),
                          quiet_hours="22:00-07:00", offset_minutes=-180) is False
    # Cruzando meia-noite (offset 0).
    assert in_quiet_hours(datetime(2026, 1, 1, 23, 30, tzinfo=UTC),
                          quiet_hours="22:00-07:00", offset_minutes=0) is True
    assert in_quiet_hours(datetime(2026, 1, 1, 6, 0, tzinfo=UTC),
                          quiet_hours="22:00-07:00", offset_minutes=0) is True


def test_cooldown_limits_same_event_type(db_session, monkeypatch):
    _enable(monkeypatch, proactive_default_cooldown_seconds=600)
    from app.proactive.events import emit

    emit("system.ready")
    emit("system.ready")
    assert _message_count(db_session) == 1
    rows_db = db_session.scalars(select(ProactiveInboxEvent)).all()
    assert [r.decision for r in rows_db] == ["notify", "defer"]


def test_rate_limit_defers_third_notification(db_session, monkeypatch):
    _enable(
        monkeypatch,
        proactive_max_per_hour=2,
        proactive_max_per_day=100,
    )
    from app.proactive.events import emit

    emit("system.ready")
    emit("task.completed")
    emit("project.test_passed")
    assert _message_count(db_session) == 2
    rows = db_session.scalars(
        select(ProactiveInboxEvent).order_by(ProactiveInboxEvent.received_at)
    ).all()
    assert [r.decision for r in rows] == ["notify", "notify", "defer"]


# --------------------------------------------------------------------------
# Scheduler persistente
# --------------------------------------------------------------------------


def test_interval_schedule_fires_once(db_session, monkeypatch):
    _enable(monkeypatch, scheduler=True)
    from app.proactive.scheduler import create_schedule, fire_due

    schedule, err = create_schedule(
        db_session, name="r", kind="interval", spec="60"
    )
    assert err is None
    now = utcnow()
    fired = fire_due(db_session, now_utc=now + timedelta(seconds=70))
    assert fired == 1
    assert schedule.run_count == 1
    assert schedule.last_run_at is not None
    assert schedule.next_run_at > now + timedelta(seconds=60)
    assert _message_count(db_session) == 1  # proactive.scheduled ∈ NOTIFY
    assert fire_due(db_session, now_utc=now + timedelta(seconds=70)) == 0


def test_oneshot_schedule_disables_after_fire(db_session, monkeypatch):
    _enable(monkeypatch, scheduler=True)
    from app.proactive.scheduler import create_schedule, fire_due

    spec = (utcnow() - timedelta(seconds=1)).isoformat()
    schedule, err = create_schedule(db_session, name="once", kind="oneshot", spec=spec)
    assert err is None
    now = utcnow()
    assert fire_due(db_session, now_utc=now) == 1
    assert schedule.enabled is False
    assert schedule.next_run_at is None
    assert fire_due(db_session, now_utc=now + timedelta(hours=1)) == 0


def test_cron_schedule_timezone(db_session, monkeypatch):
    _enable(monkeypatch, scheduler=True)
    from app.proactive.scheduler import create_schedule, next_cron_run

    # offset +60: 09:30 local ⇒ 08:30 UTC. Determinístico via `next_cron_run`.
    assert next_cron_run(
        "09:30",
        now_utc=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        offset_minutes=60,
    ) == datetime(2026, 1, 1, 8, 30, tzinfo=UTC)
    # Já passou hoje local ⇒ próximo é amanhã.
    assert next_cron_run(
        "09:30",
        now_utc=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        offset_minutes=60,
    ) == datetime(2026, 1, 2, 8, 30, tzinfo=UTC)

    schedule, err = create_schedule(
        db_session, name="cron", kind="cron", spec="09:30", tz_offset_minutes=60
    )
    assert err is None
    assert schedule.next_run_at is not None
    assert schedule.next_run_at.replace(tzinfo=UTC) > datetime.now(timezone.utc)


def test_scheduler_disabled_does_not_fire(db_session, monkeypatch):
    _enable(monkeypatch, scheduler=False)
    from app.proactive.scheduler import create_schedule, fire_due

    create_schedule(db_session, name="off", kind="interval", spec="1")
    assert fire_due(db_session, now_utc=utcnow() + timedelta(days=1)) == 0
    assert _message_count(db_session) == 0


def test_schedule_persists_across_restart_and_catches_up(monkeypatch):
    _enable(monkeypatch, scheduler=True)
    from app.db.session import SessionLocal
    from app.proactive.scheduler import create_schedule, fire_due, list_schedules

    db1 = SessionLocal()
    spec = (utcnow() - timedelta(seconds=5)).isoformat()  # venceu "durante o crash"
    create_schedule(db1, name="post-crash", kind="oneshot", spec=spec)
    db1.close()

    # "restart": estado reconstruído apenas do banco (memória zerada).
    from app.proactive.engine import reset_engine

    reset_engine()
    db2 = SessionLocal()
    schedules = list_schedules(db2)
    assert len(schedules) == 1  # sobreviveu ao restart
    assert schedules[0].enabled is True
    assert fire_due(db2, now_utc=utcnow()) == 1  # catch-up pós-restart
    assert schedules[0].enabled is False  # one-shot executado
    # Segunda avaliação não refaz nada (sem dupla execução).
    assert fire_due(db2, now_utc=utcnow() + timedelta(hours=2)) == 0
    db2.commit()
    db2.close()


def test_deferred_sweep_promotes_to_notify(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.scheduler import deferred_sweep

    row = ProactiveInboxEvent(
        event_id="defer-1",
        event_type="system.ready",
        source="system",
        priority="normal",
        decision="defer",
        delivered=False,
        next_evaluation_at=utcnow() - timedelta(seconds=1),
    )
    db_session.add(row)
    db_session.commit()
    assert deferred_sweep(db_session, now_utc=utcnow()) == 1
    db_session.commit()
    db_session.expire_all()
    assert _message_count(db_session) == 1
    assert row.decision == "notify"


# --------------------------------------------------------------------------
# Delivery (Web SSE / Remote / offline)
# --------------------------------------------------------------------------


def test_web_delivery_replay_for_late_subscriber(monkeypatch):
    _enable(monkeypatch)
    from app.proactive.events import emit, proactive_event_stream

    emit("system.ready", payload={"title": "Pronto", "message": "olá"})

    async def grab() -> list[str]:
        types = []
        async for entry in proactive_event_stream(limit=2):
            types.append(entry.get("type"))
        return types

    types = asyncio.run(grab())
    assert "proactive.message" in types
    assert "proactive.connected" in types


def test_delivery_persists_web_flag_only(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.events import emit

    assert settings.remote_enabled is False
    emit("system.ready")
    msg = db_session.scalars(select(ProactiveMessage)).first()
    assert msg is not None
    assert msg.delivered_web_at is not None
    assert msg.delivered_remote_at is None
    assert msg.status == "delivered_web"


def test_delivery_remote_when_remote_enabled(db_session, monkeypatch):
    _enable(monkeypatch, remote_enabled=True)
    from app.proactive.events import emit

    emit("system.ready")
    msg = db_session.scalars(select(ProactiveMessage)).first()
    assert msg is not None
    assert msg.delivered_remote_at is not None
    assert msg.status == "delivered"
    from app.remote.events import _broker as remote_broker

    types = [entry.get("type") for entry in remote_broker._replay]
    assert "proactive.message" in types


def test_deliver_is_idempotent_by_dedup_key(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.delivery import deliver

    class _Event:
        event_id = "idem-1"
        event_type = "demo.generated"
        priority = "normal"

    e = _Event()
    first = deliver(db_session, event=e, decision="notify", title="T", content="C")
    second = deliver(db_session, event=e, decision="notify", title="T", content="C")
    assert first.created is True
    assert second.created is False
    assert _message_count(db_session) == 1


def test_expire_old_messages(db_session, monkeypatch):
    _enable(monkeypatch, proactive_message_ttl_seconds=1)
    from app.proactive.delivery import expire_old

    msg = ProactiveMessage(
        dedup_key="exp-1",
        event_type="system.ready",
        decision="notify",
        priority="normal",
        title="T",
        content="C",
        expires_at=utcnow() - timedelta(seconds=5),
    )
    db_session.add(msg)
    db_session.commit()
    assert expire_old(db_session) == 1
    db_session.expire_all()
    assert msg.status == "expired"


# --------------------------------------------------------------------------
# Segurança: EXECUTE nunca além do Permission Engine
# --------------------------------------------------------------------------


def test_unknown_tool_never_executes(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.models import AuditLog
    from app.proactive.events import emit

    emit("action.requested", payload={"tool": "tool_inexistente"})
    row = db_session.scalars(select(ProactiveInboxEvent)).first()
    assert row.decision == "ignore"
    audits = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "proactive.blocked")
    ).all()
    assert len(audits) == 1
    assert audits[0].allowed is False
    assert (
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "proactive.execute")
        ).first()
        is None
    )


def test_level2_tool_requires_ask(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.models import AuditLog
    from app.proactive.events import emit

    emit("action.requested", payload={"tool": "write_file"})
    row = db_session.scalars(select(ProactiveInboxEvent)).first()
    assert row.decision == "ask"
    assert _message_count(db_session) == 1  # mensagem acionável (sem auto-run)
    assert (
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "proactive.execute")
        ).first()
        is None
    )
    ask_audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "proactive.ask")
    ).first()
    assert ask_audit is not None and ask_audit.allowed is None


def test_level3_tool_never_executes(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.models import AuditLog
    from app.proactive.events import emit

    emit("action.requested", payload={"tool": "shutdown_computer"})
    row = db_session.scalars(select(ProactiveInboxEvent)).first()
    assert row.decision == "ask"
    assert (
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "proactive.execute")
        ).first()
        is None
    )


def test_level1_tool_executes_and_audits(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.models import AuditLog
    from app.proactive.events import emit

    emit("action.requested", payload={"tool": "read_file"})
    row = db_session.scalars(select(ProactiveInboxEvent)).first()
    assert row.decision == "execute"
    execute_audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "proactive.execute")
    ).first()
    assert execute_audit is not None
    assert execute_audit.allowed is True


def test_revoked_device_never_executes(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.core.enums import DeviceStatus
    from app.models import AuditLog
    from app.proactive.events import emit

    db_session.add(Device(id="dev-revogado", name="d", status=DeviceStatus.REVOKED.value))
    db_session.commit()
    emit(
        "action.requested",
        payload={"tool": "read_file"},
        device_id="dev-revogado",
    )
    row = db_session.scalars(select(ProactiveInboxEvent)).first()
    assert row.decision == "ignore"
    assert (
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "proactive.execute")
        ).first()
        is None
    )
    blocked = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "proactive.blocked")
    ).first()
    assert blocked is not None and blocked.allowed is False


# --------------------------------------------------------------------------
# Anti-spam e LLM-off
# --------------------------------------------------------------------------


def test_spam_100_events_single_notification(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.events import emit

    for _ in range(100):
        emit("task.failed", payload={"task_id": str(uuid.uuid4())})
    assert _message_count(db_session) == 1
    rows = db_session.scalars(select(ProactiveInboxEvent)).all()
    assert len(rows) == 100


def test_deterministic_path_never_calls_llm(db_session, monkeypatch):
    _enable(monkeypatch)
    from app.proactive.engine import llm_invocations
    from app.proactive.events import emit

    emit("system.ready")
    emit("task.completed")
    assert _message_count(db_session) == 2
    assert llm_invocations() == 0
    from app.proactive.observer import proactive_stats

    assert proactive_stats(db_session)["llm_invocations"] == 0


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_proactive_status_endpoint(client):
    res = client.get("/api/proactive/status")
    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is False
    assert "scheduler_enabled" in data
    assert "events_received" in data
    assert data["llm_invocations"] == 0


def test_schedule_crud_api(client, monkeypatch):
    _enable(monkeypatch, scheduler=True)
    payload = {
        "name": "relatório",
        "kind": "interval",
        "spec": "3600",
        "event_type": "proactive.scheduled",
        "priority": "normal",
    }
    created = client.post("/api/proactive/schedules", json=payload)
    assert created.status_code == 201
    sched = created.json()
    assert sched["id"]
    assert sched["next_run_at"] is not None

    listing = client.get("/api/proactive/schedules")
    assert listing.status_code == 200
    assert any(s["id"] == sched["id"] for s in listing.json())

    patched = client.patch(
        f"/api/proactive/schedules/{sched['id']}", json={"enabled": False}
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    deleted = client.delete(f"/api/proactive/schedules/{sched['id']}")
    assert deleted.status_code == 204
    gone = client.delete(f"/api/proactive/schedules/{sched['id']}")
    assert gone.status_code == 404


def test_schedule_api_validation(client):
    assert (
        client.post(
            "/api/proactive/schedules",
            json={"name": "x", "kind": "weekly", "spec": "1"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/proactive/schedules",
            json={"name": "x", "kind": "interval", "spec": "0"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/proactive/schedules",
            json={"name": "x", "kind": "interval", "spec": "1",
                  "event_type": "Invalido"},
        ).status_code
        == 422
    )


def test_schedule_mutations_are_audited(client, monkeypatch):
    _enable(monkeypatch)
    from app.models import AuditLog

    sid = client.post(
        "/api/proactive/schedules",
        json={"name": "a", "kind": "interval", "spec": "3600"},
    ).json()["id"]
    client.patch(f"/api/proactive/schedules/{sid}", json={"name": "b"})
    client.delete(f"/api/proactive/schedules/{sid}")
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        actions = [
            a.action
            for a in db.scalars(
                select(AuditLog).where(AuditLog.action.like("proactive.schedule.%"))
            ).all()
        ]
    assert actions == [
        "proactive.schedule.create",
        "proactive.schedule.update",
        "proactive.schedule.delete",
    ]


def test_ops_overview_includes_proactive_block(client):
    res = client.get("/api/ops/overview")
    assert res.status_code == 200
    proactive = res.json()["proactive"]
    assert proactive["enabled"] is False
    assert "events_received" in proactive
    assert "schedules_total" in proactive
    assert "messages_total" in proactive


# --------------------------------------------------------------------------
# Stream SSE + falhas
# --------------------------------------------------------------------------


async def _probe_sse_endpoint() -> list[str]:
    from app.proactive.api import proactive_stream

    response = await proactive_stream()
    assert response.media_type == "text/event-stream"
    chunks: list[str] = []
    iterator = response.body_iterator.__aiter__()
    try:
        for _ in range(3):
            chunk = await asyncio.wait_for(
                response.body_iterator.__anext__(), timeout=1.0
            )
            chunks.append(chunk)
    except (StopAsyncIteration, asyncio.TimeoutError):
        pass
    finally:
        await iterator.aclose()
    return chunks


def test_proactive_stream_endpoint_returns_sse_frames(monkeypatch):
    """Endpoint stream transmite replay + evento `connected` (sem bloquear)."""
    _enable(monkeypatch)
    from app.proactive.events import emit

    emit("system.ready", payload={"title": "Pronto", "message": "olá"})

    async def _run() -> list[str]:
        await asyncio.sleep(0.01)
        return await _probe_sse_endpoint()

    chunks = asyncio.run(_run())
    assert any("data: " in c for c in chunks)
    joined = "\n".join(chunks)
    assert "proactive.connected" in joined


def test_worker_loop_starts_and_cancels_cleanly():
    async def _run() -> None:
        from app.proactive.engine import worker_loop

        task = asyncio.create_task(worker_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_tick_is_isolated_from_worker_crash(monkeypatch):
    _enable(monkeypatch, scheduler=True)
    import app.proactive.scheduler as scheduler_module
    from app.proactive.engine import tick

    def boom(*args, **kwargs):
        raise RuntimeError("crash simulado")

    monkeypatch.setattr(scheduler_module, "fire_due", boom)
    result = tick()
    assert isinstance(result, dict)
    assert "fired" in result