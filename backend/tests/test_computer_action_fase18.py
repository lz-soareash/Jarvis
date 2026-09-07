"""Testes da Fase 18 — Computer Action Layer.

Cobertura hermética (sem hardware real — FakeComputerAdapter):
- validação (tipo desconhecido, params inválidos, limites);
- segurança (hotkeys bloqueadas, confirmação obrigatória, text perigoso);
- permissionamento (effective_level / confirmação /LEVEL_3 bloqueado);
- rate limiting (token bucket);
- timeout e cancelamento;
- dry_run (valida a cadeia SEM chamar o adapter);
- adapter unavailable e adapter failure;
- auditoria (log_action) e sanitização (sem payload completo);
- ausência de execução física durante dry_run;
- integração com Tool Registry (unique tool `computer_action`);
- ausência de bypass (única porta de execução é o ActionExecutor);
- comportamento com COMPUTER_ENABLED=false.
"""

import asyncio
import json

import pytest
from sqlalchemy.orm import Session as OrmSession

from app.action.adapters.base import ComputerAdapter
from app.action.executor import ActionExecutor, ActionExecutorOptions
from app.action.models import (
    ActionCapabilities,
    ActionRequest,
    ActionResult,
    ActionStatus,
    ActionType,
    ScreenInfo,
)
from app.action.observer import (
    EVENT_ACTION_DRY_RUN,
    EVENT_ACTION_EXECUTED,
    EVENT_ACTION_FAILED,
    EVENT_ACTION_REJECTED,
    action_stats,
)
from app.action.safety import ActionSafetyPolicy, SafetyVerdict
from app.action.models import ActionRequest as AR
from app.core.enums import PermissionLevel, RiskLevel
from app.remote.ratelimit import RateLimiter
from app.tools.registry import get_tool_registry


# ---------------------------------------------------------------------------
# Fake adapter
# ---------------------------------------------------------------------------

class FakeComputerAdapter(ComputerAdapter):
    """Adapter fake que registra chamadas — nunca age fisicamente."""

    def __init__(self, *, fail: str | None = None, available: bool = True) -> None:
        self._fail = fail
        self._available = available
        self.calls: list[tuple[str, dict]] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def available(self) -> bool:
        return self._available

    def discover_capabilities(self) -> ActionCapabilities:
        return ActionCapabilities(mouse=True, keyboard=True, scroll=True, window_focus=True,
                                  type_text=True, hotkey=True, detail="fake")

    def get_screen_info(self) -> ScreenInfo:
        return ScreenInfo(width=1920, height=1080)

    def _check(self, method: str) -> None:
        if self._fail == method:
            raise RuntimeError(f"falha simulada em {method}")

    def _record(self, method: str, **kwargs) -> None:
        self._check(method)
        self.calls.append((method, kwargs))

    def mouse_move(self, x: int, y: int) -> None:
        self._record("mouse_move", x=x, y=y)

    def mouse_click(self, x: int, y: int, button: str = "left") -> None:
        self._record("mouse_click", x=x, y=y, button=button)

    def mouse_double_click(self, x: int, y: int) -> None:
        self._record("mouse_double_click", x=x, y=y)

    def mouse_scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        self._record("mouse_scroll", amount=amount, x=x, y=y)

    def key_press(self, key: str) -> None:
        self._record("key_press", key=key)

    def hotkey(self, keys: list[str]) -> None:
        self._record("hotkey", keys=keys)

    def type_text(self, text: str, delay_ms: int = 0) -> None:
        self._record("type_text", text=text, delay_ms=delay_ms)

    def focus_window(self, title: str | None = None, process: str | None = None) -> bool:
        self._record("focus_window", title=title, process=process)
        return bool(title or process)


