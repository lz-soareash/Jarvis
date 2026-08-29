"""Contratos do Tool Engine: ferramentas declaradas e executadas pelo Core.

O modelo (Gemini) propõe chamadas (`ToolDeclaration`); o Core decide se
executa conforme a política de permissão e devolve o resultado ao loop.
O Core nunca executa o que o modelo inventa — apenas o que está registrado.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider
from app.core.enums import PermissionLevel, RiskLevel
from app.schemas.ai import ToolDeclaration


@dataclass
class ToolResult:
    """Resultado da execução de uma ferramenta, devolvido ao modelo."""

    output: str = ""
    ok: bool = True

    @classmethod
    def success(cls, output: str) -> "ToolResult":
        return cls(output=output, ok=True)

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(output=f"ERRO: {error}", ok=False)

    def as_text(self) -> str:
        return self.output


@dataclass
class ToolContext:
    """Ambiente de execução de uma ferramenta (injetado pelo Core no run)."""

    db: OrmSession | None = None
    provider: AIProvider | None = None
    session_id: str | None = None
    extras: dict = field(default_factory=dict)


class Tool(ABC):
    """Uma ferramenta concretizável e segura do JARVIS."""

    name: str = ""
    description: str = ""
    parameters: dict = {}
    permission_level: PermissionLevel = PermissionLevel.LEVEL_0
    risk: RiskLevel = RiskLevel.LOW

    @abstractmethod
    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        """Executa a ação e devolve o resultado para o loop do agente."""

    def declaration(self) -> ToolDeclaration:
        return ToolDeclaration(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )