"""Agente (Tool Engine — Fases 3 e 4): modelo propõe chamadas, Core decide e executa.

Loop determinístico e limitado: a cada rodada o provedor pode propor N chamadas
de ferramenta; o Core valida contra o registro (nunca executa chamadas
inventadas), aplica a política de permissão e devolve os resultados até o
modelo responder por texto. Tudo transcorre via SSE.

Fase 4 — Permissions: ferramentas com nível ≥ 2 geram um pedido de aprovação e
o turno pausa (`approval_pending`). O usuário decide pela API e o `run_agent`
retoma, transformando a decisão em resultado de ferramenta para o modelo.
"""

import logging
from typing import AsyncIterator

from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider, AIProviderError
from app.core.config import settings
from app.core.enums import ApprovalStatus, PermissionLevel
from app.models import Message, Session, utcnow
from app.schemas.ai import ToolCall
from app.schemas.chat import MessageOut
from app.services import approvals as approval_service
from app.services import audit as audit_service
from app.services import permissions as permission_service
from app.services.chat import build_context, sse_event
from app.tools import ToolContext, ToolResult
from app.tools import registry as tool_registry

logger = logging.getLogger("jarvis.agent")


async def _run_tool(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    call: ToolCall,
) -> ToolResult:
    """Executa uma chamada já autorizada e registra na auditoria."""
    tool = tool_registry.get_tool_registry().get(call.name)
    if tool is None:
        return ToolResult.failure(
            f"A ferramenta '{call.name}' não está registrada no Core."
        )
    context = ToolContext(db=db, provider=provider, session_id=session_id)
    try:
        result = await tool.run(context, **call.arguments)
    except Exception as exc:  # noqa: BLE001 — falha vira resultado p/ o modelo
        logger.exception("Falha ao executar a ferramenta %s", call.name)
        result = ToolResult.failure(f"{type(exc).__name__}: {exc}")
    audit_service.log_action(
        db,
        action="tool.execute",
        session_id=session_id,
        tool=call.name,
        allowed=result.ok,
        detail=result.output,
    )
    return result


async def _execute_tool_calls(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    calls: list[ToolCall],
) -> tuple[list[ToolResult], list[object]]:
    """Aplica a política: executa autorizadas; cria aprovações para as sensíveis.

    Retorna (resultados das executadas, pedidos de aprovação criados). Ferramentas
    com nível efetivo ≥ 2 NÃO são executadas aqui — viram `ApprovalRequest`.
    """
    results: list[ToolResult] = []
    pending: list[object] = []
    for call in calls:
        if tool_registry.get_tool_registry().get(call.name) is None:
            results.append(
                ToolResult.failure(
                    f"A ferramenta '{call.name}' não está registrada no Core."
                )
            )
            continue
        level = permission_service.effective_level(db, call.name)
        if level > PermissionLevel.LEVEL_1:
            tool = tool_registry.get_tool_registry().get(call.name)
            approval = approval_service.create_approval(
                db,
                session_id=session_id,
                tool_name=call.name,
                arguments=call.arguments,
                permission_level=level.value,
                risk=(tool.risk.value if tool is not None else "low"),
            )
            audit_service.log_action(
                db,
                action="tool.approve_requested",
                session_id=session_id,
                tool=call.name,
                allowed=None,
                detail=f"nível {level.value} — decisão do usuário",
            )
            pending.append(approval)
            continue
        results.append(await _run_tool(db, session_id, provider, call))
    return results, pending


async def _collect_decisions(
    db: OrmSession,
    session_id: str,
    provider: AIProvider,
    messages: list,
) -> tuple[list, list[ToolCall], list[ToolResult]]:
    """Aplica decisões pendentes do usuário (retomada do turno pausado).

    Cada pedido já decidido (approved/denied) vira um par assistant/tool no
    histórico, exatamente como se tivesse sido respondido pelo modelo. Assim o
    loop continua de onde parou, sem re-propor a mesma chamada.
    """
    decisions = approval_service.decided_unapplied_for_session(db, session_id)
    if not decisions:
        return messages, [], []

    calls: list[ToolCall] = []
    results: list[ToolResult] = []
    for decision in decisions:
        approval_service.mark_applied(db, decision)
        call = ToolCall(
            name=decision.tool_name,
            arguments=decision.arguments_dict,
            call_id=decision.id,
        )
        calls.append(call)
        if decision.status == ApprovalStatus.APPROVED.value:
            result = await _run_tool(db, session_id, provider, call)
            audit_service.log_action(
                db,
                action="tool.approved",
                session_id=session_id,
                tool=call.name,
                allowed=True,
                detail=f"aprovação {decision.id}",
            )
        else:
            result = ToolResult.failure(
                "O usuário negou a execução desta ferramenta. Informe a negação "
                "e sugerir uma alternativa segura."
            )
            audit_service.log_action(
                db,
                action="tool.decided",
                session_id=session_id,
                tool=call.name,
                allowed=False,
                detail=f"negação {decision.id}",
            )
        results.append(result)

    messages.extend(provider.tool_result_message(calls, results))
    return messages, calls, results


async def run_agent(
    session_id: str,
    provider: AIProvider,
    db: OrmSession,
    user_text: str,
) -> AsyncIterator[str]:
    """Executa o ciclo do agente emitindo eventos SSE para o cliente."""
    declarations = tool_registry.get_tool_registry().declarations()

    yield sse_event({"type": "start"})

    history, system = await build_context(db, session_id, provider, query=user_text)
    messages = [*history]
    messages, resumed_calls, resumed_results = await _collect_decisions(
        db, session_id, provider, messages
    )
    if resumed_calls:
        yield sse_event(
            {
                "type": "tool_start",
                "round": 0,
                "names": [call.name for call in resumed_calls],
                "resume": True,
            }
        )
        for call, result in zip(resumed_calls, resumed_results):
            event: dict = {"type": "tool_done", "name": call.name, "ok": result.ok}
            if result.ok:
                event["output"] = result.output
            else:
                event["detail"] = result.output
            yield sse_event(event)

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
            results, pending = await _execute_tool_calls(
                db, session_id, provider, response.tool_calls
            )

            if pending:
                for approval in pending:
                    yield sse_event(
                        {
                            "type": "approval_request",
                            "approval": approval_service.to_out(approval).model_dump(mode="json"),
                        }
                    )
                yield sse_event(
                    {
                        "type": "approval_pending",
                        "count": len(pending),
                        "approvals": [
                            approval_service.to_out(a).model_dump(mode="json")
                            for a in pending
                        ],
                    }
                )
                audit_service.log_action(
                    db,
                    action="agent.paused",
                    session_id=session_id,
                    tool=", ".join(a.tool_name for a in pending),
                    allowed=None,
                    detail=f"aguardando decisão ({len(pending)})",
                )
                return

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