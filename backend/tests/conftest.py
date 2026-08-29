"""Fixtures e ambiente para testes.

As variáveis de ambiente são forçadas ANTES de qualquer import do app,
garantindo que os testes nunca toquem credenciais reais nem o banco real.
"""

import os

os.environ["ENV"] = "test"
os.environ["GEMINI_API_KEY"] = ""
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["LOG_LEVEL"] = "ERROR"

import re
import unicodedata

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.ai import AIProviderStatus, AIResponse


def _normalize_token(token: str) -> str:
    return unicodedata.normalize("NFD", token).encode("ascii", "ignore").decode()


EMBED_VOCAB = ["jarvis", "cafe", "musica", "python", "gosto", "ajuda", "codigo", "jogo"]


def embed_vector(text: str) -> list[float]:
    """Vetorizador determinístico (bag-of-words) p/ testes de similaridade."""
    import math

    tokens = [_normalize_token(t) for t in re.findall(r"[^\W\d_]+", text.lower())]
    vector = [0.0] * len(EMBED_VOCAB)
    for token in tokens:
        if token in EMBED_VOCAB:
            vector[EMBED_VOCAB.index(token)] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class FakeProvider:
    """Provedor de IA determinístico para testes (sem rede)."""

    name = "fake"

    def __init__(self, reply: str = "resposta fake"):
        self.reply = reply
        self.generate_calls = 0
        self.stream_calls = 0
        self.analyze_calls = 0
        self.embed_calls = 0
        self.last_generate_kwargs = None
        self.last_stream_kwargs = None
        self.last_analyze_text = None

    @property
    def is_configured(self) -> bool:
        return True

    async def generate(self, messages, **kwargs) -> AIResponse:
        self.generate_calls += 1
        self.last_generate_kwargs = kwargs
        return AIResponse(text=self.reply, provider=self.name, model="fake-model")

    async def stream(self, messages, **kwargs):
        self.stream_calls += 1
        self.last_stream_kwargs = kwargs
        for piece in [self.reply[:5], self.reply[5:]]:
            yield piece

    async def analyze(self, text, **kwargs) -> AIResponse:
        self.analyze_calls += 1
        self.last_analyze_text = text
        return AIResponse(text="resumo fake", provider=self.name, model="fake-model")

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return embed_vector(text)

    async def health_check(self) -> AIProviderStatus:
        return AIProviderStatus(status="ok", provider=self.name, model="fake-model")


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_db():
    """Isola o banco in-memory compartilhado: limpa as tabelas a cada teste."""
    from app.db.session import SessionLocal
    from app.models import Memory, Message, Session

    db = SessionLocal()
    try:
        db.rollback()
        db.query(Memory).delete()
        db.query(Message).delete()
        db.query(Session).delete()
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def fake_ai():
    """Override da dependency get_ai_provider com um FakeProvider."""
    from app.api.deps import get_ai_provider

    provider = FakeProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(get_ai_provider, None)