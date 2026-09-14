"""Fase 28 — Remote Operations: operações coordenadas seguras entre dispositivos.

Este orquestrador transforma um plano de passos ("abra o YouTube no PC e depois
o Chrome no celular") em execução controlada, reaproveitando TODA a infraestrutura
existente: Permission Engine (`effective_level`), Approval Engine (bind por
`operation_id`/passo), Tool Registry (ações locais do PC) e o Agent Orchestration
móvel (`mobile_agent` — capacidades/dispatch/idempotência/timeout).

Regras de segurança (conservadoras e determinísticas):
- O LLM NUNCA recebe device_ids/tokens/transportes — apenas `target` amigável.
- Resolução de alvo: PC explícito > nome/capability de dispositivo > contexto de
  sessão > único entregável > AMBIGUIDADE (pergunta ao usuário). Nunca executa em
  alvo desconhecido.
- Safety por PASSO via `permission_service.effective_level` da ferramenta real:
  L0/L1 executam; L2 cria `ApprovalRequest` vinculado à operação e PASSA; L3 é
  BLOQUEADO (denied) — mesmo dentro de uma operação, nunca se confirma L3.
- Confirmações de L2 são atreladas a `operation_id` + índice do passo — um "sim"
  válido só retoma AQUELA operação/passo.
- Idempotência por `operation_id`/passo: reuses devolvem o estado atual, sem
  re-despacho. Resultados/steps terminais nunca são reescritos (eventos tardios
  não modificam operações concluídas).
- Nenhum token/segredo/URL-sensível entra nos eventos, no `steps_json` nem nos
  `result` — tudo é sumarizado/sanitizado.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session as OrmSession

from app.models import RemoteOperation, utcnow
from app.remote import mobile_agent
from app.services import approvals as approval_service
from app.services import audit as audit_service
from app.services import permissions as permission_service
from app.tools import ToolContext
from app.tools import registry as tool_registry

logger = logging.getLogger("jarvis.operations")


# ---------------------------------------------------------------------------
# Vocabulário e limites
# ---------------------------------------------------------------------------

# pending | running | awaiting_confirmation | authorized | success | failed
# | denied | unsupported | timeout | cancelled
OPERATION_STATUS_VOCAB = frozenset(
    {
        "pending",
        "running",
        "awaiting_confirmation",
        "authorized",
        "success",
        "failed",
        "denied",
        "unsupported",
        "timeout",
        "cancelled",
    }
)
TERMINAL_OPERATION_STATUSES = frozenset(
    {"success", "failed", "denied", "timeout", "cancelled"}
)
_STEP_TERMINAL = frozenset(
    {"success", "failed", "denied", "unsupported", "timeout", "cancelled"}
)

_MAX_STEPS = 8
_PC_DEFAULT_TIMEOUT_MS = 20_000
# Timeout padrão de um comando móvel (espelha o CoreLink / mobile_agent).
_DEFAULT_MOBILE_TIMEOUT_MS = 15_000

# Aliases de alvo (determinístico — o LLM nunca escolhe device_id).
_PC_TARGET_ALIASES = frozenset({"pc", "computador", "desktop", "notebook"})
_MOBILE_TARGET_ALIASES = frozenset({"celular", "telefone", "aparelho", "mobile", "smartphone"})

# Aliases fechados de app → pacote android (OPEN_APP no móvel) e → alias do PC.
APP_ALIAS_TO_PACKAGE: dict[str, str] = {
    "chrome": "com.android.chrome",
    "firefox": "org.mozilla.firefox",
    "settings": "com.android.settings",
}
PACKAGE_TO_PC_ALIAS: dict[str, str] = {
    "com.android.chrome": "chrome",
    "org.mozilla.firefox": "firefox",
    "com.android.settings": "settings",
}


# ---------------------------------------------------------------------------
# Catálogo de ações canônicas -> ferramentas reais por tipo de alvo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionSpec:
    name: str
    label: str
    default_level: int
    pc_tool: str | None = None          # tool local registrada no Core
    mobile_capability: str | None = None  # capability móvel (registry Fase 25)
    mobile_tool: str | None = None      # tool mobile registrada (safety/override)
    risk: str = "low"


OPERATION_ACTIONS: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in [
        ActionSpec("OPEN_URL", "abertura de URL", 1, "open_url", "OPEN_URL", "mobile_open_url"),
        ActionSpec("OPEN_APP", "abertura de app", 1, "open_application", "OPEN_APP", "mobile_open_app"),
        ActionSpec("DEVICE_INFO", "informações do dispositivo", 0, "get_system_status", "DEVICE_INFO", "mobile_device_info"),
        ActionSpec("BATTERY_STATUS", "bateria", 0, None, "BATTERY_STATUS", "mobile_battery_status"),
        ActionSpec("NETWORK_STATUS", "rede", 0, None, "NETWORK_STATUS", "mobile_network_status"),
        ActionSpec("MEDIA_STATUS", "mídia", 0, None, "MEDIA_STATUS", "mobile_media_status"),
        ActionSpec("GET_SYSTEM_STATS", "estatísticas do PC", 0, "get_system_stats", None, None),
        ActionSpec("GET_SYSTEM_STATUS", "status do PC", 0, "get_system_status", None, None),
        ActionSpec("VIBRATE", "vibração", 1, None, "VIBRATE", "mobile_vibrate"),
        ActionSpec("SET_VOLUME", "volume", 1, None, "SET_VOLUME", "mobile_set_volume"),
        ActionSpec("SET_BRIGHTNESS", "brilho", 1, None, "SET_BRIGHTNESS", "mobile_set_brightness"),
        ActionSpec("CLOSE_APP", "fechamento de app", 2, "close_application", None, None, "medium"),
        ActionSpec("SHUTDOWN_COMPUTER", "desligamento do PC", 3, "shutdown_computer", None, None, "high"),
        ActionSpec("RESTART_COMPUTER", "reinicialização do PC", 3, "restart_computer", None, None, "high"),
        ActionSpec("SLEEP_COMPUTER", "suspensão do PC", 3, "sleep_computer", None, None, "high"),
        ActionSpec("LOCK_COMPUTER", "bloqueio do PC", 3, "lock_computer", None, None, "medium"),
    ]
}


def get_action(name: str) -> ActionSpec | None:
    if not isinstance(name, str) or not name:
        return None
    return OPERATION_ACTIONS.get(name.strip().upper())


def available_actions() -> list[dict[str, Any]]:
    """Visão declarativa do catálogo (sanitizada; p/ UI/prompt)."""
    return [
        {
            "action": spec.name,
            "label": spec.label,
            "level": spec.default_level,
            "pc": spec.pc_tool is not None,
            "mobile": spec.mobile_capability is not None,
            "risk": spec.risk,
        }
        for spec in OPERATION_ACTIONS.values()
    ]


# ---------------------------------------------------------------------------
# Erros de domínio
# ---------------------------------------------------------------------------


class OperationError(ValueError):
    """Erro de validação de uma operação (mensagem sanitizada p/ o modelo)."""


class UnknownApp(OperationError):
    """Alias/pacote de app não reconhecido para o alvo solicitado."""


# ---------------------------------------------------------------------------
# Embrulho de operação (estado + passos)
# ---------------------------------------------------------------------------


@dataclass
class OperationOutcome:
    operation: RemoteOperation
    steps: list[dict[str, Any]]
    status: str
    ok: bool
    message: str
    error: str | None = None
    requires_confirmation: bool = False
    needs_input: bool = False
    pending_approvals: list[Any] = field(default_factory=list)
    succeeded_steps: int = 0
    failed_steps: int = 0
    latency_ms: int | None = None


def _load_steps(operation: RemoteOperation) -> list[dict[str, Any]]:
    if not operation.steps_json:
        return []
    try:
        data = json.loads(operation.steps_json)
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def _save_steps(db: OrmSession, operation: RemoteOperation, steps: list[dict[str, Any]]) -> None:
    operation.steps_json = json.dumps(steps, ensure_ascii=False, default=str)
    db.commit()
    db.refresh(operation)


def _touch(db: OrmSession, operation: RemoteOperation) -> None:
    db.commit()
    db.refresh(operation)


def _finished(db: OrmSession, operation: RemoteOperation, started: float) -> None:
    operation.finished_at = utcnow()
    _touch(db, operation)


def _publish(event_type: str, meta: dict[str, Any]) -> None:
    try:
        from app.remote.events import publish_event

        publish_event(event_type, meta)
    except Exception:  # noqa: BLE001 — observabilidade nunca derruba a operação
        pass


def _event_meta(operation: RemoteOperation, **extra: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {"operation_id": operation.id}
    meta.update(extra)
    return meta


# ---------------------------------------------------------------------------
# Criação (idempotente por operation_id)
# ---------------------------------------------------------------------------


def _new_operation_id() -> str:
    return f"op_{uuid4().hex[:16]}"


def create_or_get_operation(
    db: OrmSession,
    *,
    session_id: str | None,
    requested_action: str,
    steps: list[dict[str, Any]],
    operation_id: str | None = None,
) -> RemoteOperation:
    """Cria a operação no banco; se `operation_id` já existir, devolve o existente.

    A reutilização do mesmo `operation_id` (retry/intent repetido) NÃO recria nem
    re-despacha: o estado persistido é a única fonte de verdade.
    """
    if operation_id:
        existing = db.get(RemoteOperation, operation_id)
        if existing is not None:
            return existing
    op_id = (operation_id or _new_operation_id()).strip()
    if len(steps) > _MAX_STEPS:
        raise OperationError(f"operação com mais de {_MAX_STEPS} passos")
    operation = RemoteOperation(
        id=op_id,
        session_id=session_id,
        requested_action=(requested_action or "").strip()[:160],
        status="pending",
        steps_json=json.dumps(steps, ensure_ascii=False, default=str),
        started_at=utcnow(),
    )
    db.add(operation)
    db.commit()
    db.refresh(operation)
    _publish(
        "remote.operation.created",
        _event_meta(operation, status="pending", action=operation.requested_action),
    )
    return operation


def load_by_id(db: OrmSession, operation_id: str) -> RemoteOperation | None:
    if not operation_id:
        return None
    return db.get(RemoteOperation, operation_id)


def approval_operation_id(arguments: dict[str, Any] | None) -> str | None:
    """Extrai `operation_id` dos arguments de uma ApprovalRequest (Fase 28)."""
    key = (arguments or {}).get("operation_id")
    return key if isinstance(key, str) and key else None


# ---------------------------------------------------------------------------
# Resolução de alvo determinística
# ---------------------------------------------------------------------------


@dataclass
class StepTarget:
    kind: str  # "pc" | "mobile"
    hint: str | None = None
    device_name: str | None = None
    device_id: str | None = None


@dataclass
class StepResolve:
    ok: bool
    target: StepTarget | None = None
    status: str = "failed"
    message: str = ""
    ambiguity: bool = False


def _resolve_step_target(
    db: OrmSession,
    *,
    target: str | None,
    action: ActionSpec,
    link=None,
    session_id: str | None = None,
) -> StepResolve:
    """Resolve o alvo de um passo com política conservadora e determinística."""
    key = (target or "").strip().lower()

    if key in _PC_TARGET_ALIASES:
        if action.pc_tool is None:
            return StepResolve(
                False,
                status="unsupported",
                message=f"'{action.label}' não é suportado no computador.",
            )
        return StepResolve(True, StepTarget(kind="pc", device_name="Computador"))

    if not key and action.pc_tool is not None and action.mobile_capability is None:
        # ação exclusiva de PC sem alvo informado
        return StepResolve(True, StepTarget(kind="pc", device_name="Computador"))

    if not key and action.pc_tool is not None:
        # ação disponível nos dois e sem alvo → padrão local (PC)
        return StepResolve(True, StepTarget(kind="pc", device_name="Computador"))

    if action.mobile_capability is None or action.mobile_tool is None:
        if key:
            return StepResolve(
                False,
                status="unsupported" if key in _MOBILE_TARGET_ALIASES else "failed",
                message=(
                    f"'{action.label}' não é suportado no celular."
                    if key in _MOBILE_TARGET_ALIASES
                    else f"Não encontrei o alvo '{target}'. Use 'computador' ou o nome de um celular pareado."
                ),
            )
        return StepResolve(
            False,
            status="failed",
            message=f"'{action.label}' exige um alvo (computador ou celular).",
        )

    # Alvo móvel: alias geral (sem hint) ou nome de dispositivo (com hint).
    hint = None if key in _MOBILE_TARGET_ALIASES else (key or None)
    resolution = mobile_agent.resolve_target(db, link, hint=hint, session_id=session_id)
    if resolution.ok and resolution.device is not None:
        return StepResolve(
            True,
            StepTarget(
                kind="mobile",
                hint=resolution.device.name,
                device_name=resolution.device.name,
                device_id=resolution.device.id,
            ),
        )
    message = resolution.message
    ambiguity = "Qual" in message and not message.startswith("Não encontrei")
    return StepResolve(False, status="failed", message=message, ambiguity=ambiguity)


# ---------------------------------------------------------------------------
# Nível efetivo por passo (Permission Engine existente)
# ---------------------------------------------------------------------------


def _step_level(db: OrmSession, action: ActionSpec, target: StepTarget) -> int:
    tool_name = action.pc_tool if target.kind == "pc" else action.mobile_tool
    if tool_name is None:
        return 0
    return permission_service.effective_level(db, tool_name).value


# ---------------------------------------------------------------------------
# Args por alvo (normalização segura)
# ---------------------------------------------------------------------------


def _pc_args(action: ActionSpec, params: dict[str, Any]) -> dict[str, Any]:
    if action.name == "OPEN_APP":
        if "target" in params:
            return {"target": str(params["target"])}
        alias = PACKAGE_TO_PC_ALIAS.get(str(params.get("package_name") or ""))
        if alias is None:
            raise UnknownApp("aplicativo não reconhecido para abrir no computador")
        return {"target": alias}
    if action.name == "OPEN_URL":
        url = (params or {}).get("url")
        return {"url": str(url)}
    return dict(params or {})


def _mobile_args(action: ActionSpec, params: dict[str, Any]) -> dict[str, Any]:
    if action.name == "OPEN_APP":
        if "package_name" in params:
            return {"package_name": str(params["package_name"])}
        alias = str(params.get("target") or params.get("app") or "").strip().lower()
        pkg = APP_ALIAS_TO_PACKAGE.get(alias)
        if pkg is None:
            raise UnknownApp(f"aplicativo não reconhecido no celular: {alias or '?'}")
        return {"package_name": pkg}
    if action.name == "OPEN_URL":
        return {"url": str((params or {}).get("url"))}
    return dict(params or {})


def _sanitize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Parâmetros persistidos sanitizados (valores truncados; nunca secrets)."""
    safe: dict[str, Any] = {}
    for k, v in (params or {}).items():
        if isinstance(v, str):
            safe[k] = v[:400]
        else:
            safe[k] = v
    return safe


