"""Computer Action Layer (Fase 18) — erros estruturados da camada de ações.

Taxonomia de erros com `code` estável — nunca stack traces, paths ou
detalhes internos nos erros expostos ao LLM/Agente/Remote.
"""

from __future__ import annotations


class ActionError(RuntimeError):
    """Classe base para erros da Computer Action Layer."""

    code: str = "ACTION_ERROR"

    def __init__(self, message: str, *, detail: str | None = None):
        self.detail = detail
        super().__init__(message)


class ActionValidationError(ActionError):
    """Ação malformada ou parâmetros inválidos (sem side effects)."""

    code = "ACTION_VALIDATION_ERROR"


class ActionRejectedError(ActionError):
    """Ação rejeitada pela política de segurança ou pelo Permission Engine."""

    code = "ACTION_REJECTED"


class ActionRateLimitedError(ActionError):
    """Rate limit de ações atingido."""

    code = "ACTION_RATE_LIMITED"


class ActionTimeoutError(ActionError):
    """Ação excedeu o tempo máximo de execução."""

    code = "ACTION_TIMEOUT"


class ActionCancelledError(ActionError):
    """Ação cancelada por solicitação (agentic core, remote, etc.)."""

    code = "ACTION_CANCELLED"


class ActionUnavailableError(ActionError):
    """Adapter indisponível (plataforma não suportada ou desabilitada)."""

    code = "ACTION_UNAVAILABLE"


class ActionDisabledError(ActionError):
    """Computer Action Layer desabilitado globalmente."""

    code = "ACTION_DISABLED"
