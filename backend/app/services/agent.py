"""Agente (Tool Engine Fase 3): modelo propõe chamadas, Core decide e executa.

Loop determinístico e limitado: a cada rodada o provedor pode propor N chamadas
de ferramenta; o Core valida contra o registro (nunca executa chamadas
inventadas), aplica a política de permissão e devolve os resultados até o
modelo responder por texto. Tudo transcorre via SSE.
"""

import logging
from typing import AsyncIterator

from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider, AIProviderError
from app.core.config import settings
from app.core.enums import PermissionLevel
from app.models import Message, Session, utcnow
from app.schemas.ai import ToolCall
from app.schemas.chat import MessageOut
from app.services.chat import build_context, sse_event
from app.tools import ToolContext, ToolResult, get_tool_registry

logger = logging.getLogger("jarvis.agent")


async def _execute_tool_calls(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    calls: list[ToolCall],
) -> list[ToolResult]:
    """Executa as chamadas propostas seguindo a política de permissão."""
    registry = get_tool_registry()
    results: list[ToolResult] = []
    for call in calls:
        tool = registry.get(call.name)
        if tool is None:
            results.append(
                ToolResult.failure(f"A ferramenta '{call.name}' não está registrada no Core.")
            )
            continue
        if tool.permission_level > PermissionLevel.LEVEL_1:
            results.append(
                ToolResult.failure(
                    f"A ferramenta '{call.name}' requer permissão de nível "
                    f"{tool.permission_level.value} — aprovação chega na Fase 4."
                )
            )
            continue
        context = ToolContext(db=db, provider=provider, session_id=session_id)
        try:
            results.append(await tool.run(context, **call.arguments))
        except Exception as exc:  # noqa: BLE001 — falha vira resultado p/ o modelo
            logger.exception("Falha ao executar a ferramenta %s", call.name)
            results.append(ToolResult.failure(f"{type(exc).__name__}: {exc}"))
    return results


async def run_agent(
    session_id: str,
    provider: AIProvider,
    db: OrmSession,
    user_text: str,
) -> AsyncIterator[str]:
    """Executa o ciclo do agente emitindo eventos SSE para o cliente."""
    registry = get_tool_registry()
    declarations = registry.declarations()

    yield sse_event({"type": "start"})

    history, system = await build_context(db, session_id, provider, query=user_text)
    messages = [*history]

    final_text = ""
    try:
        for round_index in range(1, settings.max_tool_rounds + 1):
            response = await provider.generate(messages, system=system, tools=declarations)

            if not response.tool_calls:
                final_text = response.text
                break

            yield sse_event(
                {
                    "type": "tool_start",
                    "round": round_index,
                    "names": [call.name for call in response.tool_calls],
                }
            )
            results = await _execute_tool_calls(db, session_id, provider, response.tool_calls)
            messages.extend(provider.tool_result_message(response.tool_calls, results))

            for call, result in zip(response.tool_calls, results):
                event: dict = {"type": "tool_done", "name": call.name, "ok": result.ok}
                if result.ok:
                    event["output"] = result.output
                else:
                    event["detail"] = result.output
                yield sse_event(event)

            final_text = response.text or ""
    except AIProviderError as exc:
        logger.warning("Agente interrompido: %s", exc)
        yield sse_event({"type": "error", "detail": str(exc)})
        return
    except Exception:
        logger.exception("Erro não tratado no loop do agente")
        yield sse_event({"type": "error", "detail": "Erro interno no loop do agente."})
        return

    if not final_text:
        yield sse_event({"type": "done"})
        return

    session = db.get(Session, session_id)
    message = Message(session_id=session_id, role="assistant", content=final_text)
    db.add(message)
    if session is not None:
        session.updated_at = utcnow()
    db.commit()
    db.refresh(message)

    yield sse_event({"type": "chunk", "text": final_text})
    yield sse_event(
        {"type": "done", "message": MessageOut.model_validate(message).model_dump(mode="json")}
    )