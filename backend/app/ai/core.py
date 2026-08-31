"""AI Core (Fase 11): orquestrador central do JARVIS.

Conecta as peças existentes — AI Router (provedores), Atlas, Tool Engine/agente,
streaming, resposta única, memória/contexto e resumo — sem reescrevê-las. A cada
turno o Core:

1. registra `chat.started`;
2. resolve o provedor via AI Router (`provider.selected`);
3. decide o caminho (atlas/agent/stream/reply) reproduzindo o fluxo anterior;
4. embrulha a execução para registrar `chat.completed` / `chat.failed`
   com latência e metadados sanitizados (nunca conteúdo de mensagens/secrets).

Esses eventos (tabela `execution_events`) alimentam a Central de Operações.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator

from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider, AIProviderError
from app.ai.registry import get_ai_router
from app.schemas.chat import MessageOut
from app.services import agent, chat as chat_service, ops, summarizer
from app.services.atlas_router import route as atlas_route
from app.services.chat import build_context, sse_event

logger = logging.getLogger("jarvis.ai.core")


@dataclass
class CoreTurn:
    """Decisão do AI Core para o endpoint de chat.

    - kind `atlas`/`agent`/`stream`: `generator` é um stream SSE.
    - kind `reply`: `message` é a resposta persistida.
    - kind `unconfigured`: nenhum provedor disponível (error de configuração).
    - kind `provider_error`: o provedor falhou (error transitório).
    """

    kind: str
    provider: AIProvider
    generator: AsyncIterator[str] | None = None
    message: object | None = None
    error: str | None = None


async def _run_summary_best_effort(db: OrmSession, session_id: str, provider: AIProvider) -> None:
    """Resumo rolante (Fase 2) — best-effort, nunca interrompe o turno."""
    try:
        await summarizer.summarize_chunk(db, session_id=session_id, provider=provider)
    except Exception:  # noqa: BLE001 — resumo é best-effort
        logger.exception("Resumo periódico falhou (best-effort)")


async def handle_message(
    db: OrmSession,
    session_id: str,
    content: str,
    *,
    tools: bool,
    stream: bool,
    requested: AIProvider | None = None,
) -> CoreTurn:
    """Orquestra um turno do chat: provider (AI Router) + caminho + observabilidade."""
    router = get_ai_router()
    provider = requested if requested is not None else router.resolve(task="generate")

    # Observabilidade (sanitizada — sem conteúdo/mensagens/segredos).
    ops.record_event(
        db,
        session_id=session_id,
        event_type=ops.EVENT_STARTED,
        meta=ops.json_safe_meta(tools=bool(tools), stream=bool(stream)),
    )
    primary = router.primary(task="generate")
    fallback = requested is None and primary is not None and provider is not primary
    ops.record_event(
        db,
        session_id=session_id,
        event_type=ops.EVENT_PROVIDER,
        provider=provider.name,
        model=getattr(provider, "model", None),
        status="fallback" if fallback else "primary",
    )

    await _run_summary_best_effort(db, session_id, provider)

    # Fase 10 — Atlas como camada externa de inteligência (preservada).
    atlas_response = atlas_route(db, session_id, content)
    if atlas_response is not None:
        ops.record_event(
            db,
            session_id=session_id,
            event_type=ops.EVENT_PATH,
            provider=provider.name,
            meta=ops.json_safe_meta(path="atlas"),
        )
        generator = _observed(
            _atlas_sse(session_id, db, atlas_response),
            db,
            session_id,
            "atlas",
            provider=provider,
        )
        return CoreTurn(kind="atlas", provider=provider, generator=generator)

    if tools:
        ops.record_event(
            db,
            session_id=session_id,
            event_type=ops.EVENT_PATH,
            provider=provider.name,
            meta=ops.json_safe_meta(path="agent"),
        )
        generator = _observed(
            agent.run_agent(session_id, provider, db, content),
            db,
            session_id,
            "agent",
            provider=provider,
        )
        return CoreTurn(kind="agent", provider=provider, generator=generator)

    if stream:
        ops.record_event(
            db,
            session_id=session_id,
            event_type=ops.EVENT_PATH,
            provider=provider.name,
            meta=ops.json_safe_meta(path="stream"),
        )
        history, system = await build_context(db, session_id, provider, query=content)
        generator = _observed(
            chat_service.stream_reply(
                session_id, history, provider, db, system=system or None
            ),
            db,
            session_id,
            "stream",
            provider=provider,
        )
        return CoreTurn(kind="stream", provider=provider, generator=generator)

    # Resposta única (não-stream) — mesmo fluxo anterior (`generate_reply`).
    ops.record_event(
        db,
        session_id=session_id,
        event_type=ops.EVENT_PATH,
        provider=provider.name,
        meta=ops.json_safe_meta(path="reply"),
    )
    if not provider.is_configured:
        ops.record_event(
            db,
            session_id=session_id,
            event_type=ops.EVENT_FAILED,
            provider=provider.name,
            status="unconfigured",
        )
        return CoreTurn(
            kind="unconfigured",
            provider=provider,
            error="Nenhum provedor de IA configurado (GEMINI_API_KEY ou fallback determinístico).",
        )

    start = time.perf_counter()
    try:
        reply = await chat_service.generate_reply(db, session_id, provider, query=content)
    except AIProviderError as exc:
        latency = int((time.perf_counter() - start) * 1000)
        ops.record_event(
            db,
            session_id=session_id,
            event_type=ops.EVENT_FAILED,
            provider=provider.name,
            status="failed",
            latency_ms=latency,
            meta=ops.json_safe_meta(path="reply", error=type(exc).__name__),
        )
        return CoreTurn(kind="provider_error", provider=provider, error=str(exc))
    latency = int((time.perf_counter() - start) * 1000)
    ops.record_event(
        db,
        session_id=session_id,
        event_type=ops.EVENT_COMPLETED,
        provider=provider.name,
        status="ok",
        latency_ms=latency,
        meta=ops.json_safe_meta(path="reply"),
    )
    return CoreTurn(kind="reply", provider=provider, message=reply)


async def _observed(
    gen: AsyncIterator[str],
    db: OrmSession,
    session_id: str,
    path: str,
    *,
    provider: AIProvider,
) -> AsyncIterator[str]:
    """Embrulha um gerador SSE (síncrono ou assíncrono) p/ registrar métricas."""
    import inspect

    start = time.perf_counter()
    try:
        if inspect.isasyncgen(gen):
            async for item in gen:  # type: ignore[union-attr]
                yield item
        else:
            for item in gen:
                yield item
    except Exception as exc:  # noqa: BLE001 — registra e repassa o erro ao SSE
        latency = int((time.perf_counter() - start) * 1000)
        ops.record_event(
            db,
            session_id=session_id,
            event_type=ops.EVENT_FAILED,
            provider=provider.name,
            status="failed",
            latency_ms=latency,
            meta=ops.json_safe_meta(path=path, error=type(exc).__name__),
        )
        raise
    latency = int((time.perf_counter() - start) * 1000)
    ops.record_event(
        db,
        session_id=session_id,
        event_type=ops.EVENT_COMPLETED,
        provider=provider.name,
        status="ok",
        latency_ms=latency,
        meta=ops.json_safe_meta(path=path),
    )


# ---------------------------------------------------------------------------
# Atlas (Fase 10) — reemissão SSE + metadados (movido de api/chat.py p/ o Core)
# ---------------------------------------------------------------------------

def _atlas_meta(atlas_response) -> dict:
    """Metadados da resposta do Atlas persistidos junto à mensagem."""
    return {
        "source": "atlas",
        "provider": atlas_response.provider,
        "classification": (
            atlas_response.classification.model_dump()
            if atlas_response.classification is not None
            else None
        ),
        "sources": [s.model_dump() for s in atlas_response.sources],
        "proposals": [p.model_dump() for p in atlas_response.proposals],
        "agent_run": (
            atlas_response.agent_run.model_dump()
            if atlas_response.agent_run is not None
            else None
        ),
        "semantic_available": atlas_response.semantic_available,
    }


def _atlas_sse(session_id: str, db: OrmSession, atlas_response) -> AsyncIterator[str]:
    """Emite a resposta do Atlas como SSE compatível com o frontend do JARVIS.

    Persiste a resposta como mensagem do assistente com metadados (`source=atlas`)
    e reproduz o contrato SSE (`start`/`chunk`/`done`) que o frontend já consome,
    sem exigir flags do lado do usuário.
    """
    text = atlas_response.answer or ""

    if not text:
        yield sse_event({"type": "done"})
        return

    message = chat_service.add_atlas_reply(db, session_id, text, _atlas_meta(atlas_response))

    yield sse_event({"type": "start"})
    chunk_size = 120
    for i in range(0, len(text), chunk_size):
        yield sse_event({"type": "chunk", "text": text[i : i + chunk_size]})
    yield sse_event(
        {
            "type": "done",
            "message": json.loads(MessageOut.model_validate(message).model_dump_json()),
        }
    )