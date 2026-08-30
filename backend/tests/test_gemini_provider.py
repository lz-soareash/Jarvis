"""Testes do GeminiProvider — 100% mockados, sem chamadas reais à API."""

import pytest

from app.ai.providers.base import AIProviderError
from app.ai.providers.gemini import GeminiProvider
from app.schemas.ai import AIMessage, ToolCall, ToolDeclaration
from app.tools.base import ToolResult


class FakeFunctionCall:
    def __init__(self, name, args=None, call_id=None, thought_signature=None):
        self.name = name
        self.args = args or {}
        self.id = call_id
        self.thought_signature = thought_signature


class FakePart:
    def __init__(self, text=None, function_call=None):
        self.text = text
        self.function_call = function_call


class FakeContent:
    def __init__(self, parts):
        self.parts = parts


class FakeCandidate:
    def __init__(self, parts):
        self.content = FakeContent(parts)


class FakeResponse:
    def __init__(self, text="resposta mockada", candidates=None):
        self.text = text
        self.candidates = candidates


class FakeContentEmbedding:
    def __init__(self, values):
        self.values = values


class FakeEmbedResponse:
    def __init__(self, values):
        self.embeddings = [FakeContentEmbedding(values)]


class FakeModels:
    def __init__(self):
        self.calls = []
        self.stream_calls = []
        self.embed_calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(
            text="resposta mockada",
            candidates=[FakeCandidate([FakePart(text="resposta mockada")])],
        )

    def generate_content_stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return iter([FakeResponse(text="um "), FakeResponse(text="dois ")])

    def embed_content(self, **kwargs):
        self.embed_calls.append(kwargs)
        return FakeEmbedResponse([0.1, 0.2, 0.3])


class FakeModelsNoStream:
    """SDK antigo/simples: apenas geração única (verifica o fallback)."""

    def __init__(self):
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(
            text="resposta mockada",
            candidates=[FakeCandidate([FakePart(text="resposta mockada")])],
        )


class FakeModelsApiError:
    """Simula indisponibilidade temporária (HTTP 503) da API Gemini."""

    def __init__(self):
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        from google.genai.errors import ServerError

        raise ServerError(
            503,
            {
                "error": {
                    "code": 503,
                    "message": "This model is currently experiencing high demand.",
                    "status": "UNAVAILABLE",
                }
            },
        )


class FakeModelsRetrying:
    """Falha com 429 por `failures` chamadas; o resto responde normal."""

    def __init__(self, failures=1):
        self.failures = failures
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            from google.genai.errors import ServerError

            payload = {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "Quota exceeded. Please retry in 0.05s.",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "0.05s",
                        }
                    ],
                }
            }
            error = ServerError(429, payload)
            error.response_json = payload
            raise error
        return FakeResponse(
            text="resposta após retry",
            candidates=[FakeCandidate([FakePart(text="resposta após retry")])],
        )


class FakeClient:
    def __init__(self, models=None):
        self.models = models or FakeModels()


def make_provider(api_key="test-key", model="gemini-test", models=None, max_retries=None):
    client = FakeClient(models=models)
    provider = GeminiProvider(
        api_key=api_key, model=model, client_factory=lambda k: client, max_retries=max_retries
    )
    return provider, client


async def test_generate_returns_structured_response():
    provider, client = make_provider()
    result = await provider.generate(
        [AIMessage(role="user", content="olá")],
        system="seja breve",
        temperature=0.2,
        max_tokens=50,
    )
    assert result.text == "resposta mockada"
    assert result.provider == "gemini"
    assert result.model == "gemini-test"

    call = client.models.calls[0]
    assert call["model"] == "gemini-test"
    assert call["contents"][0].role == "user"
    assert call["contents"][0].parts[0].text == "olá"


async def test_generate_maps_assistant_role_to_model():
    provider, client = make_provider()
    await provider.generate([AIMessage(role="assistant", content="resposta anterior")])
    call = client.models.calls[0]
    assert call["contents"][0].role == "model"


async def test_generate_raises_when_not_configured():
    provider, _ = make_provider(api_key="")
    with pytest.raises(AIProviderError):
        await provider.generate([AIMessage(role="user", content="oi")])


async def test_generate_converts_api_error_to_provider_error():
    provider, _ = make_provider(models=FakeModelsApiError(), max_retries=0)
    with pytest.raises(AIProviderError) as exc_info:
        await provider.generate([AIMessage(role="user", content="oi")])
    assert "503" in str(exc_info.value)
    assert "high demand" in str(exc_info.value)


async def test_generate_retries_on_quota_and_succeeds():
    provider, client = make_provider(models=FakeModelsRetrying(failures=2), max_retries=5)
    result = await provider.generate([AIMessage(role="user", content="oi")])
    assert result.text == "resposta após retry"
    assert len(client.models.calls) == 3


async def test_generate_gives_up_after_max_retries():
    provider, client = make_provider(models=FakeModelsRetrying(failures=99), max_retries=2)
    with pytest.raises(AIProviderError) as exc_info:
        await provider.generate([AIMessage(role="user", content="oi")])
    assert "429" in str(exc_info.value)
    assert len(client.models.calls) == 3


async def test_analyze_uses_instruction_as_system():
    provider, client = make_provider()
    await provider.analyze("resuma isto", instruction="seja objetivo")
    call = client.models.calls[0]
    assert call["contents"][0].parts[0].text == "resuma isto"
    assert call["config"].system_instruction == "seja objetivo"


async def test_embed_returns_vector():
    provider, client = make_provider()
    vec = await provider.embed("oi")
    assert vec == [0.1, 0.2, 0.3]
    assert client.models.embed_calls[0]["contents"] == "oi"
    assert provider.embed_model.startswith("text-embedding")


async def test_embed_raises_when_not_configured():
    provider, _ = make_provider(api_key="")
    with pytest.raises(AIProviderError):
        await provider.embed("oi")


async def test_stream_falls_back_when_sdk_lacks_streaming():
    provider, _ = make_provider(models=FakeModelsNoStream())
    chunks = []
    async for chunk in provider.stream([AIMessage(role="user", content="oi")]):
        chunks.append(chunk)
    assert chunks == ["resposta mockada"]


async def test_stream_uses_token_streaming_when_available():
    provider, client = make_provider()
    chunks = []
    async for chunk in provider.stream([AIMessage(role="user", content="oi")]):
        chunks.append(chunk)
    assert chunks == ["um ", "dois "]
    assert len(client.models.stream_calls) == 1


async def test_health_check_ok_with_client():
    provider, client = make_provider()
    status = await provider.health_check()
    assert status.status == "ok"
    assert status.provider == "gemini"
    assert status.model == "gemini-test"
    assert client.models.calls[0]["contents"] == "ping"


async def test_health_check_unconfigured_does_not_call_api():
    provider, client = make_provider(api_key="")
    status = await provider.health_check()
    assert status.status == "unconfigured"
    assert client.models.calls == []


async def test_provider_healthcheck_swallows_errors():
    provider, client = make_provider()

    def explode(**kwargs):
        raise ConnectionError("network down")

    client.models.generate_content = explode
    status = await provider.health_check()
    assert status.status == "error"
    assert "network down" in (status.detail or "")


async def test_generate_proposes_tool_calls():
    provider, client = make_provider()
    recorded = {}

    def fake_generate(**kwargs):
        recorded.update(kwargs)
        return FakeResponse(
            candidates=[
                FakeCandidate(
                    [FakePart(function_call=FakeFunctionCall("get_current_time", {}, "call-1"))]
                )
            ]
        )

    client.models.generate_content = fake_generate
    result = await provider.generate(
        [AIMessage(role="user", content="que horas são?")],
        tools=[ToolDeclaration(name="get_current_time", description="hora atual")],
    )
    assert result.text == ""
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.name == "get_current_time"
    assert call.call_id == "call-1"
    assert call.arguments == {}
    assert recorded["config"].tools is not None


async def test_generate_captures_thought_signature_and_roundtrips():
    provider, client = make_provider()
    calls = []

    def fake_generate(**kwargs):
        calls.append(kwargs)
        return FakeResponse(
            candidates=[
                FakeCandidate(
                    [
                        FakePart(
                            function_call=FakeFunctionCall(
                                "get_current_time",
                                {"zone": "localtime"},
                                "call-t1",
                                thought_signature=b"sig\x00abc123",
                            )
                        )
                    ]
                )
            ]
        )

    client.models.generate_content = fake_generate
    first = await provider.generate(
        [AIMessage(role="user", content="que horas são?")],
        tools=[ToolDeclaration(name="get_current_time", description="hora atual")],
    )
    call = first.tool_calls[0]
    assert call.thought_signature == "c2lnAGFiYzEyMw=="

    # Reenvio do turno completo (assistant function_call + tool response)
    messages = [
        AIMessage(role="user", content="que horas são?"),
        *provider.tool_result_message(
            [call],
            [ToolResult.success("12:00")],
        ),
    ]
    await provider.generate(
        messages,
        tools=[ToolDeclaration(name="get_current_time", description="hora atual")],
    )
    contents = calls[1]["contents"]
    roles = [c.role for c in contents]
    assert roles == ["user", "model", "user"]
    model_parts = contents[1].parts
    assert model_parts[0].function_call.id == "call-t1"
    assert model_parts[0].thought_signature == b"sig\x00abc123"


async def test_function_call_without_signature_serializes_plain():
    provider, client = make_provider()
    messages = provider.tool_result_message(
        [ToolCall(name="get_current_time", arguments={}, call_id="call-x")],
        [ToolResult.success("12:00")],
    )
    await provider.generate(
        messages,
        tools=[ToolDeclaration(name="get_current_time", description="hora atual")],
    )
    model_parts = client.models.calls[0]["contents"][0].parts
    assert model_parts[0].function_call.name == "get_current_time"
    assert model_parts[0].thought_signature is None


async def test_tool_result_message_pairs_calls_and_results():
    provider, _ = make_provider()
    messages = provider.tool_result_message(
        [ToolCall(name="get_current_time", call_id="call-1")],
        [ToolResult.success("12:00")],
    )
    assert messages[0].role == "assistant"
    assert messages[0].tool_calls[0].call_id == "call-1"
    assert messages[1].role == "tool"
    assert messages[1].tool_name == "get_current_time"
    assert messages[1].tool_call_id == "call-1"
    assert messages[1].content == "12:00"


async def test_contents_serialize_function_roundtrip():
    provider, client = make_provider()
    messages = [
        AIMessage(role="user", content="que horas são?"),
        *provider.tool_result_message(
            [ToolCall(name="get_current_time", arguments={}, call_id="call-9")],
            [ToolResult.failure("fuso indisponível")],
        ),
    ]
    await provider.generate(
        messages,
        tools=[ToolDeclaration(name="get_current_time", description="hora atual")],
    )
    contents = client.models.calls[0]["contents"]
    roles = [c.role for c in contents]
    assert roles == ["user", "model", "user"]
    model_parts = contents[1].parts
    assert model_parts[0].function_call.name == "get_current_time"
    assert model_parts[0].function_call.id == "call-9"
    assert model_parts[0].thought_signature is None
    user_parts = contents[2].parts
    assert user_parts[0].function_response.id == "call-9"
    assert user_parts[0].function_response.response["result"] == "ERRO: fuso indisponível"