# ---------------------------------------------------------------------------
# Execução de passo
# ---------------------------------------------------------------------------


async def _execute_pc_step(
    db: OrmSession,
    action: ActionSpec,
    params: dict[str, Any],
    timeout_ms: int | None,
) -> dict[str, Any]:
    tool = tool_registry.get_tool_registry().get(action.pc_tool or "")
    if tool is None:
        return {"status": "failed", "message": f"ferramenta local indisponível: {action.pc_tool}"}
    try:
        args = _pc_args(action, params)
    except OperationError as exc:
        return {"status": "failed", "message": str(exc)}
    context = ToolContext(db=db)
    wait = max(int(timeout_ms or _PC_DEFAULT_TIMEOUT_MS), 1_000)
    try:
        result = await asyncio.wait_for(tool.run(context, **args), timeout=wait / 1000.0)
    except asyncio.TimeoutError:
        return {"status": "timeout", "message": f"'{action.label}' local não respondeu a tempo (TIMEOUT)."}
    except Exception as exc:  # noqa: BLE001 — versão sanitizada p/ o modelo
        return {"status": "failed", "message": f"falha local ({type(exc).__name__})", "error": str(exc)[:240]}
    output = (result.output or "").strip()
    if result.ok:
        return {"status": "success", "message": output[:400] or f"'{action.label}' no computador: sucesso."}
    return {"status": "failed", "message": output[:400] or "falha local", "error": output[:400]}


