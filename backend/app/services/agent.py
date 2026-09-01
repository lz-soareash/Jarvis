"""Agente (Tool Engine — Fases 3, 4 e 11.2): modelo propõe chamadas, Core decide e executa.

Loop determinístico e limitado: a cada rodada o provedor pode propor N chamadas
de ferramenta; o Core valida contra o registro (nunca executa chamadas
inventadas), aplica a política de permissão e devolve os resultados até o
modelo responder por texto. Tudo transcorre via SSE.

Fase 4 — Permissions: ferramentas com nível ≥ 2 geram um pedido de aprovação e
o turno pausa (`approval_pending`). O usuário decide pela API e o `run_agent`
retoma, transformando a decisão em resultado de ferramenta para o modelo.

Fase 11.2 — Intent Detection: quando o LLM não gera tool_calls mas o texto
contém uma solicitação claramente operacional, a camada determinística de
detecção de intenção verifica e executa a ferramenta apropriada.
"""

import json
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


def _call_content_key(call: ToolCall) -> str:
    """Chave determinística de conteúdo de uma tool call (Fase 11.3).

    Usada para deduplicação DENTRO do mesmo turno: mesma (name, arguments)
    proposta de novo não deve ser re-executada neste turno. Vale SÓ para o
    turno corrente — não bloqueia execuções futuras em outros turnos.
    """
    try:
        args = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        args = str(call.arguments)
    return f"{call.name}:{args}"


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
    *,
    executed_this_turn: dict[str, str] | None = None,
    executions: list[dict] | None = None,
) -> tuple[list[ToolResult], list[object]]:
    """Aplica a política: executa autorizadas; cria aprovações para as sensíveis.

    Retorna (resultados das executadas, pedidos de aprovação criados). Ferramentas
    com nível efetivo ≥ 2 NÃO são executadas aqui — viram `ApprovalRequest`.

    Fase 11.3 (#2): se `executed_this_turn` for fornecido, a mesma tool call
    (mesmo name+arguments) já executada com sucesso NESTE turno não é re-executada
    — vira um resultado de bloqueio determinístico registrado na auditoria. A
    trilha de execuções é acumulada em `executions` (execution_id único por ação)
    para persistência no metadata da resposta.
    """
    results: list[ToolResult] = []
    pending: list[object] = []
    for call in calls:
        tool = tool_registry.get_tool_registry().get(call.name)
        if tool is None:
            results.append(
                ToolResult.failure(
                    f"A ferramenta '{call.name}' não está registrada no Core."
                )
            )
            continue

        level = permission_service.effective_level(db, call.name)

        # Deduplicação determinística dentro do mesmo turno (não bloqueia outros turnos).
        if executed_this_turn is not None and level <= PermissionLevel.LEVEL_1:
            key = _call_content_key(call)
            if key in executed_this_turn:
                audit_service.log_action(
                    db,
                    action="tool.duplicate_blocked",
                    session_id=session_id,
                    tool=call.name,
                    allowed=False,
                    detail=f"tool já executada neste turno (execution={executed_this_turn[key]})",
                )
                results.append(
                    ToolResult.failure(
                        "Esta ação já foi executada neste turno e não será repetida."
                    )
                )
                continue

        if level > PermissionLevel.LEVEL_1:
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

        result = await _run_tool(db, session_id, provider, call)

        # Registra execution_id único + trilha p/ persistência.
        if executed_this_turn is not None and level <= PermissionLevel.LEVEL_1:
            key = _call_content_key(call)
            execution_id = _new_execution_id()
            executed_this_turn[key] = execution_id
            if executions is not None:
                executions.append(
                    {
                        "execution_id": execution_id,
                        "call_id": call.call_id,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "ok": result.ok,
                        "output": getattr(result, "output", "") if result.ok else None,
                        "error": None if result.ok else getattr(result, "output", ""),
                    }
                )
        results.append(result)
    return results, pending


