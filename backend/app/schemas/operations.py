"""Fase 28 — Remote Operations: schemas da API de operações coordenadas.

Nunca expõem tokens, credenciais, device_ids ou payloads sensíveis — apenas o
`target` amigável e resultados sumarizados/sanitizados.
"""

from typing import Any

from pydantic import Field

from app.schemas.approvals import ApprovalOut
from app.schemas.base import APIModel


class OperationStepIn(APIModel):
    """Um passo do plano: alvo amigável + ação canônica + parâmetros opcionais."""

    target: str | None = Field(
        default=None,
        description="Alvo amigável: 'computador'/'pc' ou o nome de um celular pareado.",
    )
    action: str = Field(..., description="Ação canônica (ex.: OPEN_URL, OPEN_APP).")
    params: dict[str, Any] | None = None
    timeout_ms: int | None = Field(default=None, ge=1000, le=120_000)


class OperationCreateIn(APIModel):
    """Criação + execução imediata de uma operação (idempotente por operation_id)."""

    operation: str | None = Field(default=None, max_length=160)
    operation_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = None
    steps: list[OperationStepIn] = Field(..., min_length=1, max_length=8)
    timeout_ms: int | None = Field(default=None, ge=1000, le=120_000)


class OperationStepOut(APIModel):
    action: str | None = None
    target: str | None = None
    status: str = "pending"
    message: str | None = None
    device: str | None = None
    transport: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class OperationOut(APIModel):
    id: str
    status: str
    requested_action: str | None = None
    succeeded_steps: int = 0
    failed_steps: int = 0
    steps: list[OperationStepOut] = Field(default_factory=list)
    created_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


class OperationCreateOut(APIModel):
    accepted: bool
    operation: OperationOut
    message: str
    requires_confirmation: bool = False
    needs_input: bool = False
    approvals: list[ApprovalOut] = Field(default_factory=list)