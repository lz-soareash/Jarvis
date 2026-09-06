"""Orquestração de Percepção com observabilidade (Fase 15).

Wrapper único usado pela tool (`observe_computer`), pelo Agentic Core (passo
`kind="observe"`), pela API e pelos testes: consome o `PerceptionProvider`,
grava a observação no `ComputerState` (em memória, bounded) e conecta o
`event_sink` ao pipeline de eventos operacionais (`ops.record_event`) e à
auditoria (`audit.log_action`) — tudo sanitizado, sem screenshots, sem segredos.
"""

import logging

from sqlalchemy.orm import Session as OrmSession

from app.perception.base import PerceptionProvider, PerceptionResult
from app.perception.state import ComputerState
from app.perception.windows import get_perception_provider
from app.services import audit as audit_service
from app.services import ops as ops_service

logger = logging.getLogger("jarvis.perception_service")

PROVIDER_SINGLETON: dict[str, PerceptionProvider] = {}


def get_provider() -> PerceptionProvider:
    """Provider singleton (dispensável em override p/ testes)."""
    if "provider" not in PROVIDER_SINGLETON:
        PROVIDER_SINGLETON["provider"] = get_perception_provider()
    return PROVIDER_SINGLETON["provider"]


def reset_provider() -> None:
    """Força rediscovery do provider (usado pelos testes p/ injetar fakes)."""
    PROVIDER_SINGLETON.pop("provider", None)


async def run_perception(
    db: OrmSession,
    session_id: str | None,
    *,
    provider: PerceptionProvider | None = None,
    state: ComputerState | None = None,
    sink=None,
    include_processes: bool = False,
    max_processes: int = 30,
    capture_screenshot: bool = False,
) -> PerceptionResult:
    """Obtém a observação, persiste no state e emite ops/audit (sanitizado).

    `provider`/`state` permitem injeção em testes (fakes/mocks); produção usa o
    provider reflexivo e o ComputerState singleton.
    """
    provider = provider or get_provider()
    state = state or get_computer_state()
    available = provider.available()
    caps = provider.discover_capabilities()

    if not available:
        _record_ops(db, session_id, {"type": "observe.failed", "error": caps.detail or "provedor indisponível"})
        return PerceptionResult.unavailable(caps.detail or "provedor indisponível")

    result = provider.observe(
        include_processes=include_processes,
        max_processes=max_processes,
    )

    if capture_screenshot and result.available:
        if caps.screenshot:
            try:
                metadata = provider.capture_screenshot()
                if metadata is not None:
                    result.observation.screenshot = metadata
            except Exception as exc:  # noqa: BLE001
                result.observation.errors.append(f"screenshot: {exc}")
        else:
            result.observation.errors.append("screenshot: capacidade indisponível")

    if result.available:
        state.record(result.observation)
        _record_ops(db, session_id, {"type": "observe.completed", "observation": result.observation})
    else:
        _record_ops(db, session_id, {"type": "observe.failed", "error": result.error})

    if sink is not None:
        await sink(_sink_payload(result))
    return result


def _sink_payload(result: PerceptionResult) -> dict:
    obs = result.observation
    return {
        "type": "computer.observation",
        "available": result.available,
        "error": result.error,
        "timestamp": obs.timestamp,
        "capabilities": obs.capabilities.summary,
        "active_window": obs.active_window.title if obs.active_window else None,
        "process_count": len(obs.processes),
        "screenshot": bool(obs.screenshot),
    }


def _record_ops(db: OrmSession, session_id: str | None, payload: dict) -> None:
    event_type = payload.get("type") or ""
    if event_type == "observe.completed":
        obs = payload.get("observation")
        active = obs.active_window.title if obs and obs.active_window else None
        ops_service.record_event(
            db,
            session_id=session_id,
            event_type=ops_service.EVENT_COMPUTER_OBSERVED,
            status="observed",
            latency_ms=None,
            meta=ops_service.json_safe_meta(
                capabilities=(obs.capabilities.summary if obs else None),
                active_window=active,
                process_count=(len(obs.processes) if obs else 0),
                screenshot=bool(obs.screenshot) if obs else False,
            ),
        )
        audit_service.log_action(
            db,
            action="computer.observe",
            session_id=session_id,
            tool="observe_computer",
            allowed=True,
            detail=f"janela ativa: {active or 'n/d'}; screenshots: {'sim' if obs and obs.screenshot else 'não'}",
        )
        logger.info("Percepção registrada (sessão %s)", session_id)
    elif event_type == "observe.failed":
        ops_service.record_event(
            db,
            session_id=session_id,
            event_type=ops_service.EVENT_COMPUTER_OBSERVATION_FAILED,
            status="failed",
            meta=ops_service.json_safe_meta(error=str(payload.get("error") or "")[:300]),
        )
        audit_service.log_action(
            db,
            action="computer.observe",
            session_id=session_id,
            tool="observe_computer",
            allowed=False,
            detail=str(payload.get("error") or "")[:300],
        )


_STATE: ComputerState | None = None


def get_computer_state() -> ComputerState:
    global _STATE
    if _STATE is None:
        _STATE = ComputerState()
    return _STATE


def reset_computer_state() -> None:
    global _STATE
    _STATE = None
    PROVIDER_SINGLETON.clear()
