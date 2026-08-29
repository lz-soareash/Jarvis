"""Testes do GeminiProvider — 100% mockados, sem chamadas reais à API."""

import pytest

from app.ai.providers.base import AIProviderError
from app.ai.providers.gemini import GeminiProvider
from app.schemas.ai import AIMessage


class FakeResponse:
    def __init__(self, text="resposta mockada"):
        self.text = text


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
        return FakeResponse()

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
        return FakeResponse()


class FakeClient:
    def __init__(self, models=None):
        self.models = models or FakeModels()


def make_provider(api_key="test-key", model="gemini-test", models=None):
    client = FakeClient(models=models)
    provider = GeminiProvider(api_key=api_key, model=model, client_factory=lambda k: client)
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
    assert call["contents"][0]["role"] == "user"
    assert call["contents"][0]["parts"][0]["text"] == "olá"


async def test_generate_maps_assistant_role_to_model():
    provider, client = make_provider()
    await provider.generate([AIMessage(role="assistant", content="resposta anterior")])
    call = client.models.calls[0]
    assert call["contents"][0]["role"] == "model"


async def test_generate_raises_when_not_configured():
    provider, _ = make_provider(api_key="")
    with pytest.raises(AIProviderError):
        await provider.generate([AIMessage(role="user", content="oi")])


async def test_analyze_uses_instruction_as_system():
    provider, client = make_provider()
    await provider.analyze("resuma isto", instruction="seja objetivo")
    call = client.models.calls[0]
    assert call["contents"][0]["parts"][0]["text"] == "resuma isto"
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