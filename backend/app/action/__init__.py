"""Computer Action Layer (Fase 18) — camada de ações controladas.

Fundação para interação segura e auditável do JARVIS com o computador.
Pipeline obrigatório: request → validation → safety → permission →
rate limit → cancellation/timeout → OS adapter → audit → event → result.

NÃO contém Planner, LLM nem lógica de raciocínio — apenas infraestrutura
de ações. O agente computadorizado (Fase 19+) decidirá quando usá-la.
"""

from .errors import (
    ActionCancelledError,
    ActionError,
    ActionRateLimitedError,
    ActionRejectedError,
    ActionTimeoutError,
    ActionUnavailableError,
    ActionValidationError,
)
from .executor import ActionExecutor, ActionExecutorOptions, get_executor
from .models import (
    ActionCapabilities,
    ActionRequest,
    ActionResult,
    ActionStatus,
    ActionType,
)
from .registry import get_action_registry, reset_action_registry
from .safety import ActionSafetyPolicy, SafetyVerdict
from .state import get_action_state, reset_action_state
from .validator import validate_action_request

__all__ = [
    "ActionCancelledError",
    "ActionError",
    "ActionRateLimitedError",
    "ActionRejectedError",
    "ActionTimeoutError",
    "ActionUnavailableError",
    "ActionValidationError",
    "ActionExecutor",
    "ActionExecutorOptions",
    "get_executor",
    "ActionCapabilities",
    "ActionRequest",
    "ActionResult",
    "ActionStatus",
    "ActionType",
    "get_action_registry",
    "reset_action_registry",
    "ActionSafetyPolicy",
    "SafetyVerdict",
    "get_action_state",
    "reset_action_state",
    "validate_action_request",
]