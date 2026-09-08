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

import inspect
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator

from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider, AIProviderError
from app.ai.registry import get_ai_router
from app.schemas.chat import MessageOut
from app.services import agent, chat as chat_service, ops, summarizer
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


# ---------------------------------------------------------------------------
# Fase 11.2 — Atlas condicional: detecta solicitações operacionais
# ---------------------------------------------------------------------------

# Padrões que indicam solicitação operacional (computer control)
_OP_PATTERNS = re.compile(
    r"\b(?:abra|abrir|abre|feche|fechar|fecha|toque|pause|pausar|mute|silenciar"
    r"|aumente|diminua|volume|bloquei|suspenda|reini?ci?e?|desligue|desligar"
    r"|quanto\s+(?:de\s+)?(?:ram|mem[óo]ria|CPU|disco|espa[çc]o)"
    r"|como\s+(?:est[áa]|vai)\s+(?:o\s+)?computador"
    r"|status\s+(?:do\s+)?computador|processos?|explorador|terminal|notepad"
    r"|vs\s*code|chrome|firefox|spotify|edge|powershell|cmd|youtube|github"
    r"|pr[óo]xima|anterior|track|sleep|shutdown|reboot|lock)\b",
    re.IGNORECASE,
)


def _is_operational_request(content: str) -> bool:
    """Verifica se o conteúdo é uma solicitação operacional (computer control).

    Retorna True se o texto contiver padrões claros de operação no computador,
    indicando que Atlas não precisa ser consultado (reduz latência).
    """
    if not content:
        return False
    return bool(_OP_PATTERNS.search(content))