async def _execute_step(
    db: OrmSession,
    *,
    action: ActionSpec,
    target: StepTarget,
    params: dict[str, Any],
    session_id: str | None,
    timeout_ms: int | None,
    link=None,
) -> dict[str, Any]:
    if target.kind == "pc":
        return await _execute_pc_step(db, action, params, timeout_ms)

    try:
        args = _mobile_args(action, params)
    except OperationError as exc:
        return {"status": "failed", "message": str(exc)}

    outcome = await mobile_agent.dispatch_mobile(
        db,
        capability=action.mobile_capability or "",
        args=args,
        session_id=session_id,
        hint=target.hint,
        timeout_ms=timeout_ms,
        link=link,
    )
    return {
        "status": outcome.status or "failed",
        "message": outcome.summary,
        "error": outcome.error,
        "transport": outcome.transport,
        "command_id": outcome.command_id,
        "device_name": outcome.device_name,
        "result": outcome.result,
    }


# ---------------------------------------------------------------------------
# Execução da operação
# ---------------------------------------------------------------------------

_running_operations: set[str] = set()


def _mark_step(
    db: OrmSession,
    operation: RemoteOperation,
    steps: list[dict[str, Any]],
    index: int,
    status: str,
    *,
    message: str | None = None,
    result: Any = None,
    error: str | None = None,
    command_id: str | None = None,
    transport: str | None = None,
    device_name: str | None = None,
) -> None:
    """Persiste o status terminal de um passo (nunca sobrescreve terminal)."""
    if not (0 <= index < len(steps)):
        return
    step = steps[index]
    if step.get("status") in _STEP_TERMINAL:
        return  # resultado tardio não reescreve passo concluído (idempotência)
    step["status"] = status
    if message is not None:
        step["message"] = message
    if result is not None:
        step["result"] = _small_result(result)
    if error is not None:
        step["error"] = error[:400]
    if command_id is not None:
        step["command_id"] = command_id
    if transport is not None:
        step["transport"] = transport
    if device_name is not None:
        step["device"] = device_name
    _save_steps(db, operation, steps)


