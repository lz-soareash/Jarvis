import asyncio
import base64
import re
from typing import AsyncIterator, Callable

from app.core.config import settings
from app.schemas.ai import AIMessage, AIProviderStatus, AIResponse, ToolCall, ToolDeclaration

from .base import AIProvider, AIProviderError

_ROLE_MAP = {"user": "user", "assistant": "model", "tool": "user"}


def _normalize_thought_signature(value):
    """SDK devolve `thought_signature` como bytes (proto); a API espera base64-string."""
    if not value:
        return None
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return str(value)


def _parse_duration(value) -> float | None:
    """Converte durações da API (`16.1s`, `500ms`, `2m`) em segundos."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([\d.]+)\s*(ms|s|m)?", value.strip().lower())
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "ms":
        return amount / 1000.0
    if unit == "m":
        return amount * 60.0
    return amount


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
        embed_model: str | None = None,
        max_retries: int | None = None,
        client_factory: Callable[[str], object] | None = None,
    ):
        self._api_key = api_key if api_key is not None else (settings.gemini_api_key or "")
        self.model = model or settings.gemini_model
        self.embed_model = embed_model or settings.gemini_embed_model
        self.max_retries = max_retries if max_retries is not None else settings.ai_max_retries
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

    def _build_config(self, system, temperature, max_tokens, tools=None):
        from google.genai import types

        if (
            system is None
            and temperature is None
            and max_tokens is None
            and not tools
        ):
            return None
        declarations = None
        if tools:
            declarations = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters=t.parameters or None,
                )
                for t in tools
            ]
        return types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
            tools=(
                [types.Tool(function_declarations=declarations)]
                if declarations is not None
                else None
            ),
        )

    @staticmethod
    def _parts(message: AIMessage) -> list:
        from google.genai import types

        parts: list = []
        if message.role == "tool":
            if message.content:
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=message.tool_name or "",
                            id=message.tool_call_id,
                            response={"result": message.content},
                        )
                    )
                )
            return parts
        if message.content:
            parts.append(types.Part(text=message.content))
        for call in message.tool_calls:
            kwargs: dict = {
                "function_call": types.FunctionCall(
                    name=call.name,
                    args=call.arguments or {},
                    id=call.call_id,
                )
            }
            if call.thought_signature:
                try:
                    # ToolCall guarda base64 (JSON-safe); Part quer bytes (proto).
                    kwargs["thought_signature"] = base64.b64decode(call.thought_signature)
                except Exception:  # noqa: BLE001 — assinatura inválida não bloqueia o turno
                    pass
            parts.append(types.Part(**kwargs))
        return parts

    @staticmethod
    def _contents(messages: list[AIMessage]) -> list:
        from google.genai import types

        return [
            types.Content(role=_ROLE_MAP[m.role], parts=GeminiProvider._parts(m))
            for m in messages
        ]

    @staticmethod
    def _extract_parts(response) -> list:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []
        content = getattr(candidates[0], "content", None)
        return list(getattr(content, "parts", None) or [])

    @classmethod
    def _parse(cls, response) -> AIResponse:
        text = ""
        tool_calls: list[ToolCall] = []
        for part in cls._extract_parts(response):
            part_text = getattr(part, "text", None)
            if part_text:
                text += part_text
            fc = getattr(part, "function_call", None)
            if fc is not None:
                args = getattr(fc, "args", None)
                if not isinstance(args, dict):
                    args = dict(args or {})
                tool_calls.append(
                    ToolCall(
                        name=getattr(fc, "name", ""),
                        arguments=args,
                        call_id=getattr(fc, "id", None),
                        thought_signature=_normalize_thought_signature(
                            getattr(fc, "thought_signature", None)
                            or getattr(part, "thought_signature", None)
                        ),
                    )
                )
        return text, tool_calls

    @staticmethod
    def _api_error_message(exc: Exception) -> str:
        from google.genai.errors import APIError as GenAIAPIError

        if isinstance(exc, GenAIAPIError):
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", None) or str(exc)
            prefix = f"[{code}] " if code else ""
            return f"Erro da API Gemini {prefix}{message}"
        return f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _retry_wait(exc: Exception) -> float | None:
        """Atraso que a API pede antes de uma nova tentativa (retryDelay/Retry-After)."""
        payload = getattr(exc, "response_json", None)
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            for detail in error.get("details") or []:
                if isinstance(detail, dict):
                    parsed = _parse_duration(detail.get("retryDelay"))
                    if parsed is not None:
                        return parsed
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None and hasattr(headers, "get") and headers.get("retry-after"):
            try:
                return float(headers["retry-after"])
            except (TypeError, ValueError):
                pass
        match = re.search(r"retry in ([\d.]+)\s*s", str(exc), re.IGNORECASE)
        if match:
            return float(match.group(1))
        return _parse_duration(str(exc))

    async def _call_with_retry(self, call, retryable_codes=(429, 502, 503, 504)) -> object:
        attempts = 0
        while True:
            try:
                return await call()
            except Exception as exc:  # noqa: BLE001 — decide entre aguardar ou propagar
                from google.genai.errors import APIError as GenAIAPIError

                wait = self._retry_wait(exc)
                if wait is None:
                    code = getattr(exc, "code", None) if isinstance(exc, GenAIAPIError) else None
                    if code not in retryable_codes:
                        raise AIProviderError(self._api_error_message(exc)) from exc
                    wait = min(1.5 * (2 ** attempts), 20.0)
                if attempts >= self.max_retries:
                    raise AIProviderError(self._api_error_message(exc)) from exc
                await asyncio.sleep(min(max(wait, 0.05), 30.0))
                attempts += 1

    async def generate(
        self,
        messages: list[AIMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDeclaration] | None = None,
    ) -> AIResponse:
        if not self.is_configured:
            raise AIProviderError("GEMINI_API_KEY não configurada (arquivo .env)")

        client = self._get_client()
        config = self._build_config(system, temperature, max_tokens, tools)
        contents = self._contents(messages)

        async def _call():
            return await asyncio.to_thread(
                client.models.generate_content,
                model=self.model,
                contents=contents,
                config=config,
            )

        response = await self._call_with_retry(_call)
        text, tool_calls = self._parse(response)
        return AIResponse(
            text=text,
            provider=self.name,
            model=self.model,
            tool_calls=tool_calls,
        )

    def tool_result_message(
        self,
        tool_calls: list[ToolCall],
        results: list,
    ) -> list[AIMessage]:
        if len(tool_calls) != len(results):
            raise ValueError("tool_calls e results devem ter o mesmo tamanho")
        messages = [AIMessage(role="assistant", tool_calls=tool_calls)]
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
        """Streaming token-a-token via SDK oficial (com fallback para geração única)."""
        if not self.is_configured:
            raise AIProviderError("GEMINI_API_KEY não configurada (arquivo .env)")

        client = self._get_client()
        config = self._build_config(system, temperature, max_tokens)
        stream_method = getattr(client.models, "generate_content_stream", None)
        if stream_method is None:
            result = await self.generate(
                messages, system=system, temperature=temperature, max_tokens=max_tokens
            )
            yield result.text
            return

        try:
            stream = stream_method(
                model=self.model,
                contents=self._contents(messages),
                config=config,
            )
            iterator = iter(stream)
            while True:
                chunk = await asyncio.to_thread(next, iterator, None)
                if chunk is None:
                    break
                text = getattr(chunk, "text", None) or ""
                if text:
                    yield text
        except Exception as exc:  # noqa: BLE001 — erros da API viram AIProviderError legível
            raise AIProviderError(self._api_error_message(exc)) from exc

    async def analyze(self, text: str, *, instruction: str | None = None) -> AIResponse:
        return await self.generate([AIMessage(role="user", content=text)], system=instruction)

    async def embed(self, text: str) -> list[float]:
        if not self.is_configured:
            raise AIProviderError("GEMINI_API_KEY não configurada (arquivo .env)")

        client = self._get_client()
        response = await asyncio.to_thread(
            client.models.embed_content,
            model=self.embed_model,
            contents=text,
        )
        embeddings = getattr(response, "embeddings", None)
        if not embeddings or not getattr(embeddings[0], "values", None):
            raise AIProviderError("Embedding vazio do modelo de vetores")
        return list(embeddings[0].values)

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