"""Computer Action Layer (Fase 18) — executor de ações.

ÚNICA porta de execução de ações de computador. Pipeline obrigatório:

    request
      ↓
    validation            (sem side effects)
      ↓
    safety policy         (blocked / confirm / allow)
      ↓
    permission            (effective_level — reutiliza Permission Engine)
      ↓
    rate limit            (RateLimiter — reutilizado)
      ↓
    cancellation/timeout  (dry_run para antes do adapter)
      ↓
    OS adapter            (método específico por action_type)
      ↓
    audit                 (log_action — reutilizado, detail sanitizado)
      ↓
    event                 (record_event — reutilizado, meta sanitizada)
      ↓
    result

`dry_run` executa toda a cadeia de validação/segurança/autorização/rate-limit
mas NÃO chama o adapter — retorna o que SERIA executado.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.enums import PermissionLevel
from app.remote.ratelimit import RateLimiter
from app.services import audit as audit_service
from app.services import permissions as permission_service

from .adapters.base import ComputerAdapter
from .adapters import get_adapter
from .errors import (
    ActionCancelledError,
    ActionDisabledError,
    ActionRateLimitedError,
    ActionRejectedError,
    ActionTimeoutError,
    ActionUnavailableError,
)
from .models import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    ActionType,
)
from .observer import (
    EVENT_ACTION_ACCEPTED,
    EVENT_ACTION_CANCELLED,
    EVENT_ACTION_DRY_RUN,
    EVENT_ACTION_EXECUTED,
    EVENT_ACTION_FAILED,
    EVENT_ACTION_REJECTED,
    EVENT_ACTION_REQUESTED,
    EVENT_ACTION_TIMEOUT,
    EVENT_ACTION_UNAVAILABLE,
    emit_action_event,
)
from .safety import ActionSafetyPolicy, SafetyVerdict
from .state import get_action_state
from .validator import validate_action_request

TOOL_NAME = "computer_action"


@dataclass
class ActionExecutorOptions:
    """Opções de execução para uma chamada específica do executor."""

    requested_by: str = "agent"
    session_id: str | None = None
    device_id: str | None = None
    cancelled_cb: Callable[[], bool] | None = None
    db: OrmSession | None = None


class ActionExecutor:
    """Executor determinístico: cada request resulta em exatamente um result."""

    def __init__(
        self,
        *,
        adapter: ComputerAdapter | None = None,
        rate_limiter: RateLimiter | None = None,
        safety_policy: ActionSafetyPolicy | None = None,
        override_enabled: bool | None = None,
    ) -> None:
        self._adapter = adapter
        self._safety_policy = safety_policy or ActionSafetyPolicy()
        self._limiter = rate_limiter or self._build_limiter()
        self._override_enabled = override_enabled

    @staticmethod
    def _build_limiter() -> RateLimiter:
        return RateLimiter(
            capacity=settings.computer_action_rate_capacity,
            refill_per_second=settings.computer_action_rate_refill_per_second,
        )

    @staticmethod
    def _sanitize_summary(request: ActionRequest) -> str:
        """Resumo sanitizado da ação para auditoria — nunca payload completo."""
        p = request.params
        if request.action_type == ActionType.TYPE_TEXT:
            n = len(p.get("text", ""))
            return f"type_text(chars={n})"
        if request.action_type == ActionType.HOTKEY:
            return f"hotkey(keys={len(p.get('keys', []))})"
        if request.action_type in (ActionType.KEY_PRESS, ActionType.FOCUS_WINDOW):
            return f"{request.action_type.value}(keys/detail omitido)"
        parts = []
        for k in ("x", "y", "amount", "button"):
            if k in p:
                parts.append(f"{k}={p[k]}")
        if not parts:
            return request.action_type.value
        return f"{request.action_type.value}({', '.join(parts)})"

    def is_enabled(self) -> bool:
        if self._override_enabled is not None:
            return self._override_enabled
        return bool(settings.computer_actions_enabled)

    async def execute(self, request: ActionRequest, opts: ActionExecutorOptions | None = None) -> ActionResult:
        """Executa UMA ação seguindo o pipeline obrigatório. Sempre retorna ActionResult."""
        opts = opts or ActionExecutorOptions()
        start = time.monotonic()
        result = ActionResult(
            action_id=request.action_id,
            action_type=request.action_type,
            status=ActionStatus.ACCEPTED,
            dry_run=request.dry_run,
        )

        # --- Gate global: camada desabilitada ---------------------------------
        if not self.is_enabled():
            result.status = ActionStatus.UNAVAILABLE
            result.error_code = "ACTION_DISABLED"
            result.error_message = "Computer Action Layer desabilitado"
            result.finished_at = _ts()
            result.duration_ms = _ms(start)
            self._emit(opts, EVENT_ACTION_UNAVAILABLE, request, result, "ACTION_DISABLED")
            return result

        # --- 1. Validation (sem side effects) ---------------------------------
        try:
            request = validate_action_request(request)
        except Exception as e:
            result.status = ActionStatus.REJECTED
            result.error_code = getattr(e, "code", "ACTION_VALIDATION_ERROR")
            result.error_message = str(e)
            result.finished_at = _ts()
            result.duration_ms = _ms(start)
            self._audit(opts, request, "computer.action.validation_failed", allowed=False)
            self._emit(opts, EVENT_ACTION_REJECTED, request, result, result.error_code)
            return result

        # --- 2. Safety policy -------------------------------------------------
        safety = self._safety_policy.evaluate(request)
        if safety.verdict == SafetyVerdict.BLOCK:
            result.status = ActionStatus.REJECTED
            result.error_code = "ACTION_REJECTED"
            result.error_message = safety.reason or "Bloqueado pela política de segurança"
            result.metadata["safety"] = safety.reason
            result.finished_at = _ts()
            result.duration_ms = _ms(start)
            self._audit(opts, request, "computer.action.rejected", allowed=False)
            self._emit(opts, EVENT_ACTION_REJECTED, request, result, "ACTION_REJECTED")
            return result
        if safety.verdict == SafetyVerdict.CONFIRM and not request.metadata.get("confirmed"):
            result.status = ActionStatus.REJECTED
            result.error_code = "ACTION_REQUIRES_CONFIRMATION"
            result.error_message = safety.reason or "Ação requer confirmação"
            result.metadata["requires_confirmation"] = True
            result.metadata["reason"] = safety.reason
            result.finished_at = _ts()
            result.duration_ms = _ms(start)
            self._audit(opts, request, "computer.action.confirmation_required", allowed=False)
            self._emit(opts, EVENT_ACTION_REJECTED, request, result, "ACTION_REQUIRES_CONFIRMATION")
            return result

        # --- 3. Permission (effective_level reutilizado) ----------------------
        perm_result = self._check_permission(request, opts)
        if perm_result is not None:
            result.status = ActionStatus.REJECTED
            result.error_code = perm_result
            result.error_message = "Ação não autorizada pelo Permission Engine"
            result.metadata["requires_confirmation"] = True
            result.metadata["reason"] = "Nível de permissão requer confirmação"
            result.finished_at = _ts()
            result.duration_ms = _ms(start)
            self._audit(opts, request, "computer.action.rejected", allowed=False)
            self._emit(opts, EVENT_ACTION_REJECTED, request, result, perm_result)
            return result

        # --- 4. Rate limit ----------------------------------------------------
        if not self._limiter.allow():
            get_action_state().record_rate_limited()
            result.status = ActionStatus.REJECTED
            result.error_code = "ACTION_RATE_LIMITED"
            result.error_message = "Rate limit de ações atingido"
            result.finished_at = _ts()
            result.duration_ms = _ms(start)
            self._audit(opts, request, "computer.action.rate_limited", allowed=False)
            self._emit(opts, EVENT_ACTION_REJECTED, request, result, "ACTION_RATE_LIMITED")
            return result

        get_action_state().start(request.action_id, request.action_type, opts.requested_by, request.dry_run)
        result.status = ActionStatus.ACCEPTED
        self._audit(opts, request, "computer.action.accepted", allowed=True)
        self._emit(opts, EVENT_ACTION_ACCEPTED, request, result)

        # --- 5. Cancellation / timeout / dry_run ------------------------------
        try:
            if request.dry_run:
                result.status = ActionStatus.DRY_RUN
                result.metadata["would_execute"] = self._sanitize_summary(request)
                result.metadata["adapter"] = self._adapter.name if self._adapter else get_adapter().name
                result.finished_at = _ts()
                result.duration_ms = _ms(start)
                self._audit(opts, request, "computer.action.dry_run", allowed=True)
                self._emit(opts, EVENT_ACTION_DRY_RUN, request, result)
                return result

            timeout = request.timeout_seconds or settings.computer_action_timeout_seconds
            adapter = self._adapter or get_adapter()

            async def _run() -> None:
                if opts.cancelled_cb and opts.cancelled_cb():
                    raise ActionCancelledError("Ação cancelada")
                await asyncio.to_thread(self._dispatch, adapter, request)
                # Checa novamente após a chamada atômica do OS: ações de mouse/
                # teclado são atômicas (ms); cancelamento é no limite da chamada.
                if opts.cancelled_cb and opts.cancelled_cb():
                    raise ActionCancelledError("Ação cancelada")

            try:
                await asyncio.wait_for(_run(), timeout=timeout)
            except asyncio.TimeoutError:
                result.status = ActionStatus.TIMEOUT
                result.error_code = "ACTION_TIMEOUT"
                result.error_message = "Ação excedeu o tempo máximo"
                self._finalize(result, start)
                self._audit(opts, request, "computer.action.timeout", allowed=True)
                self._emit(opts, EVENT_ACTION_TIMEOUT, request, result)
                return result
            except ActionCancelledError:
                result.status = ActionStatus.CANCELLED
                result.error_code = "ACTION_CANCELLED"
                result.error_message = "Ação cancelada"
                self._finalize(result, start)
                self._audit(opts, request, "computer.action.cancelled", allowed=None)
                self._emit(opts, EVENT_ACTION_CANCELLED, request, result)
                return result
            except ActionUnavailableError:
                result.status = ActionStatus.UNAVAILABLE
                result.error_code = "ACTION_UNAVAILABLE"
                result.error_message = "Adaptador indisponível"
                self._finalize(result, start)
                self._audit(opts, request, "computer.action.unavailable", allowed=None)
                self._emit(opts, EVENT_ACTION_UNAVAILABLE, request, result)
                return result

            result.status = ActionStatus.EXECUTED
            result.metadata["adapter"] = adapter.name
            result.adapter_detail = adapter.name
            self._finalize(result, start)
            self._audit(opts, request, "computer.action.executed", allowed=True)
            self._emit(opts, EVENT_ACTION_EXECUTED, request, result, duration_ms=result.duration_ms)
            return result

        except Exception as e:  # noqa: BLE001 — falha do adapter vira FAILED
            result.status = ActionStatus.FAILED
            result.error_code = getattr(e, "code", "ACTION_EXECUTION_FAILED")
            result.error_message = str(e)[:300]
            self._finalize(result, start)
            self._audit(opts, request, "computer.action.failed", allowed=None)
            self._emit(opts, EVENT_ACTION_FAILED, request, result, result.error_code)
            return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _dispatch(self, adapter: ComputerAdapter, request: ActionRequest) -> None:
        """Chama o método correto do adapter. Valida capacidades de antemão."""
        p = request.params
        if request.action_type == ActionType.MOUSE_MOVE:
            adapter.mouse_move(p["x"], p["y"])
        elif request.action_type in (ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK):
            if request.action_type == ActionType.DOUBLE_CLICK:
                adapter.mouse_double_click(p["x"], p["y"])
            else:
                button = p.get("button", "left")
                if request.action_type == ActionType.RIGHT_CLICK:
                    button = "right"
                adapter.mouse_click(p["x"], p["y"], button=button)
        elif request.action_type == ActionType.SCROLL:
            adapter.mouse_scroll(p["amount"], p.get("x"), p.get("y"))
        elif request.action_type == ActionType.KEY_PRESS:
            adapter.key_press(p["key"])
        elif request.action_type == ActionType.HOTKEY:
            adapter.hotkey(p["keys"])
        elif request.action_type == ActionType.TYPE_TEXT:
            delay = int(p.get("delay_ms") or 0)
            adapter.type_text(p["text"], delay_ms=delay)
        elif request.action_type == ActionType.FOCUS_WINDOW:
            found = adapter.focus_window(p.get("title"), p.get("process"))
            if not found:
                raise ActionRejectedError(
                    "Janela não encontrada",
                    detail="Nenhuma janela correspondeu a title/process",
                )
        else:
            raise ActionRejectedError(f"Tipo de ação não despachável: {request.action_type.value}")

    def _check_permission(self, request: ActionRequest, opts: ActionExecutorOptions) -> str | None:
        """Checa permissão efetiva. Retorna error_code se negado, senão None."""
        if opts.db is None:
            return None
        level = None
        try:
            level = permission_service.effective_level(opts.db, TOOL_NAME)
        except Exception:  # noqa: BLE001
            level = PermissionLevel.LEVEL_3

        # Nível base da própria ação (registry) — interseção conservadora.
        from app.action.registry import get_action_registry
        desc = get_action_registry().get(request.action_type)
        base_level = desc.permission_level if desc else PermissionLevel.LEVEL_1
        effective = max(level, base_level)

        if effective >= PermissionLevel.LEVEL_3:
            return "ACTION_BLOCKED_BY_DEFAULT"
        if effective >= PermissionLevel.LEVEL_2 and not request.metadata.get("confirmed"):
            return "ACTION_REQUIRES_CONFIRMATION"
        return None

    def _audit(self, opts: ActionExecutorOptions, request: ActionRequest, action: str, allowed: bool | None) -> None:
        if opts.db is None:
            return
        detail = self._sanitize_summary(request)
        try:
            audit_service.log_action(
                opts.db,
                action=action,
                session_id=opts.session_id,
                tool=TOOL_NAME,
                allowed=allowed,
                detail=f"{request.action_id} {request.action_type.value} {detail}",
            )
        except Exception:  # noqa: BLE001 — auditoria nunca derruba a chamada
            pass

    def _emit(
        self,
        opts: ActionExecutorOptions,
        event_type: str,
        request: ActionRequest,
        result: ActionResult,
        error_code: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if opts.db is None:
            return
        try:
            emit_action_event(
                opts.db,
                session_id=opts.session_id,
                event_type=event_type,
                action_type=request.action_type.value,
                status=result.status.value,
                dry_run=request.dry_run,
                error_code=error_code,
                duration_ms=duration_ms,
            )
        except Exception:  # noqa: BLE001 — evento nunca derruba a chamada
            pass

    def _finalize(self, result: ActionResult, start: float) -> None:
        result.finished_at = _ts()
        result.duration_ms = _ms(start)
        get_action_state().finish(
            result.action_id,
            result.status,
            result.duration_ms or 0,
            adapter_detail=result.metadata.get("adapter") or result.adapter_detail,
        )


_executor: ActionExecutor | None = None


def get_executor() -> ActionExecutor:
    global _executor
    if _executor is None:
        _executor = ActionExecutor()
    return _executor


def reset_executor() -> None:
    global _executor
    _executor = None


def _ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _ms(start: float) -> int:
    return int(round((time.monotonic() - start) * 1000))