async def _run_summary_best_effort(db: OrmSession, session_id: str, provider: AIProvider) -> None:
    """Resumo rolante (Fase 2) — best-effort, não bloqueia o turno.

    Fase 11.2: executa como tarefa em background via asyncio.create_task
    para não adicionar latência ao turno do chat.
    """
    import asyncio

    async def _do_summary():
        try:
            await summarizer.summarize_chunk(db, session_id=session_id, provider=provider)
        except Exception:  # noqa: BLE001 — resumo é best-effort
            logger.exception("Resumo periódico falhou (best-effort)")

    try:
        asyncio.create_task(_do_summary())
    except Exception:  # noqa: BLE001
        logger.debug("Não foi possível agendar resumo em background")


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
    if provider.name == "local":
        ops.record_event(
            db,
            session_id=session_id,
            event_type=ops.EVENT_LOCAL_STARTED,
            provider=provider.name,
            model=getattr(provider, "model", None),
            status="started",
        )

    await _run_summary_best_effort(db, session_id, provider)

    # Fase 11.2 — Atlas condicional: só consulta Atlas para consultas de
    # conhecimento/contexto. Para solicitações operacionais (computer control),
    # pula Atlas para reduzir latência. Fase 19.5 — Atlas é OPCIONAL: o import
    # é lazy (o desligamento/indisponibilidade nunca atinge o fluxo da VEGA).
    atlas_response = None
    if not _is_operational_request(content):
        from app.services.atlas_router import route as atlas_route

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
        generator = _sse_with_fallback(
            lambda p: agent.run_agent(session_id, p, db, content),
            db,
            session_id,
            "agent",
            provider=provider,
            router=router,
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
        generator = _sse_with_fallback(
            lambda p: _stream_for(p, db, session_id, content),
            db,
            session_id,
            "stream",
            provider=provider,
            router=router,
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
        # Failover de resiliência: tenta o próximo provider configurado.
        fallback = router.fallback_for(provider, task="generate")
        if fallback is not None and fallback is not provider:
            ops.record_event(
                db,
                session_id=session_id,
                event_type=ops.EVENT_FALLBACK,
                provider=fallback.name,
                status="fallback",
                meta=ops.json_safe_meta(
                    path="reply",
                    from_provider=provider.name,
                    error=type(exc).__name__,
                ),
            )
            logger.warning("Fallback (reply): %s -> %s", provider.name, fallback.name)
            fb_start = time.perf_counter()
            try:
                reply = await chat_service.generate_reply(
                    db, session_id, fallback, query=content
                )
            except AIProviderError as fb_exc:
                fb_latency = int((time.perf_counter() - fb_start) * 1000)
                ops.record_event(
                    db,
                    session_id=session_id,
                    event_type=ops.EVENT_FAILED,
                    provider=fallback.name,
                    status="failed",
                    latency_ms=fb_latency,
                    meta=ops.json_safe_meta(
                        path="reply", error=type(fb_exc).__name__
                    ),
                )
                _record_local_stage(
                    db, session_id, fallback, completed=False, latency_ms=fb_latency
                )
                return CoreTurn(
                    kind="provider_error", provider=provider, error=str(fb_exc)
                )
            fb_latency = int((time.perf_counter() - fb_start) * 1000)
            ops.record_event(
                db,
                session_id=session_id,
                event_type=ops.EVENT_COMPLETED,
                provider=fallback.name,
                status="ok",
                latency_ms=fb_latency,
                meta=ops.json_safe_meta(path="reply"),
            )
            _record_local_stage(
                db, session_id, fallback, completed=True, latency_ms=fb_latency
            )
            return CoreTurn(kind="reply", provider=fallback, message=reply)

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
        _record_local_stage(db, session_id, provider, completed=False, latency_ms=latency)
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
    _record_local_stage(db, session_id, provider, completed=True, latency_ms=latency)
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
        _record_local_stage(
            db, session_id, provider, completed=False, latency_ms=latency
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
    _record_local_stage(db, session_id, provider, completed=True, latency_ms=latency)


def _record_local_stage(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    *,
    completed: bool,
    latency_ms: int | None = None,
) -> None:
    """Espelha o ciclo do modelo local em eventos observáveis dedicados."""
    if provider.name != "local":
        return
    ops.record_event(
        db,
        session_id=session_id,
        event_type=(
            ops.EVENT_LOCAL_COMPLETED if completed else ops.EVENT_LOCAL_FAILED
        ),
        provider=provider.name,
        model=getattr(provider, "model", None),
        status="ok" if completed else "failed",
        latency_ms=latency_ms,
    )


async def _stream_for(provider: AIProvider, db: OrmSession, session_id: str, content: str):
    history, system = await build_context(db, session_id, provider, query=content)
    return chat_service.stream_reply(
        session_id, history, provider, db, system=system or None
    )


async def _sse_with_fallback(
    make_generator,
    db: OrmSession,
    session_id: str,
    path: str,
    *,
    provider: AIProvider,
    router,
):
    """Emite um SSE do provider primário; se ele falhar antes de responder
    (`{"type":"error"}` sem `done`), recomeça o turno com o próximo provider
    configurado (`fallback_for`) — reemitindo `start`/`chunk`/`done` limpo.

    Só recorre ao fallback quando o primário falhou antes de qualquer resposta
    textual (não houve `chunk` nem `done`), evitando respostas duplicadas.

    Fase 11.3 (#3): se uma ferramenta JÁ foi executada com sucesso no turno
    primário (`tool_done` com `ok:true`), o turno NÃO recorre ao fallback —
    o efeito colateral já ocorreu no computador e reprocessar com outro provider
    poderia RE-EXECUTAR a mesma tool. Nesse caso apenas o erro de texto é
    reportado.
    """
    errored = False
    tool_executed_ok = False

    async def _try(prov: AIProvider):
        nonlocal errored, tool_executed_ok
        value = make_generator(prov)
        gen = await value if inspect.isawaitable(value) else value
        async for item in gen:
            try:
                payload = json.loads(item.removeprefix("data: ").strip())
            except (ValueError, AttributeError):
                yield item
                continue
            if payload.get("type") == "error":
                errored = True
            elif payload.get("type") == "tool_done" and payload.get("ok") is True:
                tool_executed_ok = True
            yield item

    ok = False
    try:
        async for item in _try(provider):
            yield item
        # Sucesso = o prov primário não emitiu erro (respondeu ou concluiu).
        ok = not errored
    except Exception:  # noqa: BLE001 — erro de provedor durante o stream
        ok = False

    if ok:
        return

    # Não recomo o turno a outro provider se já houve efeito colateral (tool ok).
    if tool_executed_ok:
        logger.warning(
            "Fallback suprimido: ferramenta já executada com sucesso no turno "
            "primário (evita re-execução) — path=%s, provider=%s",
            path,
            provider.name,
        )
        return

    fallback = router.fallback_for(provider, task="generate")
    if fallback is None or fallback is provider:
        return

    ops.record_event(
        db,
        session_id=session_id,
        event_type="fallback_triggered",
        provider=fallback.name,
        status="fallback",
        meta=ops.json_safe_meta(path=path, from_provider=provider.name),
    )
    logger.warning(
        "Fallback de resiliência: %s -> %s (path=%s)",
        provider.name,
        fallback.name,
        path,
    )

    async for item in _try(fallback):
        yield item


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