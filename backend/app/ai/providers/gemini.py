import asyncio
from typing import AsyncIterator, Callable

from app.core.config import settings
from app.schemas.ai import AIMessage, AIProviderStatus, AIResponse

from .base import AIProvider, AIProviderError

_ROLE_MAP = {"user": "user", "assistant": "model"}


class GeminiProvider(AIProvider):
    """Implementação concreta do Google Gemini via google-genai (SDK oficial).

    O acesso ao SDK é isolado em `_default_client` e nas chamadas de `_client`,
    de forma que o restante do sistema dependa apenas de `AIProvider`.
    """

    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client_factory: Callable[[str], object] | None = None,
    ):
        self._api_key = api_key if api_key is not None else (settings.gemini_api_key or "")
        self.model = model or settings.gemini_model
        self._client_factory = client_factory or self._default_client
        self._client = None

    @staticmethod
    def _default_client(api_key: str):
        from google import genai

        return genai.Client(api_key=api_key)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _get_client(self):
        if self._client is None:
            self._client = self._client_factory(self._api_key)
        return self._client

    def _build_config(self, system, temperature, max_tokens):
        from google.genai import types

        if system is None and temperature is None and max_tokens is None:
            return None
        return types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

    @staticmethod
    def _contents(messages: list[AIMessage]) -> list[dict]:
        return [
            {"role": _ROLE_MAP[m.role], "parts": [{"text": m.content}]}
            for m in messages
        ]

    async def generate(
        self,
        messages: list[AIMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AIResponse:
        if not self.is_configured:
            raise AIProviderError("GEMINI_API_KEY não configurada (arquivo .env)")

        client = self._get_client()
        config = self._build_config(system, temperature, max_tokens)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=self.model,
            contents=self._contents(messages),
            config=config,
        )
        text = getattr(response, "text", None) or ""
        return AIResponse(text=text, provider=self.name, model=self.model)

    async def stream(
        self,
        messages: list[AIMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        # Contrato async já definido; streaming token-a-token chega em fase futura.
        result = await self.generate(
            messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        yield result.text

    async def analyze(self, text: str, *, instruction: str | None = None) -> AIResponse:
        return await self.generate([AIMessage(role="user", content=text)], system=instruction)

    async def health_check(self) -> AIProviderStatus:
        if not self.is_configured:
            return AIProviderStatus(
                status="unconfigured",
                provider=self.name,
                model=self.model,
                detail="GEMINI_API_KEY não configurada (arquivo .env)",
            )
        try:
            client = self._get_client()
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model,
                contents="ping",
            )
            if response is None or getattr(response, "text", None) is None:
                return AIProviderStatus(
                    status="error",
                    provider=self.name,
                    model=self.model,
                    detail="Resposta vazia do modelo",
                )
            return AIProviderStatus(status="ok", provider=self.name, model=self.model, detail="ok")
        except Exception as exc:  # noqa: BLE001 — healthcheck reporta qualquer falha
            return AIProviderStatus(
                status="error",
                provider=self.name,
                model=self.model,
                detail=f"{type(exc).__name__}: {exc}",
            )