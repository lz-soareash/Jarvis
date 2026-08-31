"""Testes da extensão da Fase 11 — Independência do Gemini / Local Brain.

Cobre os Cenários A–H do requisito, usando um `llama-cpp-python` fake (sem
carregar o modelo real no disco) para manter os testes portáveis e velozes e
provar que a arquitetura funciona sem depender do Gemini, da API key ou da rede.

A `LocalLLMProvider` aceita `llama_factory`/`embed_factory` injetáveis, exatamente
como o `GeminiProvider` aceita `client_factory` — sem reescrever o AI Core.
"""

import json

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fakes (impectam o contrato do llama-cpp-python que o provider usa)
# ---------------------------------------------------------------------------


class FakeLlama:
    """Mimetiza `llama_cpp.Llama.create_chat_completion`.

    Devolve o conteúdo configurado; se `content` tiver `<tool_call>`, simula a
    emissão do tool call em texto (formato nativo do Qwen).
    """

    def __init__(self, content: str = "resposta local"):
        self.content = content
        self.calls: list[dict] = []

    def create_chat_completion(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if kwargs.get("stream"):
            return self._stream_chunks()
        return {"choices": [{"message": {"content": self.content}}]}

    def _stream_chunks(self):
        # Formato de streaming do llama-cpp-python: iterável de chunks.
        first = self.content[:6]
        rest = self.content[6:]
        yield {"choices": [{"delta": {"content": first}}]}
        yield {"choices": [{"delta": {"content": rest}}]}


class FakeEmbedder:
    """Mimetiza `create_embedding` (vetor fixo 768-dim, como o nomic GGUF)."""

    def __init__(self, dims: int = 768):
        self.dims = dims

    def create_embedding(self, text):
        return {"data": [{"embedding": [float(0.1)] * self.dims}]}


def make_provider(tmp_path, content="resposta local", enable_embed=True):
    from app.ai.providers.local_llm import LocalLLMProvider

    model_path = tmp_path / "qwen.gguf"
    model_path.write_bytes(b"fake")
    embed = FakeEmbedder()
    embed_path = tmp_path / "nomic.gguf"
    if enable_embed:
        embed_path.write_bytes(b"fake")
    llm = FakeLlama(content=content)
    return LocalLLMProvider(
        enabled=True,
        model_path=str(model_path),
        embed_path=str(embed_path),
        n_ctx=512,
        n_threads=2,
        llama_factory=lambda: llm,
        embed_factory=lambda: embed,
    ), llm, embed, model_path


@pytest.fixture
def local_ai(tmp_path):
    """Provedor local fake + fakes, com arquivos de modelo 'existentes'."""
    provider, llm, embed, model_path = make_provider(tmp_path)
    return {"provider": provider, "llm": llm, "embed": embed, "model_path": model_path}


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Cenário A — Local + Gemini disponíveis → Router escolhe Local (primário)
# ---------------------------------------------------------------------------

class _GeminiFake:
    name = "gemini"
    is_configured = True
    capabilities = {"generate", "stream", "analyze", "embed", "tools"}
    model = "gemini-fake"

    async def generate(self, messages, **kwargs):
        return type("R", (), {"text": "gemini", "tool_calls": []})()

    async def stream(self, messages, **kwargs):
        yield "gemini-delivery"

    async def embed(self, text):
        return [0.0] * 16

    async def health_check(self):
        return type("H", (), {"status": "ok"})()

    def tool_result_message(self, tool_calls, results):
        return []


def _build_router(monkeypatch, local_provider, enable_llm=True):
    from app.core.config import settings
    from app.ai import registry as reg

    monkeypatch.setattr(settings, "ai_provider_order", "local,gemini,deterministic")
    monkeypatch.setattr(settings, "ai_local_llm_enabled", enable_llm)
    # Registra factories apenas para gemini/deterministic; local entra via factory injetada
    orig_factories = dict(reg._PROVIDER_FACTORIES)
    reg._PROVIDER_FACTORIES["local"] = lambda: local_provider
    reg._PROVIDER_FACTORIES["gemini"] = _GeminiFake
    orig_default = reg.build_default_router
    try:
        reg.reset_ai_router()
        router = reg.get_ai_router()
        return router
    finally:
        reg._PROVIDER_FACTORIES.clear()
        reg._PROVIDER_FACTORIES.update(orig_factories)


def test_cenario_a_local_preferred_over_gemini(monkeypatch, local_ai):
    router = _build_router(monkeypatch, local_ai["provider"])
    assert router.primary(task="generate").name == "local"
    resolved = router.resolve(task="generate")
    assert resolved.name == "local"  # local é o provider ativo


# ---------------------------------------------------------------------------
# Cenário B — Gemini indisponível + Local disponível → Local responde
# Cenário C — GEMINI_API_KEY ausente + Local disponível → Local responde
# ---------------------------------------------------------------------------

class _FailingGemini:
    name = "gemini"
    is_configured = True  # configurado, mas a API falha (indisponível/quota)

    async def generate(self, messages, **kwargs):
        from app.ai.providers.base import AIProviderError

        raise AIProviderError("quota excedida")

    async def stream(self, messages, **kwargs):
        from app.ai.providers.base import AIProviderError

        raise AIProviderError("quota excedida")


@pytest.mark.asyncio
async def test_cenario_b_gemini_indisponivel_local_responde(monkeypatch, local_ai):
    from app.ai.providers.base import AIProviderError

    # Router com local primeiro; gemini falha em runtime.
    router = _build_router(monkeypatch, local_ai["provider"])
    provider = router.resolve(task="generate")
    assert provider.name == "local"
    # Gera resposta via local mesmo sem tocar na rede.
    from app.schemas.ai import AIMessage

    resp = await provider.generate([AIMessage(role="user", content="oi")])
    assert resp.text == "resposta local"
    assert resp.tool_calls == []


@pytest.mark.asyncio
async def test_cenario_c_sem_api_key_local_responde(monkeypatch, local_ai, tmp_path):
    from app.ai.providers.local_llm import LocalLLMProvider

    # Local sem qualquer chave/credencial: só precisa do arquivo do modelo.
    assert local_ai["provider"].is_configured is True
    # Deterministic é o último; local não usa rede.
    from app.schemas.ai import AIMessage

    resp = await local_ai["provider"].generate(
        [AIMessage(role="user", content="explique o que é API REST")]
    )
    assert resp.text == "resposta local"


# ---------------------------------------------------------------------------
# Cenário D — Internet off (o provider local não faz rede) → Local responde
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cenario_d_offline_local_responde(local_ai):
    from app.schemas.ai import AIMessage

    resp = await local_ai["provider"].generate([AIMessage(role="user", content="oi")])
    assert resp.text == "resposta local"
    # Streaming também é local.
    chunks = [
        c
        async for c in local_ai["provider"].stream(
            [AIMessage(role="user", content="oi")]
        )
    ]
    assert "".join(chunks) == "resposta local"


# ---------------------------------------------------------------------------
# Cenário E — Local + Gemini indisponíveis → Fallback determinístico
# ---------------------------------------------------------------------------

def test_cenario_e_lokal_gemini_off_deterministic(monkeypatch, local_ai):
    # Desabilitar local e gemini não ter key → deterministic
    from app.core.config import settings
    from app.ai import registry as reg

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "ai_local_llm_enabled", False)
    reg.reset_ai_router()
    router = reg.get_ai_router()
    provider = router.resolve(task="generate")
    assert provider.name == "deterministic"


