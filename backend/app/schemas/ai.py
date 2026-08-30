from typing import Literal

from app.schemas.base import APIModel


class ToolCall(APIModel):
    """Chamada de ferramenta proposta pelo modelo (Tool Engine — Fase 3)."""

    name: str
    arguments: dict = {}
    call_id: str | None = None
    thought_signature: str | None = None


class ToolDeclaration(APIModel):
    """Contrato público de uma ferramenta exposto ao provedor de IA."""

    name: str
    description: str
    parameters: dict = {}


class AIMessage(APIModel):
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = []
    tool_name: str | None = None
    tool_call_id: str | None = None


class AIResponse(APIModel):
    text: str
    provider: str
    model: str | None = None
    tool_calls: list[ToolCall] = []


class AIProviderStatus(APIModel):
    """Resultado do healthcheck de um provedor de IA."""

    status: Literal["ok", "unconfigured", "error"]
    provider: str
    model: str | None = None
    detail: str | None = None
    code: str | None = None