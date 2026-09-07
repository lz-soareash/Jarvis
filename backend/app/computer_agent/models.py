"""Fase 19 — Computer Agent: modelos de dados do Computer Use Agent.

Conceitos centrais:
- `IntendedAction`: intenção estruturada produzida pelo PLANNER (nunca atendida
  pelo planner — sempre validada pelo pipeline). Inclui `expected` para a
  VERIFICAÇÃO estrutural pós-ação.
- `ComputerTaskState`: estado explícito e resumível da tarefa (fases/marcos,
  contadores, limites, deadline, cancelamento).
- `AutonomyLevel`: nível de autonomia C0..C4 (imutável durante a tarefa — o
  LLM/agente nunca altera o próprio nível).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

# Limites de permissão por nível de autonomia (máx. nível de AÇÃO aceito).
# C0 = observa apenas. C1 = reversível (L1). C2 = automação controlada (L2 com
# confirmação). C3 = sensível exige confirmação (L3 bloqueado).
# C4 = crítica exige autorização explícita (L3 liberado SÓ se autorizado).
_AUTONOMY_MAX_LEVEL = {
    "C0": 0,
    "C1": 1,
    "C2": 2,
    "C3": 3,  # L3 bloqueado por default do Permission Engine (executado nunca)
    "C4": 3,  # L3 permitido apenas com `explicit_authorization=True`
}
_VALID_AUTONOMIES = set(_AUTONOMY_MAX_LEVEL)


class AutonomyLevel(str, Enum):
    """Nível de autonomia do Computer Agent (não alterável pelo agente)."""

    C0 = "C0"  # observe only
    C1 = "C1"  # reversible actions
    C2 = "C2"  # controlled automation (L2 com confirmação)
    C3 = "C3"  # sensitive actions require confirmation (L3 bloqueado)
    C4 = "C4"  # critical actions require explicit authorization (L3)

    @classmethod
    def from_str(cls, value: str) -> "AutonomyLevel":
        value = (value or "C1").strip().upper()
        if value not in _VALID_AUTONOMIES:
            raise ValueError(f"Nível de autonomia inválido: {value!r}")
        return cls(value)

    def max_allowed_level(self) -> int:
        return _AUTONOMY_MAX_LEVEL[self.value]


class ComputerStatus(str, Enum):
    """Máquina de estados explícita do Computer Agent."""

    IDLE = "idle"
    PLANNING = "planning"
    PERCEIVING = "perceiving"
    EXECUTING = "executing"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class IntendedAction:
    """Intenção estruturada do planner (O QUE deveria acontecer).

    - `action`: tipo de ação do Computer Action Layer (ex.: "click", "hotkey")
      ou "observe" (passo de percepção) ou "none" (C0 — sem ação).
    - `params`: argumentos para o `computer_action` (ActionType + validator).
    - `expected`: critério estrutural da verificação pós-ação
      (ex.: {"window_title_contains": "Bloco de Notas"}).
    - `source`: de onde a intenção foi derivada (goal/observation/llm) — usado
      pelo guard de prompt injection (observação é DADO não confiável).
    """

    action: str
    params: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] | None = None
    note: str = ""
    source: str = "goal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "params": dict(self.params or {}),
            "expected": dict(self.expected or {}),
            "note": self.note[:200],
            "source": self.source,
        }


@dataclass
class ComputerTaskState:
    """Estado explícito e resumível de uma tarefa de Computer Use."""

    task_id: str
    goal: str
    session_id: str | None = None
    status: ComputerStatus = ComputerStatus.IDLE
    autonomy: AutonomyLevel = AutonomyLevel.C1
    plan: list[IntendedAction] = field(default_factory=list)
    step_index: int = 0
    observations: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    verification_results: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    retries_used: int = 0
    plan_retries_used: int = 0
    actions_total: int = 0
    steps_total: int = 0
    recoveries: int = 0
    loops_prevented: int = 0
    confirmations_required: int = 0
    pending_approval_ids: list[str] = field(default_factory=list)
    started_at: str = ""
    updated_at: str = ""
    deadline: float | None = None
    cancellation_requested: bool = False
    last_observation: dict | None = None
    last_action_summary: str | None = None
    last_verified_action_id: str | None = None
    error: str | None = None
    completed_at: str | None = None
    requested_by: str = "local"
    device_id: str | None = None
    execution_deadline: float | None = None
    explicit_authorization: bool = False
    provider_used: str | None = None
    provider_reason: str | None = None
    # Limites rígidos (visíveis/explícitos no estado).
    max_steps: int = 12
    max_actions: int = 24
    max_retries: int = 2
    max_plan_retries: int = 3
    timeout_seconds: float = 180.0
    # Próxima ação confirmada (aprovação concedida nos approvals existentes).
    confirmed_pending: bool = False

    def snapshot(self) -> dict:
        return {
            "task_id": self.task_id,
            "goal": self.goal[:500],
            "session_id": self.session_id,
            "status": self.status.value,
            "autonomy": self.autonomy.value,
            "step_index": self.step_index,
            "steps_total": self.steps_total,
            "actions_total": self.actions_total,
            "retries_used": self.retries_used,
            "plan_retries_used": self.plan_retries_used,
            "recoveries": self.recoveries,
            "loops_prevented": self.loops_prevented,
            "confirmations_required": self.confirmations_required,
            "pending_approval_ids": list(self.pending_approval_ids),
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "last_action_summary": self.last_action_summary,
            "last_verified_action_id": self.last_verified_action_id,
            "requested_by": self.requested_by,
            "device_id": self.device_id,
            "provider_used": self.provider_used,
            "provider_reason": self.provider_reason,
        }

    def with_(self, **changes) -> "ComputerTaskState":
        return replace(self, **changes)

    def plan_dicts(self) -> list[dict]:
        return [s.to_dict() for s in self.plan]


class Limits:
    """Limites rígidos do loop (nunca loops indefinidos)."""

    __slots__ = ("max_steps", "max_actions", "max_retries", "max_plan_retries", "timeout_seconds")

    def __init__(self, *, max_steps=12, max_actions=24, max_retries=2, max_plan_retries=3, timeout_seconds=180.0):
        self.max_steps = max_steps
        self.max_actions = max_actions
        self.max_retries = max_retries
        self.max_plan_retries = max_plan_retries
        self.timeout_seconds = timeout_seconds