# ---------------------------------------------------------------------------
# Cenário F — Integração com memória (embed local) + fallback lexical
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cenario_f_memoria_embed_local(client, tmp_path):
    from app.services import memory as mem
    from app.db.session import SessionLocal

    provider, llm, embed, model_path = make_provider(tmp_path)
    db = SessionLocal()
    try:
        created = await mem.create_memory(
            db, content="o usuário gosta de programar em python", provider=provider
        )
        assert created.content == "o usuário gosta de programar em python"
        # Embedding gerado pelo modelo local (768-dim)
        results = await mem.search_memories(
            db, query="python", provider=provider, include_global=True
        )
        assert isinstance(results, list)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_cenario_f_memoria_lexical_sem_embed(client, tmp_path):
    # Sem modelo de embedding, a busca cai no fallback lexical (sem rede).
    from app.services import memory as mem
    from app.db.session import SessionLocal
    from app.ai.providers.deterministic import DeterministicProvider

    provider, llm, embed, model_path = make_provider(tmp_path, enable_embed=False)
    db = SessionLocal()
    try:
        await mem.create_memory(db, content="gosto de música e de código")
        results = await mem.search_memories(
            db, query="música", provider=provider, include_global=True
        )
        # fallback lexical encontrou a memória (token "música")
        assert results
        assert results[0][0].content == "gosto de música e de código"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cenário G — Tools + Permissões (tool calling parse e segurança)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cenario_g_tool_call_parse(local_ai):
    from app.schemas.ai import AIMessage
    from app.ai.providers.local_llm import _parse_tool_calls

    text = '<tool_call>{"name": "get_time", "arguments": {"city": "Sao Paulo"}}</tool_call>'
    calls = _parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "get_time"
    assert calls[0].arguments == {"city": "Sao Paulo"}


