from abc import ABC, abstractmethod
from typing import AsyncIterator

from app.schemas.ai import AIMessage, AIProviderStatus, AIResponse


class AIProviderError(RuntimeError):
    """Erro do provedor de IA (config ausente, falha de API, etc.)."""


class AIProvider(ABC):
    """Contrato abstrato de IA.

    O restante do sistema depende desta interface — nunca do SDK específico.
    Permite trocar a implementação (ex.: outro provedor) sem reescrever o Core.
    """

    name: str = "base"

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Indica se o provedor possui credenciais/configuração válidas."""

    @abstractmethod
    async def generate(
        self,
        messages: list[AIMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AIResponse:
        """Gera uma resposta completa para o histórico de mensagens."""

    @abstractmethod
    async def stream(
        self,
        messages: list[AIMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Retorna a resposta como um fluxo assíncrono de texto."""

    @abstractmethod
    async def analyze(self, text: str, *, instruction: str | None = None) -> AIResponse:
        """Análise one-shot de um texto (com instrução opcional)."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Gera o vetor semântico (embeddings) do texto — usado pela memória v2."""

    @abstractmethod
    async def health_check(self) -> AIProviderStatus:
        """Verifica a saúde do provedor (sem expor secrets)."""