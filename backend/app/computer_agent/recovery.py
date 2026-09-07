"""Computer Recovery (Fase 19) — decisão após falha: retry/replan/give_up.

Limites rígidos (nunca retry infinito): max_retries por passo, max_plan_retries,
max_steps, max_actions e deadline. Recovery re-OBSERVA → RE-ANALISA → REPLANEJA
→ tenta novamente; em limites esgotados → FAILED.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .models import ComputerTaskState

KIND_CONTINUE = "continue"
KIND_RETRY = "retry"
KIND_REPLAN = "replan"
KIND_GIVE_UP = "give_up"


@dataclass
class RecoveryDecision:
    kind: str  # continue | retry | replan | give_up
    reason: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "reason": self.reason, "detail": self.detail[:400]}


class ComputerRecovery:
    def decide(self, *, task: ComputerTaskState, failed_index: int | None = None,
               error: str = "", now: float | None = None) -> RecoveryDecision:
        now = now if now is not None else time.time()
        if task.cancellation_requested:
            return RecoveryDecision(KIND_GIVE_UP, "cancelled", "cancelamento solicitado")
        if task.deadline is not None and now > task.deadline:
            return RecoveryDecision(KIND_GIVE_UP, "deadline", "prazo de execução excedido")
        if task.steps_total >= task.max_steps > 0 or task.actions_total >= task.max_actions > 0:
            return RecoveryDecision(KIND_GIVE_UP, "limits", "tetos de passos/ações atingidos")
        if task.retries_used >= task.max_retries > 0:
            return RecoveryDecision(KIND_GIVE_UP, "max_retries", "retries esgotados")
        if task.plan_retries_used >= task.max_plan_retries > 0:
            return RecoveryDecision(KIND_GIVE_UP, "max_plan_retries", "replan esgotado")

        if task.recoveries < 3:
            if task.plan_retries_used > 0:
                return RecoveryDecision(KIND_REPLAN, "replan", error or "ação falhou")
            return RecoveryDecision(KIND_RETRY, "retry", error or "ação falhou")
        return RecoveryDecision(KIND_GIVE_UP, "recoveries", "recuperação repetida sem sucesso")