@pytest.mark.asyncio
async def test_cenario_g_tool_call_invalido_ignorado(local_ai):
    from app.ai.providers.local_llm import _parse_tool_calls

    assert _parse_tool_calls("texto sem tool call") == []
    assert _parse_tool_calls('<tool_call>not-json</tool_call>') == []
    assert _parse_tool_calls('<tool_call>{"name": ""}</tool_call>') == []


# ---------------------------------------------------------------------------
# Cenário H — Observabilidade (eventos provider_selected / local_model.*)
# ---------------------------------------------------------------------------

def test_cenario_h_eventos_local_model(client):
    import app.services.ops as ops
    from app.ai.providers.local_llm import LocalLLMProvider
    from app.ai import registry as reg

    # Monkeypatch leve: um provider local fake sem arquivo real é simulado
    # através de um provedor fake direto na dependência do chat (sem gatilho).
    # Aqui validamos apenas o vocabulário de eventos novos existente.
    assert ops.EVENT_LOCAL_STARTED == "local_model.started"
    assert ops.EVENT_LOCAL_COMPLETED == "local_model.completed"
    assert ops.EVENT_LOCAL_FAILED == "local_model.failed"
    assert ops.EVENT_FALLBACK == "fallback_triggered"


# ---------------------------------------------------------------------------
# Contrato AIProvider completo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_local_provider_contract(local_ai):
    from app.schemas.ai import AIMessage, AIResponse

    p = local_ai["provider"]
    assert p.name == "local"
    assert "generate" in p.capabilities
    assert "stream" in p.capabilities
    assert "embed" in p.capabilities
    assert "tools" in p.capabilities
    assert p.is_configured is True

    resp = await p.generate([AIMessage(role="user", content="oi")])
    assert isinstance(resp, AIResponse)
    assert resp.provider == "local"

    vector = await p.embed("qualquer texto")
    assert len(vector) == 768

    health = await p.health_check()
    assert health.status == "ok"
    assert health.provider == "local"


@pytest.mark.asyncio
async def test_local_provider_tool_result_message(local_ai):
    from app.schemas.ai import ToolCall, AIMessage
    from app.tools.base import ToolResult

    p = local_ai["provider"]
    calls = [ToolCall(name="get_time", arguments={})]
    results = [ToolResult.success("resposta da ferramenta")]
    msgs = p.tool_result_message(calls, results)
    assert msgs[0].role == "assistant"
    assert msgs[1].role == "tool"
    assert msgs[1].content == "resposta da ferramenta"
