"""Testes da Fase 27 — Multi-turn Agentic Context (backend).

Cobre a camada determinística de contexto no Core:

Parte A — `Session.context_json`: gravação/upsert/leitura/limpeza do bloco
`device` via `turn_context.record_device_outcome`; best-effort sem nome.

Parte B — frescura/TTL: `DeviceContext.fresh` verdadeiro p/ ação recente e falso
para contexto envelhecido (nenhuma continuidade automática).

Parte C — Active Task Context: derivado do modelo `AgentTask` (planned/running):
passos/status/próximos passos/última ferramenta; tarefas terminais NÃO voltam.

Parte D — Renderização sanitizada: `render_context_blocks` injeta só o NOME do
dispositivo (nunca id/transporte/secrets) e o objetivo da tarefa; vazio sem ctx.

Parte E — Continuidade determinística (`intent.detect_continuation`):
"Agora pesquisa FIAP" → `mobile_open_url` (Google Search) no mesmo dispositivo;
requisitos de frescura e de thread de navegação; sufixo "no meu celular".

Parte F — Integração: `build_system_prompt` injeta o bloco; e o fluxo real do
turno 1 ("abra o chrome no meu celular") persiste o contexto que habilita o
turno 2 ("agora pesquisa fiap").

Sem credenciais reais; testes herméticos (sem rede).
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from app.ai import turn_context
from app.ai.intent import detect_continuation
from app.models import AgentTask, Message, Session, utcnow
from app.remote import mobile_inbox

# ---------------------------------------------------------------------------
# Helpers e fixtures
# ---------------------------------------------------------------------------


def _add_device(db, **overrides):
    from app.models.remote import Device

    defaults: dict = {
        "id": "dev-galaxy",
        "name": "Galaxy A15",
        "device_type": "mobile",
        "status": "active",
        "platform": "android",
        "last_seen_at": utcnow(),
    }
    defaults.update(overrides)
    device = Device(**defaults)
    db.add(device)
    db.commit()
    return device


def _make_session(db) -> Session:
    session = Session(title="Fase 27")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


async def _auto_resolve_lan(device_id, *, status="success", result=None):
    inbox = mobile_inbox.get_mobile_inbox()
    for _ in range(300):
        pending = inbox.pending_for(device_id)
        if pending:
            inbox.submit_result(
                device_id=device_id,
                command_id=pending[0]["command_id"],
                status=status,
                result=result,
            )
            return pending[0]["command_id"]
        await asyncio.sleep(0.01)
    return None


def _outcome_payload(db, session_id: str, device: str = "Galaxy A15"):
    turn_context.record_device_outcome(
        db,
        session_id,
        device_id="dev-galaxy",
        device_name=device,
        capability="OPEN_APP",
        status="success",
        transport="lan",
        summary="abertura de app de Galaxy A15: sucesso.",
    )


async def _drain(gen):
    return [item async for item in gen]


def _run_turn_with_feeder(db, provider, session_id, text, result):
    async def _flow():
        feeder = asyncio.create_task(
            _auto_resolve_lan("dev-galaxy", result=result)
        )
        events = await _drain(_run_agent(session_id, provider, db, text))
        await feeder
        return events

    return asyncio.run(_flow())


def _run_agent(session_id, provider, db, text):
    from app.services.agent import run_agent

    return run_agent(session_id, provider, db, text)


# ---------------------------------------------------------------------------
# Parte A — Session.context_json (gravação/leitura/limpeza)
# ---------------------------------------------------------------------------


def test_record_device_outcome_writes_session_context(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    data = json.loads(session.context_json)
    assert data["device"]["name"] == "Galaxy A15"
    assert data["device"]["capability"] == "OPEN_APP"
    assert data["device"]["status"] == "success"
    assert data["device"]["transport"] == "lan"
    assert data["device"]["id"] == "dev-galaxy"


def test_device_context_read_back(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    ctx = turn_context.device_context(db_session, session.id)
    assert ctx is not None
    assert ctx.name == "Galaxy A15"
    assert ctx.capability == "OPEN_APP"
    assert ctx.fresh is True


def test_record_outcome_upserts_replace_previous(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id, device="Galaxy A15")
    turn_context.record_device_outcome(
        db_session,
        session.id,
        device_id="dev-moto",
        device_name="Moto G84",
        capability="BATTERY_STATUS",
        status="success",
        transport=None,
        summary="bateria de Moto G84: sucesso.",
    )
    ctx = turn_context.device_context(db_session, session.id)
    assert ctx.name == "Moto G84"
    assert ctx.capability == "BATTERY_STATUS"
    assert "dev-galaxy" not in session.context_json  # não acumula histórico


def test_record_outcome_without_name_is_ignored(db_session):
    session = _make_session(db_session)
    turn_context.record_device_outcome(
        db_session,
        session.id,
        device_id="dev-x",
        device_name="",
        capability="DEVICE_INFO",
        status="success",
        transport=None,
        summary="",
    )
    assert session.context_json is None or "device" not in json.loads(
        session.context_json or "{}"
    )


def test_record_outcome_no_session_is_ignored(db_session):
    turn_context.record_device_outcome(
        db_session,
        "sess-inexistente",
        device_id="dev-x",
        device_name="Moto",
        capability="DEVICE_INFO",
        status="success",
        transport=None,
        summary="",
    )  # não levanta


def test_clear_device_context_removes_block(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    assert turn_context.device_context(db_session, session.id) is not None
    turn_context.clear_device_context(db_session, session.id)
    assert turn_context.device_context(db_session, session.id) is None


# ---------------------------------------------------------------------------
# Parte B — Frescura / TTL
# ---------------------------------------------------------------------------


def test_device_context_stale_is_not_fresh(db_session):
    session = _make_session(db_session)
    stale_iso = (utcnow() - timedelta(seconds=1200)).isoformat()  # > TTL (600s)
    session.context_json = json.dumps(
        {
            "device": {
                "id": "dev-galaxy",
                "name": "Galaxy A15",
                "capability": "OPEN_APP",
                "status": "success",
                "transport": "lan",
                "summary": "antiga",
                "updated_at": stale_iso,
            }
        }
    )
    db_session.add(session)
    db_session.commit()
    ctx = turn_context.device_context(db_session, session.id)
    assert ctx is not None
    assert ctx.fresh is False


def test_device_context_fresh_within_ttl(db_session):
    session = _make_session(db_session)
    fresh_iso = utcnow().isoformat()
    session.context_json = json.dumps(
        {
            "device": {
                "id": "dev-galaxy",
                "name": "Galaxy A15",
                "capability": "OPEN_APP",
                "status": "success",
                "transport": "lan",
                "summary": "recente",
                "updated_at": fresh_iso,
            }
        }
    )
    db_session.add(session)
    db_session.commit()
    assert turn_context.device_context(db_session, session.id).fresh is True


# ---------------------------------------------------------------------------
# Parte C — Active Task Context (derivado de AgentTask)
# ---------------------------------------------------------------------------


def _make_active_task(db, session_id: str) -> AgentTask:
    plan = [
        {"tool": "web_research", "arguments": {}, "description": "Pesquisar fontes"},
        {"tool": "execute_command", "arguments": {}, "description": "Rodar build"},
        {"tool": "observe_computer", "arguments": {}, "description": "Verificar"},
    ]
    task = AgentTask(
        session_id=session_id,
        objective="entregar a feature X",
        status="running",
        steps_total=3,
        plan_json=json.dumps(plan, ensure_ascii=False),
        progress_json=json.dumps(
            [{"status": "done"}, {"status": "pending"}, {"status": "pending"}]
        ),
        steps_done=1,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_active_task_none_when_no_task(db_session):
    session = _make_session(db_session)
    assert turn_context.active_task_context(db_session, session.id) is None


def test_active_task_derives_steps_and_current_step(db_session):
    session = _make_session(db_session)
    task = _make_active_task(db_session, session.id)
    ctx = turn_context.active_task_context(db_session, session.id)
    assert ctx is not None
    assert ctx.task_id == task.id
    assert ctx.objective == "entregar a feature X"
    assert ctx.status == "running"
    assert ctx.steps_total == 3
    assert ctx.steps_done == 1
    assert ctx.current_step == 2  # primeiro pendente = passo 2
    assert ctx.next_steps == ["Rodar build", "Verificar"]
    assert ctx.last_tool == "web_research"
    assert ctx.last_result == "done"


def test_active_task_excludes_terminal_tasks(db_session):
    session = _make_session(db_session)
    for status in ("completed", "failed", "cancelled"):
        task = AgentTask(
            session_id=session.id,
            objective="tarefa %s" % status,
            status=status,
            steps_total=1,
            plan_json=json.dumps(
                [{"tool": "execute_command", "arguments": {}, "description": "x"}]
            ),
        )
        db_session.add(task)
    db_session.commit()
    assert turn_context.active_task_context(db_session, session.id) is None


def test_load_turn_context_bundles_device_and_task(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    _make_active_task(db_session, session.id)
    ctx = turn_context.load_turn_context(db_session, session.id)
    assert ctx.device is not None and ctx.device.name == "Galaxy A15"
    assert ctx.active_task is not None and ctx.active_task.status == "running"


# ---------------------------------------------------------------------------
# Parte D — Renderização sanitizada
# ---------------------------------------------------------------------------


def test_render_blocks_include_device_name_not_id(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    blocks = turn_context.system_context_block(db_session, session.id)
    joined = "\n".join(blocks)
    assert "[Continuidade de dispositivo]" in joined
    assert "Galaxy A15" in joined
    assert "dev-galaxy" not in joined  # id NUNCA chega ao prompt
    assert "lan" not in joined  # transporte não vai ao prompt


def test_render_blocks_include_active_task(db_session):
    session = _make_session(db_session)
    _make_active_task(db_session, session.id)
    blocks = turn_context.system_context_block(db_session, session.id)
    joined = "\n".join(blocks)
    assert "[Tarefa ativa]" in joined
    assert "entregar a feature X" in joined
    assert "Rodar build" in joined


def test_render_blocks_empty_without_context(db_session):
    session = _make_session(db_session)
    assert turn_context.system_context_block(db_session, session.id) == []


def test_render_failed_outcome_does_not_claim_continuity(db_session):
    """Fase 27.2 (F7) — resultado NÃO-sokcess não é assumido como sucesso."""
    session = _make_session(db_session)
    turn_context.record_device_outcome(
        db_session,
        session.id,
        device_id="dev-galaxy",
        device_name="Galaxy A15",
        capability="OPEN_URL",
        status="timeout",
        transport="lan",
        summary="abertura de URL não respondeu a tempo (TIMEOUT).",
    )
    blocks = turn_context.system_context_block(db_session, session.id)
    joined = "\n".join(blocks)
    assert "[Continuidade de dispositivo]" not in joined
    assert "[Dispositivo]" in joined
    assert "Galaxy A15" in joined
    assert "não foi concluída com sucesso" in joined
    assert "Não assuma continuidade" in joined


@pytest.mark.parametrize(
    "status",
    ["failed", "timeout", "denied", "unsupported", "cancelled"],
)
def test_render_non_success_status_never_reuses_continuity_block(db_session, status):
    session = _make_session(db_session)
    turn_context.record_device_outcome(
        db_session,
        session.id,
        device_id="dev-galaxy",
        device_name="Galaxy A15",
        capability="OPEN_APP",
        status=status,
        transport="lan",
        summary="resumo",
    )
    joined = "\n".join(turn_context.system_context_block(db_session, session.id))
    assert "[Continuidade de dispositivo]" not in joined


def test_render_success_keeps_continuity_block(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)  # status=success
    joined = "\n".join(turn_context.system_context_block(db_session, session.id))
    assert "[Continuidade de dispositivo]" in joined
    assert "Galaxy A15" in joined


def test_render_blocks_never_expose_device_id_or_tokens(db_session):
    """Fase 27.2 (F8) — device_id/token/credential jamais chegam ao prompt."""
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    joined = "\n".join(turn_context.system_context_block(db_session, session.id)).lower()
    assert "dev-galaxy" not in joined
    assert "token" not in joined
    assert "credential" not in joined
    assert "bearer" not in joined


# ---------------------------------------------------------------------------
# Parte E — Continuidade determinística
# ---------------------------------------------------------------------------


def test_continuation_requires_device_context():
    assert detect_continuation("agora pesquisa fiap", None) is None


def test_continuation_requires_fresh_device(db_session):
    session = _make_session(db_session)
    session.context_json = json.dumps(
        {
            "device": {
                "id": "dev-galaxy",
                "name": "Galaxy A15",
                "capability": "OPEN_APP",
                "status": "success",
                "transport": "lan",
                "summary": "",
                "updated_at": (utcnow() - timedelta(days=365)).isoformat(),
            }
        }
    )
    db_session.add(session)
    db_session.commit()
    ctx = turn_context.load_turn_context(db_session, session.id)
    assert ctx.device is not None and ctx.device.fresh is False
    assert detect_continuation("agora pesquisa fiap", ctx) is None


def test_continuation_agora_search_maps_device(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    ctx = turn_context.load_turn_context(db_session, session.id)
    match = detect_continuation("agora pesquisa fiap", ctx)
    assert match is not None
    assert match.tool_call.name == "mobile_open_url"
    args = match.tool_call.arguments
    assert args["device"] == "Galaxy A15"
    assert args["url"].startswith("https://www.google.com/search?q=")
    assert "fiap" in args["url"]


def test_continuation_search_without_agora_on_browser_thread(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)  # capability OPEN_APP → thread web
    ctx = turn_context.load_turn_context(db_session, session.id)
    match = detect_continuation("pesquisa notícias de hoje", ctx)
    assert match is not None
    assert match.tool_call.name == "mobile_open_url"


def test_continuation_search_without_agora_off_browser_thread_is_none(db_session):
    session = _make_session(db_session)
    turn_context.record_device_outcome(
        db_session,
        session.id,
        device_id="dev-galaxy",
        device_name="Galaxy A15",
        capability="BATTERY_STATUS",
        status="success",
        transport="lan",
        summary="bateria",
    )
    ctx = turn_context.load_turn_context(db_session, session.id)
    assert detect_continuation("pesquisa inteligência artificial", ctx) is None


def test_continuation_site_only_with_agora(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    ctx = turn_context.load_turn_context(db_session, session.id)
    match = detect_continuation("agora abre o youtube", ctx)
    assert match is not None
    assert match.tool_call.name == "mobile_open_url"
    assert match.tool_call.arguments["url"] == "https://youtube.com"
    assert match.tool_call.arguments["device"] == "Galaxy A15"


def test_continuation_site_without_agora_is_none(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    ctx = turn_context.load_turn_context(db_session, session.id)
    assert detect_continuation("abre o youtube", ctx) is None


def test_continuation_strips_device_tail(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    ctx = turn_context.load_turn_context(db_session, session.id)
    match = detect_continuation("agora pesquisa fiap no meu celular", ctx)
    assert match is not None
    url = match.tool_call.arguments["url"]
    assert "celular" not in url
    assert "fiap" in url


def test_continuation_agora_open_app_spotify(db_session):
    # Fase 27.1 — "agora abre o spotify" continua no MESMO dispositivo.
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)  # OPEN_APP → thread de navegação
    ctx = turn_context.load_turn_context(db_session, session.id)
    match = detect_continuation("agora abre o spotify", ctx)
    assert match is not None
    assert match.tool_call.name == "mobile_open_app"
    assert match.tool_call.arguments["package_name"] == "com.spotify.music"
    assert match.tool_call.arguments["device"] == "Galaxy A15"


def test_continuation_open_app_requires_agora(db_session):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)
    ctx = turn_context.load_turn_context(db_session, session.id)
    assert detect_continuation("abre o spotify", ctx) is None


def test_continuation_open_app_requires_browser_thread(db_session):
    session = _make_session(db_session)
    turn_context.record_device_outcome(
        db_session,
        session.id,
        device_id="dev-galaxy",
        device_name="Galaxy A15",
        capability="BATTERY_STATUS",
        status="success",
        transport="lan",
        summary="bateria",
    )
    ctx = turn_context.load_turn_context(db_session, session.id)
    assert detect_continuation("agora abre o spotify", ctx) is None


# ---------------------------------------------------------------------------
# Parte F — Integração com o fluxo real (build_system_prompt + run_agent)
# ---------------------------------------------------------------------------


def test_build_system_prompt_injects_device_block(db_session, fake_ai):
    session = _make_session(db_session)
    _outcome_payload(db_session, session.id)

    from app.services.chat import build_system_prompt

    system = asyncio.run(
        build_system_prompt(db_session, session.id, fake_ai, query="olá")
    )
    assert "[Continuidade de dispositivo]" in system
    assert "Galaxy A15" in system
    assert "dev-galaxy" not in system


def test_turn1_records_device_then_turn2_continues(db_session, fake_ai):
    """E2E hermético: turno 1 abre Chrome no celular; contexto persiste e o
    turno 2 ("agora pesquisa fiap") é resolvido para o MESMO dispositivo."""
    session = _make_session(db_session)
    _add_device(db_session)

    events1 = _run_turn_with_feeder(
        db_session, fake_ai, session.id, "abra o chrome no meu celular",
        {"opened": "com.android.chrome"},
    )
    assert any('"mobile_open_app"' in ev for ev in events1)
    assert any('"type": "done"' in ev for ev in events1)

    # Contexto de dispositivo persistido pelo turno 1.
    ctx = turn_context.load_turn_context(db_session, session.id)
    assert ctx.device is not None and ctx.device.name == "Galaxy A15"
    assert ctx.device.capability == "OPEN_APP"

    # --- Turno 2: "agora pesquisa fiap" → continua no mesmo dispositivo ---
    events2 = _run_turn_with_feeder(
        db_session, fake_ai, session.id, "agora pesquisa fiap",
        {"opened": True},
    )
    assert any('"mobile_open_url"' in ev for ev in events2)
    assert any('"type": "done"' in ev for ev in events2)

    # A mensagem do turno 2 foi persistida (o loop terminou com done).
    messages = db_session.query(Message).filter_by(session_id=session.id).all()
    assert sum(1 for m in messages if m.role == "assistant") >= 1


def test_run_agent_continuation_uses_server_context(db_session, fake_ai):
    """Sem ação prévia de dispositivo, "agora pesquisa fiap" NÃO vira intenção
    determinística — o contexto está vazio e a ambiguidade fica com o fluxo."""
    session = _make_session(db_session)

    events = asyncio.run(_drain(_run_agent(session.id, fake_ai, db_session, "agora pesquisa fiap")))
    joined = "".join(events)
    assert '"mobile_open_url"' not in joined
    assert '"tool_start"' not in joined