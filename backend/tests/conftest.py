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


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c