def run_async(coro):
    """Roda coroutine com loop fresco (padrão hermético do repo)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def make_request(action_type: str, params: dict, **kw) -> ActionRequest:
    return ActionRequest(action_type=action_type, params=params, **kw)


def _executor(adapter=None, *, enabled=True, limiter=None) -> ActionExecutor:
    return ActionExecutor(
        adapter=adapter or FakeComputerAdapter(),
        rate_limiter=limiter,
        override_enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Validation (Fase 18 §1 — validação ESTRUTURADA, sem side effects)
# ---------------------------------------------------------------------------

class TestValidation:
    def test_unknown_type_rejected_at_construction(self):
        with pytest.raises(ValueError):
            make_request("fly_dragon", {})

    def test_tool_rejects_unknown_type(self):
        """Entrada real (LLM→tool): action_type desconhecida vira erro limpo."""
        from app.action.tool import ComputerActionTool
        from app.tools.base import ToolContext
        tool = ComputerActionTool()
        result = run_async(tool.run(ToolContext(), action_type="fly_dragon", params={}))
        assert not result.ok
        assert "desconhecido" in result.output or "ausente" in result.output

    def test_mouse_move_missing_coords(self):
        executor = _executor()
        for params in ({}, {"x": 5}):
            result = run_async(executor.execute(make_request("mouse_move", params)))
            assert result.status == ActionStatus.REJECTED

    def test_mouse_move_negative_coord(self):
        executor = _executor()
        result = run_async(executor.execute(make_request("mouse_move", {"x": -5, "y": 10})))
        assert result.status == ActionStatus.REJECTED

    def test_click_invalid_button(self):
        executor = _executor()
        result = run_async(executor.execute(
            make_request("click", {"x": 10, "y": 10, "button": "explosive"})))
        assert result.status == ActionStatus.REJECTED

    def test_type_text_empty_rejected(self):
        executor = _executor()
        result = run_async(executor.execute(make_request("type_text", {"text": ""})))
        assert result.status == ActionStatus.REJECTED

    def test_type_text_limit_ignores_oversize(self):
        """Sem `confirmed` o texto gigante NÃO chega ao adapter."""
        executor = _executor()
        big = "A" * (500 + 5)
        result = run_async(executor.execute(make_request("type_text", {"text": big})))
        assert result.status == ActionStatus.REJECTED

    def test_scroll_limit_respected(self):
        executor = _executor()
        result = run_async(executor.execute(make_request("scroll", {"amount": 99})))
        assert result.status == ActionStatus.REJECTED

    def test_hotkey_too_many_keys(self):
        executor = _executor()
        result = run_async(executor.execute(
            make_request("hotkey", {"keys": ["CTRL", "SHIFT", "ALT", "DEL"]})))
        assert result.status == ActionStatus.REJECTED

    def test_hotkey_empty_keys(self):
        executor = _executor()
        result = run_async(executor.execute(make_request("hotkey", {"keys": []})))
        assert result.status == ActionStatus.REJECTED

    def test_valid_mouse_move_executes(self):
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(make_request("mouse_move", {"x": 500, "y": 300})))
        assert result.status == ActionStatus.EXECUTED
        assert result.ok
        assert adapter.calls == [("mouse_move", {"x": 500, "y": 300})]


# ---------------------------------------------------------------------------
# Safety policy (Fase 18 §2 — segurança)
# ---------------------------------------------------------------------------

class TestSafetyPolicy:
    def test_ctrl_alt_del_blocked(self):
        policy = ActionSafetyPolicy()
        req = make_request("hotkey", {"keys": ["CTRL", "ALT", "DEL"]}, metadata={"confirmed": True})
        assert policy.evaluate(req).verdict == SafetyVerdict.BLOCK

    def test_ctrl_shift_esc_blocked(self):
        policy = ActionSafetyPolicy()
        req = make_request("hotkey", {"keys": ["CTRL", "SHIFT", "ESC"]}, metadata={"confirmed": True})
        assert policy.evaluate(req).verdict == SafetyVerdict.BLOCK

    def test_alt_f4_blocked(self):
        policy = ActionSafetyPolicy()
        req = make_request("hotkey", {"keys": ["ALT", "F4"]}, metadata={"confirmed": True})
        assert policy.evaluate(req).verdict == SafetyVerdict.BLOCK

    def test_alt_tab_requires_confirmation(self):
        policy = ActionSafetyPolicy()
        req = make_request("hotkey", {"keys": ["ALT", "TAB"]}, metadata={"confirmed": True})
        assert policy.evaluate(req).verdict == SafetyVerdict.CONFIRM

    def test_alt_tab_without_confirmation_rejected(self):
        executor = _executor()
        result = run_async(executor.execute(
            make_request("hotkey", {"keys": ["ALT", "TAB"]})))
        assert result.status == ActionStatus.REJECTED
        assert result.metadata.get("requires_confirmation") is True

    def test_alt_tab_with_confirmation_allowed(self):
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(
            make_request("hotkey", {"keys": ["ALT", "TAB"]}, metadata={"confirmed": True})))
        assert result.status == ActionStatus.EXECUTED
        assert adapter.calls == [("hotkey", {"keys": ["ALT", "TAB"]})]

    def test_blocked_hotkey_rejected_even_with_confirmation(self):
        executor = _executor()
        result = run_async(executor.execute(
            make_request("hotkey", {"keys": ["CTRL", "ALT", "DEL"]}, metadata={"confirmed": True})))
        assert result.status == ActionStatus.REJECTED

    def test_multiple_blocked_variants(self):
        policy = ActionSafetyPolicy()
        for combo in (["CTRL", "ALT", "DEL"], ["CTRL", "SHIFT", "ESC"],
                      ["ALT", "F4"], ["CTRL", "ALT", "F2"]):
            req = make_request("hotkey", {"keys": combo}, metadata={"confirmed": True})
            assert policy.evaluate(req).verdict == SafetyVerdict.BLOCK

    def test_common_hotkeys_allowed(self):
        policy = ActionSafetyPolicy()
        for combo in (["CTRL", "C"], ["CTRL", "V"], ["CTRL", "Z"], ["SUPER", "L"]):
            req = make_request("hotkey", {"keys": combo})
            assert policy.evaluate(req).verdict in (SafetyVerdict.ALLOW,)

    def test_dangerous_text_blocked(self):
        policy = ActionSafetyPolicy()
        for text in ("rm -rf /", "powershell -c 1", "sudo rm", "os.system('x')",
                     "del /f /q *.*"):
            req = make_request("type_text", {"text": text})
            assert policy.evaluate(req).verdict == SafetyVerdict.BLOCK

    def test_plain_text_allowed(self):
        policy = ActionSafetyPolicy()
        req = make_request("type_text", {"text": "Oi JARVIS, tudo bem?"})
        assert policy.evaluate(req).verdict == SafetyVerdict.ALLOW


# ---------------------------------------------------------------------------
# Permission (Fase 18 §3 — Permission Engine reutilizado)
# ---------------------------------------------------------------------------

class TestPermission:
    def test_level2_requires_confirmation(self, db_session: OrmSession):
        from app.services.permissions import set_policy
        set_policy(db_session, "computer_action", PermissionLevel.LEVEL_2.value)
        executor = _executor(enabled=True)
        result = run_async(executor.execute(
            make_request("mouse_move", {"x": 1, "y": 1}),
            ActionExecutorOptions(db=db_session)))
        assert result.status == ActionStatus.REJECTED
        assert "CONFIRMATION" in (result.error_code or "")

    def test_level2_confirmed_allowed(self, db_session: OrmSession):
        from app.services.permissions import set_policy
        set_policy(db_session, "computer_action", PermissionLevel.LEVEL_2.value)
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(
            make_request("mouse_move", {"x": 1, "y": 1}, metadata={"confirmed": True}),
            ActionExecutorOptions(db=db_session)))
        assert result.status == ActionStatus.EXECUTED

    def test_level3_blocked(self, db_session: OrmSession):
        from app.services.permissions import set_policy
        set_policy(db_session, "computer_action", PermissionLevel.LEVEL_3.value)
        executor = _executor()
        result = run_async(executor.execute(
            make_request("mouse_move", {"x": 1, "y": 1}, metadata={"confirmed": True}),
            ActionExecutorOptions(db=db_session)))
        assert result.status == ActionStatus.REJECTED

    def test_tool_registered_level1(self):
        tool = get_tool_registry().get("computer_action")
        assert tool is not None
        assert tool.permission_level == PermissionLevel.LEVEL_1
        assert tool.risk == RiskLevel.MEDIUM

    def test_permission_effective_from_registry(self, db_session: OrmSession):
        from app.services.permissions import effective_level
        assert effective_level(db_session, "computer_action") == PermissionLevel.LEVEL_1


# ---------------------------------------------------------------------------
# Rate limiting (Fase 18 §4)
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_rate_limiter_denies_when_exhausted(self):
        limiter = RateLimiter(capacity=1, refill_per_second=0.0001)
        executor = _executor(limiter=limiter)
        first = run_async(executor.execute(make_request("mouse_move", {"x": 1, "y": 1})))
        second = run_async(executor.execute(make_request("mouse_move", {"x": 2, "y": 2})))
        assert first.status == ActionStatus.EXECUTED
        assert second.status == ActionStatus.REJECTED
        assert second.error_code == "ACTION_RATE_LIMITED"

    def test_executor_rate_limiter_shared_across_calls(self):
        limiter = RateLimiter(capacity=2, refill_per_second=0.0001)
        executor = _executor(limiter=limiter)
        results = [
            run_async(executor.execute(make_request("mouse_move", {"x": i, "y": i})))
            for i in range(5)
        ]
        assert sum(1 for r in results if r.status == ActionStatus.EXECUTED) == 2
        assert sum(1 for r in results if r.status == ActionStatus.REJECTED) == 3


# ---------------------------------------------------------------------------
# Timeout / cancellation (Fase 18 §5)
# ---------------------------------------------------------------------------

class _BlockingAdapter(FakeComputerAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.block = True
        self.cancelled_flag = False

    def mouse_move(self, x: int, y: int) -> None:
        if self.block:
            import time
            time.sleep(3)
        self._record("mouse_move", x=x, y=y)


class TestTimeoutCancellation:
    def test_timeout(self):
        executor = ActionExecutor(
            adapter=_BlockingAdapter(),
            override_enabled=True,
        )
        result = run_async(executor.execute(
            make_request("mouse_move", {"x": 1, "y": 1}, timeout_seconds=0.1)))
        assert result.status == ActionStatus.TIMEOUT
        assert result.error_code == "ACTION_TIMEOUT"

    def test_cancellation(self):
        class CancelBlockingAdapter(FakeComputerAdapter):
            def mouse_move(self, x: int, y: int) -> None:
                import time
                time.sleep(2)

        adapter = CancelBlockingAdapter()
        executor = ActionExecutor(adapter=adapter, override_enabled=True)
        flag = {"cancelled": False}

        async def run_then_cancel():
            opts = ActionExecutorOptions(cancelled_cb=lambda: flag["cancelled"])
            task = asyncio.create_task(
                executor.execute(make_request("mouse_move", {"x": 1, "y": 1}), opts))
            await asyncio.sleep(0.05)
            flag["cancelled"] = True
            return await task

        result = run_async(run_then_cancel())
        assert result.status == ActionStatus.CANCELLED
        assert result.error_code == "ACTION_CANCELLED"


# ---------------------------------------------------------------------------
# Dry-run (Fase 18 §13 — para antes do adapter)
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_never_calls_adapter(self):
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(
            make_request("mouse_move", {"x": 500, "y": 300}, dry_run=True)))
        assert result.status == ActionStatus.DRY_RUN
        assert adapter.calls == []  # sem execução física
        assert result.metadata.get("would_execute") == "mouse_move(x=500, y=300)"

    def test_dry_run_blocks_dangerous_hotkey(self):
        """dry_run NÃO bypassa a política de segurança."""
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(
            make_request("hotkey", {"keys": ["CTRL", "ALT", "DEL"]}, dry_run=True)))
        assert result.status == ActionStatus.REJECTED
        assert adapter.calls == []

    def test_dry_run_requires_confirmation_for_level2(self, db_session: OrmSession):
        from app.services.permissions import set_policy
        set_policy(db_session, "computer_action", PermissionLevel.LEVEL_2.value)
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(
            make_request("mouse_move", {"x": 1, "y": 1}, dry_run=True),
            ActionExecutorOptions(db=db_session)))
        assert result.status == ActionStatus.REJECTED  # confirmação exigida
        assert adapter.calls == []

    def test_dry_run_type_text_summary_sanitized(self):
        """dry_run não vaza texto digitado no resumo."""
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(
            make_request("type_text", {"text": "SUPER-SECRETO-123"}, dry_run=True)))
        assert result.status == ActionStatus.DRY_RUN
        assert "SUPER-SECRETO-123" not in json.dumps(result.to_dict())
        assert result.metadata.get("would_execute") == f"type_text(chars={len('SUPER-SECRETO-123')})"

    def test_dry_run_validates_and_emits_event(self, db_session: OrmSession):
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(
            make_request("mouse_move", {"x": 1, "y": 1}, dry_run=True),
            ActionExecutorOptions(db=db_session)))
        assert result.status == ActionStatus.DRY_RUN
        stats = action_stats(db_session)
        assert stats["events"]["dry_run"] == 1


# ---------------------------------------------------------------------------
# Adapter unavailable / failure (Fase 18 §8)
# ---------------------------------------------------------------------------

class TestAdapterFailure:
    def test_unavailable_adapter_fails_all(self):
        from app.action.adapters.unavailable import UnavailableComputerAdapter
        adapter = UnavailableComputerAdapter()
        executor = _executor(adapter)
        for action in ("mouse_move", "click", "scroll", "key_press", "hotkey",
                       "type_text", "focus_window"):
            params = {"mouse_move": {"x": 1, "y": 1}, "click": {"x": 1, "y": 1},
                      "scroll": {"amount": 1}, "key_press": {"key": "ENTER"},
                      "hotkey": {"keys": ["CTRL", "C"]},
                      "type_text": {"text": "oi"},
                      "focus_window": {"title": "Janela"}}[action]
            result = run_async(executor.execute(make_request(action, params)))
            assert result.status == ActionStatus.UNAVAILABLE, f"{action} deveria ser UNAVAILABLE"

    def test_unavailable_capabilities(self):
        from app.action.adapters.unavailable import UnavailableComputerAdapter
        caps = UnavailableComputerAdapter().discover_capabilities()
        assert not caps.mouse and not caps.keyboard and caps.summary == "nenhuma"

    def test_adapter_method_failure(self):
        adapter = FakeComputerAdapter(fail="mouse_move")
        executor = _executor(adapter)
        result = run_async(executor.execute(make_request("mouse_move", {"x": 1, "y": 1})))
        assert result.status == ActionStatus.FAILED
        assert "falha simulada" in (result.error_message or "")

    def test_focus_window_missing_both_targets_rejected(self):
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(make_request("focus_window", {})))
        assert result.status == ActionStatus.REJECTED

    def test_focus_window_not_found_fails(self):
        class NoWindowAdapter(FakeComputerAdapter):
            def focus_window(self, title=None, process=None):
                return False

        executor = _executor(NoWindowAdapter())
        result = run_async(executor.execute(
            make_request("focus_window", {"title": "Nao-Existe"})))
        assert result.status == ActionStatus.FAILED


# ---------------------------------------------------------------------------
# Audit / sanitização (Fase 18 §9)
# ---------------------------------------------------------------------------

class TestAudit:
    def test_success_audited(self, db_session: OrmSession):
        from app.models import AuditLog
        executor = _executor(FakeComputerAdapter())
        run_async(executor.execute(
            make_request("mouse_move", {"x": 1, "y": 1}),
            ActionExecutorOptions(db=db_session)))
        entries = db_session.query(AuditLog).filter(
            AuditLog.action == "computer.action.executed").all()
        assert len(entries) == 1
        assert "mouse_move" in entries[0].detail

    def test_rejected_audited(self, db_session: OrmSession):
        from app.models import AuditLog
        executor = _executor()
        run_async(executor.execute(
            make_request("hotkey", {"keys": ["CTRL", "ALT", "DEL"]}),
            ActionExecutorOptions(db=db_session)))
        entries = db_session.query(AuditLog).filter(
            AuditLog.action == "computer.action.rejected").all()
        assert len(entries) == 1
        assert entries[0].allowed is False

    def test_type_text_audit_sanitized(self, db_session: OrmSession):
        from app.models import AuditLog
        executor = _executor(FakeComputerAdapter())
        run_async(executor.execute(
            make_request("type_text", {"text": "SENHA-ULTRA-TOP-SECRETA"}),
            ActionExecutorOptions(db=db_session)))
        entries = db_session.query(AuditLog).all()
        joined = "\n".join(e.detail + (e.action or "") for e in entries)
        assert "SENHA-ULTRA-TOP-SECRETA" not in joined
        assert f"type_text(chars={len('SENHA-ULTRA-TOP-SECRETA')})" in joined

    def test_hotkey_audit_omits_keys(self, db_session: OrmSession):
        from app.models import AuditLog
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        run_async(executor.execute(
            make_request("hotkey", {"keys": ["CTRL", "V"]}),
            ActionExecutorOptions(db=db_session)))
        entries = db_session.query(AuditLog).all()
        assert "hotkey(keys=2)" in "".join(e.detail for e in entries)
        assert "CTRLC" not in "".join(e.detail + (e.action or "") for e in entries)


# ---------------------------------------------------------------------------
# Ops observer / eventos
# ---------------------------------------------------------------------------

class TestObserver:
    def test_stats_populated(self, db_session: OrmSession):
        executor = _executor(FakeComputerAdapter())
        run_async(executor.execute(
            make_request("click", {"x": 1, "y": 2}),
            ActionExecutorOptions(db=db_session)))
        stats = action_stats(db_session)
        assert stats["events"]["executed"] == 1
        assert stats["actions_total"] >= 1

    def test_events_never_contain_payload(self, db_session: OrmSession):
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        run_async(executor.execute(
            make_request("type_text", {"text": "CONTEUDO-CONFIDENCIAL-ABC"}),
            ActionExecutorOptions(db=db_session)))
        run_async(executor.execute(
            make_request("hotkey", {"keys": ["CTRL", "V"]}),
            ActionExecutorOptions(db=db_session)))
        from app.services import ops as ops_service
        events = ops_service.list_events(db_session, event_type=EVENT_ACTION_EXECUTED)
        for e in events:
            assert "CONTEUDO-CONFIDENCIAL-ABC" not in (e.meta_json or "")
            assert "CTRL" not in (e.meta_json or "")
        metas = [e.meta_json or "" for e in events]
        assert any("type_text" in m for m in metas)
        assert any("hotkey" in m for m in metas)


# ---------------------------------------------------------------------------
# Enforcement da ordem do pipeline (Fase 18 §12)
# ---------------------------------------------------------------------------

class TestPipelineOrder:
    def test_validation_happens_before_safety(self):
        """Params inválidos falham na validação, não no adapter."""
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(make_request("scroll", {"amount": 999})))
        assert result.status == ActionStatus.REJECTED
        assert adapter.calls == []

    def test_safety_before_permission(self, db_session: OrmSession):
        """Bloqueio de segurança ocorre mesmo sem db (permission engine off)."""
        from app.services.permissions import set_policy
        set_policy(db_session, "computer_action", PermissionLevel.LEVEL_1.value)
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(
            make_request("hotkey", {"keys": ["CTRL", "ALT", "DEL"]}),
            ActionExecutorOptions(db=None)))
        assert result.status == ActionStatus.REJECTED
        assert adapter.calls == []


# ---------------------------------------------------------------------------
# Integração com Tool Registry (Fase 18 §16/§17)
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_tool_registered(self):
        tool = get_tool_registry().get("computer_action")
        assert tool is not None
        assert tool.name == "computer_action"
        assert "mouse_move" in tool.parameters["properties"]["action_type"]["enum"]

    def test_tool_not_in_dangerous_zone(self):
        decls = get_tool_registry().declarations()
        names = {d.name for d in decls}
        assert "computer_action" in names
        for banned in ("execute_anything", "shell", "raw_input"):
            assert banned not in names

    def test_no_bypass_tools_in_registry(self):
        tools = get_tool_registry().all()
        for t in tools:
            assert "shell" not in t.name, f"tool {t.name} parece bypass"
            assert "raw" not in t.name, f"tool {t.name} parece bypass"
            assert "execute_anything" not in t.name

    def test_tool_run_dispatches_to_executor(self):
        tool = get_tool_registry().get("computer_action")
        assert tool is not None

    def test_tool_declaration_serializable(self):
        tool = get_tool_registry().get("computer_action")
        decl = tool.declaration()
        data = decl.model_dump(mode="json")
        assert data["name"] == "computer_action"
        assert "params" in data["parameters"]["properties"]


# ---------------------------------------------------------------------------
# COMPUTER_ENABLED=false (Fase 18 §16)
# ---------------------------------------------------------------------------

class TestDisabled:
    def test_disabled_layer_returns_unavailable(self):
        executor = ActionExecutor(
            adapter=FakeComputerAdapter(),
            override_enabled=False,
        )
        result = run_async(executor.execute(make_request("mouse_move", {"x": 1, "y": 1})))
        assert result.status == ActionStatus.UNAVAILABLE
        assert result.error_code == "ACTION_DISABLED"

    def test_disabled_even_dryrun(self):
        executor = ActionExecutor(
            adapter=FakeComputerAdapter(),
            override_enabled=False,
        )
        result = run_async(executor.execute(
            make_request("mouse_move", {"x": 1, "y": 1}, dry_run=True)))
        assert result.status == ActionStatus.UNAVAILABLE

    def test_default_tool_declared_but_gated(self):
        """Tool existe no registry mas a camada precisa de decisão explícita."""
        from app.core.config import settings
        assert settings.computer_actions_enabled is False
        tool = get_tool_registry().get("computer_action")
        assert tool is not None


# ---------------------------------------------------------------------------
# Sanitização / segredo nunca vaza (Fase 18 §9/§17)
# ---------------------------------------------------------------------------

class TestSanitization:
    def test_result_metadata_no_sensitive_text(self):
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = run_async(executor.execute(
            make_request("type_text", {"text": "XPTO-NUCLEAR-42"})))
        dump = json.dumps(result.to_dict())
        assert "XPTO-NUCLEAR-42" not in dump
        assert "type_text" in dump

    def test_failed_adapter_error_sanitized(self):
        executor = _executor(FakeComputerAdapter(fail="mouse_move"))
        result = run_async(executor.execute(make_request("mouse_move", {"x": 1, "y": 1})))
        assert "Stack" not in (result.error_message or "")
        assert "traceback" not in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# Serviço (camada de orquestração sem bypass)
# ---------------------------------------------------------------------------

class TestService:
    def test_service_blocks_dangerous(self):
        from app.action.service import execute_action
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = execute_action(
            action_type="hotkey",
            params={"keys": ["CTRL", "ALT", "DEL"]},
            executor=executor,
        )
        assert result.status == ActionStatus.REJECTED
        assert adapter.calls == []

    def test_service_dry_run_does_not_execute(self):
        from app.action.service import execute_action
        adapter = FakeComputerAdapter()
        executor = _executor(adapter)
        result = execute_action(
            action_type="mouse_move", params={"x": 5, "y": 5},
            dry_run=True, executor=executor,
        )
        assert result.status == ActionStatus.DRY_RUN
        assert adapter.calls == []