def _new_execution_id() -> str:
    import uuid

    return f"ex_{uuid.uuid4().hex[:12]}"


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

    # Fase 11.3 (#1/#14) — expõe métricas de contexto ao Central de Operações.
    # Estimativa de alto nível (heurística) do que será enviado ao modelo,
    # usando o mesmo limite aplicado pelo context budget no provider.
    try:
        from app.ai import context as ctx_service

        n_ctx = settings.ai_local_llm_n_ctx
        used = sum(
            ctx_service.estimate_input_tokens(m.content or "")
            for m in messages
        )
        tools_tokens = sum(
            len(d.name or "")
            + len(ctx_service.json.dumps(d.parameters or {}, ensure_ascii=False))
            for d in declarations
        )
        used += int(tools_tokens / 2) + 64  # margem p/ declarações de tools
        critical_hit = used > n_ctx
        from app.services import ops as ops_service

        ops_service.record_event(
            db,
            session_id=session_id,
            event_type="context.budget",
            provider=getattr(provider, "name", None),
            meta={
                "used_tokens_est": used,
                "limit_tokens": n_ctx,
                "truncated_est": critical_hit,
            },
        )
    except Exception:  # noqa: BLE001 — métricas nunca quebram o turno
        logger.debug("Falha ao registrar métricas de contexto", exc_info=True)

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
    _intent_triggered = False
    # Fase 11.3 (#2) — idempotência + trilha de execuções do turno.
    _executed_this_turn: dict[str, str] = {}  # content_key -> execution_id
    _tool_executions: list[dict] = []  # persistidos no metadata da resposta

    # Fase 11.3 (#4) — Intent Detection como camada determinística PRIMÁRIA.
    # Para operações de computador claramente reconhecidas, executa direto via
    # Tool Registry -> Permission Engine -> execução (SEM bypass de Registry/
    # Permission/Audit). Só deixa o LLM gerar a resposta final em seguida. Isso
    # também reduz latência (#6) ao não fazer geração LLM desnecessária primeiro.
    from app.ai.intent import detect_intent
    from app.core.config import settings as _settings

    _primary_intent = detect_intent(user_text) if _settings.local_first else None
    try:
        if _primary_intent is not None and _primary_intent.tool_call.name in [
            t.name for t in tool_registry.get_tool_registry().all()
        ]:
            _intent_triggered = True
            logger.info(
                "Intent primária: %s (conf=%.2f) — execução determinística",
                _primary_intent.tool_call.name,
                _primary_intent.confidence,
            )
            audit_service.log_action(
                db,
                action="intent.detected",
                session_id=session_id,
                tool=_primary_intent.tool_call.name,
                allowed=None,
                detail=f"confidence={_primary_intent.confidence:.2f} (primária)",
            )
            yield sse_event(
                {
                    "type": "tool_start",
                    "round": 0,
                    "names": [_primary_intent.tool_call.name],
                }
            )
            results, pending = await _execute_tool_calls(
                db, session_id, provider, [_primary_intent.tool_call],
                executed_this_turn=_executed_this_turn,
                executions=_tool_executions,
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
            for call, result in zip([_primary_intent.tool_call], results):
                event: dict = {"type": "tool_done", "name": call.name, "ok": result.ok}
                if result.ok:
                    event["output"] = result.output
                else:
                    event["detail"] = result.output
                yield sse_event(event)
            # Re-injeta o resultado para o LLM gerar a resposta final.
            messages.extend(
                provider.tool_result_message([_primary_intent.tool_call], results)
            )
    except AIProviderError as exc:
        logger.warning("Agente interrompido na intent primária: %s", exc)
        yield sse_event({"type": "error", "detail": str(exc)})
        return

    try:
        for round_index in range(1, settings.max_tool_rounds + 1):
            response = await provider.generate(messages, system=system, tools=declarations)

            if not response.tool_calls:
                # Fase 11.3 (#4): a Intent primária já foi tratada antes do
                # loop (camada determinística). Se o LLM não gerou tool_call,
                # a tarefa é conversacional/ambígua — responde por texto.
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
                db, session_id, provider, response.tool_calls,
                executed_this_turn=_executed_this_turn,
                executions=_tool_executions,
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
    # Fase 11.3 (#2): persiste a trilha de tool executions do turno no metadata,
    # permitindo reconstruir o histórico informando ao modelo quais tools já foram
    # executadas (evita re-execução de tool calls antigos entre turnos).
    meta: dict = {}
    if _tool_executions:
        meta["tools_executed"] = _tool_executions
    message = Message(
        session_id=session_id,
        role="assistant",
        content=final_text,
        metadata_json=json.dumps(meta, ensure_ascii=False) if meta else None,
    )
    db.add(message)
    if session is not None:
        session.updated_at = utcnow()
    db.commit()
    db.refresh(message)

    yield sse_event({"type": "chunk", "text": final_text})
    yield sse_event(
        {"type": "done", "message": MessageOut.model_validate(message).model_dump(mode="json")}
    )