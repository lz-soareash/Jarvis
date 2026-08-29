"""Fixtures e ambiente para testes.

As variáveis de ambiente são forçadas ANTES de qualquer import do app,
garantindo que os testes nunca toquem credenciais reais nem o banco real.
"""

import os

os.environ["ENV"] = "test"
os.environ["GEMINI_API_KEY"] = ""
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["LOG_LEVEL"] = "ERROR"

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.ai import AIProviderStatus, AIResponse


class FakeProvider:
    """Provedor de IA determinístico para testes (sem rede)."""

    name = "fake"

    def __init__(self, reply: str = "resposta fake"):
        self.reply = reply
        self.generate_calls = 0
        self.stream_calls = 0

    @property
    def is_configured(self) -> bool:
        return True

    async def generate(self, messages, **kwargs) -> AIResponse:
        self.generate_calls += 1
        return AIResponse(text=self.reply, provider=self.name, model="fake-model")

    async def stream(self, messages, **kwargs):
        self.stream_calls += 1
        for piece in [self.reply[:5], self.reply[5:]]:
            yield piece

    async def analyze(self, text, **kwargs) -> AIResponse:
        return AIResponse(text=self.reply, provider=self.name, model="fake-model")

    async def health_check(self) -> AIProviderStatus:
        return AIProviderStatus(status="ok", provider=self.name, model="fake-model")


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fake_ai():
    """Override da dependency get_ai_provider com um FakeProvider."""
    from app.api.deps import get_ai_provider

    provider = FakeProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(get_ai_provider, None)