def _small_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _small_result(v) for k, v in list(value.items())[:6]}
    if isinstance(value, (list, tuple)):
        return [_small_result(v) for v in list(value)[:6]]
    if isinstance(value, str):
        return value[:200]
    return value


async def _execute_steps(
    db: OrmSession,
    operation: RemoteOperation,
    steps: list[dict[str, Any]],
    *,
    session_id: str | None,
    link=None,
    timeout_ms: int | None = None,
) -> OperationOutcome:
    started = time.monotonic()

    for i, step in enumerate(steps):
        status = step.get("status") or "pending"
        if status in _STEP_TERMINAL:
            continue
        if status == "running":
            # Execução interrompida (reprocessamento): não re-despacha comando —
            # marca o passo como falho para evitar dupla execução de dispositivo.
            _mark_step(
                db, operation, steps, i, "failed",
                message="execução interrompida (passo inconcluído).",
            )
            continue
        pre_authorized = status == "authorized"
        if not pre_authorized:
            step["status"] = "pending"

        action_name = str(step.get("action") or "")
        action = get_action(action_name)
        if action is None:
            _mark_step(
                db, operation, steps, i, "failed",
                message=f"ação desconhecida: {action_name or '?'}",
            )
            continue

        resolution = _resolve_step_target(
            db, target=step.get("target"), action=action, link=link, session_id=session_id
        )
        if not resolution.ok:
            step["status"] = "pending"
            if resolution.ambiguity:
                # Não executa: pede desambiguação ao usuário (determinístico).
                operation.status = "pending"
                _touch(db, operation)
                _publish(
                    "remote.operation.awaiting_confirmation",
                    _event_meta(operation, reason="ambiguity", step=i),
                )
                return OperationOutcome(
                    operation=operation,
                    steps=steps,
                    status="pending",
                    ok=False,
                    needs_input=True,
                    message=resolution.message,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            _mark_step(
                db, operation, steps, i, resolution.status or "failed",
                message=resolution.message,
                error=resolution.message,
            )
            continue

        target = resolution.target
        level = _step_level(db, action, target)

        if level >= 3:
            # L3 — destrutiva/crítica: BLOQUEADA (nenhuma confirmação torna
            # aceitável dentro de uma Remote Operation).
            _mark_step(
                db, operation, steps, i, "denied",
                message="ação de nível ALTO (destrutiva) bloqueada em Remote Operation.",
                error="blocked: nível 3",
            )
            for j in range(i + 1, len(steps)):
                if steps[j].get("status") not in _STEP_TERMINAL:
                    steps[j]["status"] = "cancelled"
                    steps[j]["message"] = "interrompido por bloqueio de segurança"
            _save_steps(db, operation, steps)
            operation.status = "denied"
            operation.error = "passo bloqueado por nível crítico"
            _finished(db, operation, started)
            _publish("remote.operation.denied", _event_meta(operation, step=i, action=action.name))
            return _outcome(operation, steps, started, message=f"'{action.label}' bloqueado (nível crítico).")

        if level == 2 and not pre_authorized:
            # L2 — exige confirmação explícita VINCULADA à operação/passo.
            if not (session_id or operation.session_id):
                _mark_step(
                    db, operation, steps, i, "denied",
                    message="confirmação exigida indisponível (operação sem sessão).",
                    error="no session",
                )
                operation.status = "denied"
                operation.error = "confirmação exigida sem sessão"
                _finished(db, operation, started)
                return _outcome(operation, steps, started, message=f"'{action.label}' exige confirmação, mas a operação não tem sessão.")

            approval = approval_service.create_approval(
                db,
                session_id=(session_id or operation.session_id or ""),
                tool_name="remote_operation",
                arguments={
                    "operation_id": operation.id,
                    "step": i,
                    "action": action.name,
                    "target": target.device_name or "",
                },
                permission_level=2,
                risk=action.risk,
            )
            step["status"] = "pending"
            step["message"] = "aguardando confirmação do usuário"
            step["confirmation_step"] = True
            _save_steps(db, operation, steps)
            operation.status = "awaiting_confirmation"
            _touch(db, operation)
            _publish(
                "remote.operation.awaiting_confirmation",
                _event_meta(operation, step=i, action=action.name, target=target.device_name),
            )
            audit_service.log_action(
                db,
                action="remote.operation.approve_requested",
                session_id=session_id or operation.session_id or "",
                tool="remote_operation",
                allowed=None,
                detail=f"operation={operation.id} step={i} action={action.name}",
            )
            return _outcome(
                operation, steps, started, message="aguardando confirmação",
                requires_confirmation=True,
                pending_approvals=[approval_service.to_out(approval)],
            )

        # Execução (L0/L1, ou L2 pré-autorizado pelo usuário).
        step["status"] = "running"
        _save_steps(db, operation, steps)
        _publish(
            "remote.operation.step_started",
            _event_meta(operation, step=i, action=action.name, target=target.kind),
        )
        try:
            step_result = await _execute_step(
                db,
                action=action,
                target=target,
                params=step.get("params") or {},
                session_id=session_id,
                timeout_ms=timeout_ms,
                link=link,
            )
        except UnknownApp as exc:
            step_result = {"status": "failed", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 — falha interna vira passo falho
            logger.exception("Falha no passo %d da operação %s", i, operation.id)
            step_result = {"status": "failed", "message": f"falha interna ({type(exc).__name__})"}
        _mark_step(
            db, operation, steps, i, step_result.get("status") or "failed",
            message=step_result.get("message"),
            result=step_result.get("result"),
            error=step_result.get("error"),
            command_id=step_result.get("command_id"),
            transport=step_result.get("transport"),
            device_name=step_result.get("device_name"),
        )
        _publish(
            "remote.operation.step_completed",
            _event_meta(operation, step=i, action=action.name, status=step_result.get("status")),
        )

    statuses = [s.get("status") for s in steps if s.get("action")]
    terminal = [s for s in statuses if s in _STEP_TERMINAL]
    if len(terminal) < len(statuses):
        # Algum passo ainda pendente (não deveria acontecer pós-loop).
        operation.status = "pending"
        _touch(db, operation)
        return _outcome(operation, steps, started, message="operação incompleta (passos pendentes).")

    if any(s == "denied" for s in statuses):
        operation.status = "denied"
        operation.error = "passo negado/bloqueado"
    elif any(s == "timeout" for s in statuses):
        operation.status = "timeout"
        operation.error = "passo sem resposta a tempo"
    elif any(s in ("failed", "unsupported") for s in statuses):
        operation.status = "failed"
        operation.error = "passo(s) falhou(falharam)"
    else:
        operation.status = "success"
    _finished(db, operation, started)

    message = _aggregate_message(steps)
    _publish("remote.operation.completed", _event_meta(operation, status=operation.status))
    return _outcome(operation, steps, started, message=message)


def _aggregate_message(steps: list[dict[str, Any]]) -> str:
    labels = [f"{s.get('action')} ({s.get('target') or 'auto'}) → {s.get('status')}" for s in steps if s.get("action")]
    if not labels:
        return "operação concluída."
    return " | ".join(labels)


def _outcome(
    operation: RemoteOperation,
    steps: list[dict[str, Any]],
    started: float,
    *,
    message: str,
    ok: bool | None = None,
    error: str | None = None,
    requires_confirmation: bool = False,
    needs_input: bool = False,
    pending_approvals: list[Any] | None = None,
) -> OperationOutcome:
    statuses = [s.get("status") for s in steps]
    succeeded = sum(1 for s in statuses if s == "success")
    failed = sum(1 for s in statuses if s in ("failed", "denied", "timeout"))
    overall = operation.status
    if ok is None:
        ok = overall == "success"
    return OperationOutcome(
        operation=operation,
        steps=steps,
        status=overall,
        ok=ok,
        message=message,
        error=error,
        requires_confirmation=requires_confirmation,
        needs_input=needs_input,
        pending_approvals=pending_approvals or [],
        succeeded_steps=succeeded,
        failed_steps=failed,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def _pending_approvals_for(
    db: OrmSession, operation: RemoteOperation, session_id: str | None
) -> list[Any]:
    """Aprovações PENDENTES vinculadas a esta operação (retomada/idempotência)."""
    bind = session_id or operation.session_id
    if not bind:
        return []
    return [
        a
        for a in approval_service.pending_for_session(db, bind)
        if a.tool_name == "remote_operation"
        and approval_operation_id(a.arguments_dict) == operation.id
    ]


async def run_operation(
    db: OrmSession,
    *,
    operation: RemoteOperation,
    session_id: str | None = None,
    link=None,
    timeout_ms: int | None = None,
) -> OperationOutcome:
    """Executa (ou retoma) a operação, publicando eventos e zerando latência.

    Idempotente: operações já terminais devolvem o estado atual sem re-despachar;
    operações pausadas em confirmação devolvem o estado com os approvals pendentes.
    """
    started = time.monotonic()
    steps = _load_steps(operation)
    if not steps:
        operation.status = "failed"
        operation.error = "operação sem passos executáveis"
        _finished(db, operation, started)
        _publish("remote.operation.failed", _event_meta(operation, reason="empty"))
        return _outcome(operation, steps, started, message="nenhum passo executável.", error="empty")

    if operation.status in TERMINAL_OPERATION_STATUSES:
        return _outcome(
            operation, steps, started,
            message=f"operação já concluída ({operation.status}).",
            error=operation.error,
        )

    if operation.status == "awaiting_confirmation":
        return _outcome(
            operation, steps, started, message="aguardando confirmação do usuário.",
            requires_confirmation=True,
            pending_approvals=_pending_approvals_for(db, operation, session_id),
        )

    if operation.id in _running_operations:
        return _outcome(
            operation, steps, started,
            message="operação já em execução.",
            ok=False, error="concurrency",
        )

    _running_operations.add(operation.id)
    operation.status = "running"
    operation.error = None
    _touch(db, operation)
    _publish("remote.operation.started", _event_meta(operation))
    try:
        return await _execute_steps(
            db, operation, steps, session_id=session_id, link=link, timeout_ms=timeout_ms
        )
    finally:
        _running_operations.discard(operation.id)


# ---------------------------------------------------------------------------
# Retomada por aprovação
# ---------------------------------------------------------------------------


async def resume_operation_from_approval(
    db: OrmSession,
    *,
    operation: RemoteOperation,
    approved: bool,
    step: int,
    session_id: str | None,
    link=None,
    timeout_ms: int | None = None,
) -> OperationOutcome:
    """Aplica a decisão do usuário a um passo L2 e segue a operação.

    - aprovado → marca o passo como `authorized` e re-executa (a partir dele,
      reavaliando L2/L3 dos passos seguintes);
    - negado → marca o passo como `denied` e a operação como `denied`.
    """
    started = time.monotonic()
    if operation.status in TERMINAL_OPERATION_STATUSES:
        # Já decidida/terminada (evento tardio) — não modifica estado concluído.
        return _outcome(
            operation, _load_steps(operation), started,
            message=f"operação já concluída ({operation.status}).",
            error=operation.error,
        )
    steps = _load_steps(operation)
    if not (0 <= step < len(steps)):
        raise OperationError("passo da aprovação não existe na operação.")

    if not approved:
        _mark_step(
            db, operation, steps, step, "denied",
            message="o usuário negou a confirmação deste passo.",
        )
        for j in range(step + 1, len(steps)):
            if steps[j].get("status") not in _STEP_TERMINAL:
                steps[j]["status"] = "cancelled"
                steps[j]["message"] = "cancelado após negação do usuário"
        _save_steps(db, operation, steps)
        operation.status = "denied"
        operation.error = "passo negado pelo usuário"
        _finished(db, operation, started)
        _publish("remote.operation.denied", _event_meta(operation, step=step, reason="user_denied"))
        return _outcome(operation, steps, started, message="confirmação negada pelo usuário.")

    # Autoriza o passo aprovado sem nova confirmação.
    if steps[step].get("status") not in _STEP_TERMINAL:
        steps[step]["status"] = "authorized"
    _save_steps(db, operation, steps)
    operation.status = "authorized"
    _touch(db, operation)
    _publish("remote.operation.authorized", _event_meta(operation, step=step))
    return await _execute_steps(
        db, operation, steps, session_id=session_id, link=link, timeout_ms=timeout_ms
    )


# ---------------------------------------------------------------------------
# Cancelamento (best-effort entre passos; nunca em plena execução)
# ---------------------------------------------------------------------------


def cancel_operation(db: OrmSession, operation_id: str) -> RemoteOperation | None:
    operation = load_by_id(db, operation_id)
    if operation is None:
        return None
    if operation.status in TERMINAL_OPERATION_STATUSES:
        return operation
    steps = _load_steps(operation)
    for step in steps:
        if step.get("status") not in _STEP_TERMINAL and step.get("status") != "running":
            step["status"] = "cancelled"
            step["message"] = "cancelado pelo usuário"
    _save_steps(db, operation, steps)
    operation.status = "cancelled"
    operation.error = "cancelado pelo usuário"
    operation.finished_at = utcnow()
    _touch(db, operation)
    _publish("remote.operation.cancelled", _event_meta(operation))
    return operation


# ---------------------------------------------------------------------------
# Serialização sanitizada (UI/modelo/API)
# ---------------------------------------------------------------------------


def _step_out(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": step.get("action"),
        "target": step.get("target"),
        "status": step.get("status") or "pending",
        "message": step.get("message"),
        "device": step.get("device"),
        "transport": step.get("transport"),
        "result": _small_result(step.get("result")),
        "error": step.get("error"),
    }


def to_out(operation: RemoteOperation, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    steps = _load_steps(operation) if steps is None else steps
    statuses = [s.get("status") for s in steps]
    return {
        "id": operation.id,
        "status": operation.status,
        "requested_action": operation.requested_action or None,
        "succeeded_steps": sum(1 for s in statuses if s == "success"),
        "failed_steps": sum(1 for s in statuses if s in ("failed", "denied", "timeout")),
        "steps": [_step_out(s) for s in steps],
        "created_at": operation.created_at.isoformat() if operation.created_at else None,
        "finished_at": operation.finished_at.isoformat() if operation.finished_at else None,
        "error": operation.error,
    }


def to_payload(outcome: OperationOutcome) -> dict[str, Any]:
    """Payload estruturado e sanitizado do resultado (tool_done SSE → UI)."""
    approvals = []
    if outcome.requires_confirmation:
        for approval in outcome.pending_approvals:
            if isinstance(approval, dict):
                approvals.append(approval)
            elif hasattr(approval, "model_dump"):
                try:
                    approvals.append(approval.model_dump(mode="json"))
                except Exception:  # noqa: BLE001
                    continue
            elif hasattr(approval, "id"):
                try:
                    approvals.append(approval_service.to_out(approval).model_dump(mode="json"))
                except Exception:  # noqa: BLE001
                    continue
    return {
        "type": "remote.operation.result",
        "operation_id": outcome.operation.id,
        "status": outcome.status,
        "requires_confirmation": outcome.requires_confirmation,
        "needs_input": outcome.needs_input,
        "message": outcome.message,
        "succeeded_steps": outcome.succeeded_steps,
        "failed_steps": outcome.failed_steps,
        "approvals": approvals,
        "operation": to_out(outcome.operation, outcome.steps),
        "error": outcome.error,
    }


def list_operations(
    db: OrmSession, *, session_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    from sqlalchemy import select  # noqa: PLC0415

    stmt = select(RemoteOperation).order_by(RemoteOperation.created_at.desc()).limit(limit)
    if session_id:
        stmt = (
            select(RemoteOperation)
            .where(RemoteOperation.session_id == session_id)
            .order_by(RemoteOperation.created_at.desc())
            .limit(limit)
        )
    return [to_out(op) for op in db.scalars(stmt).all()]