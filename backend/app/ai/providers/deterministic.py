"""Provedor determinístico local — Safety Fallback (Fase 11).

Não é um LLM generativo: é a camada de segurança que responde tarefas simples,
previsíveis e offline (horário, data, aritmética segura, saudação), mantendo o
JARVIS funcional quando nenhum provedor externo está configurado ou disponível.

A arquitetura do AI Router já está preparada para adicionar um **LLM local real**
no futuro (capacidade `tools`/`embed`), sem refatorar o AI Core: basta registrar
um provedor com `capabilities` mais amplas na ordem de prioridade.
"""

import ast
import operator
import re
from datetime import datetime, timezone
from typing import AsyncIterator

from app.schemas.ai import AIMessage, AIProviderStatus, AIResponse, ToolCall, ToolDeclaration

from .base import AIProvider, AIProviderError

# Capacidades deste provedor: conversa/stream/resumo, mas SEM tools e SEM embed
# (não propõe chamadas de ferramenta nem gera vetores semânticos confiáveis).
_CAPABILITIES = {"generate", "stream", "analyze"}

_ACCENTS = str.maketrans(
    "áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc"
)

_ARITHMETIC_RE = re.compile(r"^[\d+\-*/().,\s]+$")

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,  # type: ignore[arg-type]
    ast.Mod: operator.mod,
}


def _safe_eval(expression: str) -> float:
    """Avalia uma expressão aritmética simples e segura (whitelist de nós).

    Não usa `eval`; só aceita números e operadores básicos via AST restrito.
    """
    tree = ast.parse(expression, mode="eval")

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.operand))
        raise ValueError("expressão não suportada")

    result = _eval(tree.body)
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result


class DeterministicProvider(AIProvider):
    """Respostas determinísticas de segurança, sem dependência externa."""

    name = "deterministic"

    capabilities = _CAPABILITIES

    def __init__(self):
        self.model = "deterministic-local"

    @property
    def is_configured(self) -> bool:
        return True  # sempre disponível quando habilitado no AI Router

    # ------------------------------------------------------------------
    # Resolução da resposta
    # ------------------------------------------------------------------
    @staticmethod
    def _last_user_text(messages: list[AIMessage]) -> str:
        text = ""
        for m in reversed(messages):
            if m.role == "user" and m.content.strip():
                text = m.content.strip()
                break
        return text

    def _answer(self, text: str) -> str:
        if not text:
            return self._fallback_text()
        lowered = text.lower().translate(_ACCENTS)

        # Horário / data
        if any(w in lowered for w in ("hora", "horas", "quehrs")) or re.search(
            r"\bque\s+horas\b", lowered
        ):
            now = datetime.now(timezone.utc).astimezone()
            return f"São {now:%H:%M} no seu fuso local."
        if any(w in lowered for w in ("data", "dia", "hoje", "date")):
            now = datetime.now(timezone.utc).astimezone()
            return f"Hoje é {now:%d/%m/%Y} ({now:%A})."

        # Aritmética simples e segura
        candidate = text.strip().replace(" ", "")
        if (_ARITHMETIC_RE.match(candidate) and any(c in candidate for c in "+-*/%")):
            try:
                value = _safe_eval(candidate.replace(",", "."))
                return f"Resultado: {value}"
            except (ValueError, ZeroDivisionError):
                return "Não consegui calcular essa expressão."

        # Saudações
        if lowered in (
            "oi", "ola", "olá", "hello", "hi", "hey", "eai", "e ai", "talvez",
        ) or lowered.startswith(("bom dia", "boa tarde", "boa noite")):
            return "Olá! Estou no modo determinístico de segurança — posso te ajudar " \
                   "com informações do sistema, horário, memórias e tarefas simples."

        return self._fallback_text()

    @staticmethod
    def _fallback_text() -> str:
        return (
            "Estou em modo determinístico de segurança (sem modelo de IA externo "
            "configurado). Para respostas completas, configure uma chave "
            "(GEMINI_API_KEY) no .env do JARVIS ou ligue o Atlas."
        )

    # ------------------------------------------------------------------
    # Contrato AIProvider
    # ------------------------------------------------------------------
    async def generate(
        self,
        messages: list[AIMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDeclaration] | None = None,
    ) -> AIResponse:
        return AIResponse(
            text=self._answer(self._last_user_text(messages)),
            provider=self.name,
            model=self.model,
            tool_calls=[],
        )

    def tool_result_message(
        self,
        tool_calls: list[ToolCall],
        results: list,
    ) -> list[AIMessage]:
        # Nunca propõe chamadas; implementado para completar a interface.
        if len(tool_calls) != len(results):
            raise ValueError("tool_calls e results devem ter o mesmo tamanho")
        messages = [AIMessage(role="assistant", tool_calls=list(tool_calls))]
        for call, result in zip(tool_calls, results):
            output = getattr(result, "output", str(result))
            messages.append(
                AIMessage(
                    role="tool",
                    tool_name=call.name,
                    tool_call_id=call.call_id,
                    content=output,
                )
            )
        return messages

    async def stream(
        self,
        messages: list[AIMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        text = self._answer(self._last_user_text(messages))
        step = 24
        for i in range(0, len(text), step):
            yield text[i : i + step]

    async def analyze(self, text: str, *, instruction: str | None = None) -> AIResponse:
        clean = " ".join(text.split())
        snippet = clean[:240] + ("…" if len(clean) > 240 else "")
        return AIResponse(
            text=snippet,
            provider=self.name,
            model=self.model,
        )

    async def embed(self, text: str) -> list[float]:
        raise AIProviderError(
            "Embeddings não suportados pelo provedor determinístico (sem vetores confiáveis)."
        )

    async def health_check(self) -> AIProviderStatus:
        return AIProviderStatus(
            status="ok",
            provider=self.name,
            model=self.model,
            detail="modo determinístico (sem API externa)",
        )