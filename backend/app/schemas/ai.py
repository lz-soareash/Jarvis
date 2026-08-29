from typing import Literal

from app.schemas.base import APIModel


class AIMessage(APIModel):
    role: Literal["user", "assistant"]
    content: str


class AIResponse(APIModel):
    text: str
    provider: str
    model: str | None = None


class AIProviderStatus(APIModel):
    """Resultado do healthcheck de um provedor de IA."""

    status: Literal["ok", "unconfigured", "error"]
    provider: str
    model: str | None = None
    detail: str | None = None