"""Mensagens conversacionais remotas (Fase 16) — REUTILIZAM o AI Core.

Um request remoto de mensagem NÃO implementa um segundo agente: ele autentica a
identidade, valida a sessão, aplica limites e então entrega o conteúdo no MESMO
fluxo do chat local (`ai_core.handle_message`). Isso significa que:

- o provider continua sendo responsabilidade do AI Router (local/determinístico/
  Gemini — indiferente ao Remote);
- tools respeitam o Permission Engine e approvals existentes (sem bypass);
- a memória/contexto é a MESMA (`jarvis_session` estável por device);
- streaming reaproveita o SSE existente, apenas enriquecido com `request_id`
  e `session_id` para correlação.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from sqlalchemy.orm import Session as OrmSession

from app.ai import core as ai_core
from app.ai.providers.base import AIProvider
from app.api.remote_deps import RemoteRequestContext
from app.models import Session as JarvisSession
from app.remote.auth import AuthenticatedDevice
from app.remote.errors import RemoteError, RemoteErrorCode
from app.remote.events import publish_event
from app.remote.jarvis_session import get_or_create_jarvis_session
from app.remote.limits import acquire_slot, check_message_rate, release_slot
from app.remote.sessions import ensure_session_valid
from app.services import audit as audit_service
from app.services import chat as chat_service
from app.services.chat import sse_event

logger = logging.getLogger("jarvis.remote.message")

# Vocabulário de auditoria/ops das mensagens remotas (Fase 16).
AUDIT_REMOTE_MESSAGE = "remote.message"
OPS_REMOTE_MESSAGE = "remote.message"

# Máximo de caracteres de uma única mensagem remota (defesa em profundidade;
# o limite físico de bytes é aplicado pelo payload guard HTTP).
MAX_REMOTE_MESSAGE_LENGTH = 20_000


class RemoteMessageError(ValueError):
    """Falha sanitizada no envio/execução de mensagem remota."""


async def handle_remote_message(
    db: OrmSession,
    *,
    authed: AuthenticatedDevice,
    content: str,
    stream: bool,
    tools: bool,
    ctx: RemoteRequestContext,
    requested: AIProvider | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Orquestra uma mensagem remota até o AI Core.

    Retorna um dict que o endpoint converte em JSON (`{"kind":"reply",...}`) ou
    SSE (`{"kind":"stream","generator":...,"jarvis_session_id":...}`). Eleva
    `RemoteError` em falhas já sanitizadas; nunca expõe stack traces.

    `session_id` (Fase 21) habilita a session sharing: quando informado, a
    mensagem é roteada para a sessão JARVIS de um device CONFIÁVEL do mesmo Core
    (continuação entre dispositivos). O contexto continua centralizado no Core;
    o cliente finito nunca recebe cópia de memória.
    """
    content = _cap_content(content)

    # Limites + sessão válida primeiro (TTL/estado/device) — a sessão é o
    # RESULTADO da autenticação, nunca o segredo.
    check_message_rate(authed.device_id)
    ensure_session_valid(db, authed.session)
    ctx.session_id = authed.session_id
    ctx.device_id = authed.device_id
    ctx.credential_id = authed.credential.id

    jarvis_session_id = resolve_conversation(db, authed, session_id)

    # Concorrência: respeita o teto de mensagens simultâneas (Fase 16).
    if not acquire_slot():
        raise RemoteError(
            RemoteErrorCode.RATE_LIMITED,
            "muitas mensagens simultâneas; aguarde a conclusão atual",
        )
    try:
        turn = await _run_turn(
            db,
            jarvis_session_id,
            content,
            stream=stream,
            tools=tools,
            requested=requested,
        )
    finally:
        release_slot()

    return _compose(db, turn, jarvis_session_id, ctx)


async def _run_turn(
    db: OrmSession,
    jarvis_session_id: str,
    content: str,
    *,
    stream: bool,
    tools: bool,
    requested: AIProvider | None = None,
) -> ai_core.CoreTurn:
    session = db.get(JarvisSession, jarvis_session_id)
    if session is None:
        raise RemoteMessageError("sessão de conversa remota não encontrada")
    chat_service.add_user_message(db, session, content)
    # MESMO fluxo do chat local: provider é responsabilidade do AI Router.
    return await ai_core.handle_message(
        db,
        session_id=jarvis_session_id,
        content=content,
        tools=tools,
        stream=stream,
        requested=requested,
    )


def _compose(
    db: OrmSession,
    turn: ai_core.CoreTurn,
    jarvis_session_id: str,
    ctx: RemoteRequestContext,
) -> dict[str, Any]:
    """Converte o `CoreTurn` em resposta JSON ou indicador de stream."""
    if turn.kind in ("atlas", "agent", "stream"):
        if turn.generator is None:
            raise RemoteMessageError("resposta em stream indisponível")
        return {
            "kind": "stream",
            "generator": _remote_sse(turn.generator, ctx, jarvis_session_id),
            "jarvis_session_id": jarvis_session_id,
        }

    if turn.kind == "unconfigured":
        raise RemoteError(
            RemoteErrorCode.INTERNAL_ERROR,
            "nenhum provedor de IA configurado",
            http_status=503,
            detail=turn.error,
        )
    if turn.kind == "provider_error":
        raise RemoteError(
            RemoteErrorCode.INTERNAL_ERROR,
            "provedor de IA indisponível no momento",
            http_status=502,
            detail=turn.error,
        )

    # kind == "reply"
    message = turn.message or getattr(turn, "message", None)
    content = getattr(message, "content", "") or ""
    audit_service.log_action(
        db,
        action=AUDIT_REMOTE_MESSAGE,
        session_id=jarvis_session_id,
        allowed=True,
        detail=ctx.audit_detail("status=completed"),
    )
    publish_event(
        OPS_REMOTE_MESSAGE,
        {
            **ctx.sse_meta(),
            "jarvis_session": jarvis_session_id,
            "status": "completed",
        },
    )
    body = {
        "type": "response",
        "request_id": ctx.request_id,
        "session_id": jarvis_session_id,
        "status": "completed",
        "content": content,
    }
    return {"kind": "reply", "body": body}


async def _remote_sse(
    generator: AsyncIterator[str], ctx: RemoteRequestContext, jarvis_session_id: str
) -> AsyncIterator[str]:
    """Enriquece o SSE existente com `request_id`/`session_id` (correlação).

    Mantém o contrato SSE do frontend (`start`/`chunk`/`done`) intacto: apenas
    adiciona request_id/session_id ao JSON de cada evento. Itens não-JSON
    passam intactos (compatibilidade com o path existente).

    O `session_id` injetado é o da **sessão de conversa JARVIS** (`jarvis_session_id`)
    — a MESMA âncora devolvida pelo caminho não-stream (`_compose`) e aceita de
    volta em `resolve_conversation`. Usar o id da sessão REMOTA de transporte
    (`ctx.session_id`) corrompia a âncora: o cliente o reenviava no próximo
    envio e o Core o recusava ("sessão de conversa não disponível para
    continuação"), quebrando turns consecutivas por HTTP/SSE.
    """
    async for item in generator:
        try:
            line = item[6:] if item.startswith("data: ") else item
            payload = json.loads(line.strip())
        except (ValueError, AttributeError):
            yield item
            continue
        if isinstance(payload, dict):
            payload["request_id"] = ctx.request_id
            if "session_id" not in payload:
                payload["session_id"] = jarvis_session_id
            yield sse_event(payload)
        else:
            yield item


def _cap_content(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        raise RemoteMessageError("mensagem vazia")
    if len(content) > MAX_REMOTE_MESSAGE_LENGTH:
        raise RemoteMessageError("mensagem acima do limite de caracteres")
    return content.strip()


def resolve_conversation(
    db: OrmSession, authed: AuthenticatedDevice, session_id: str | None
) -> str:
    """Fase 21 — resolve a conversa-alvo de uma mensagem remota (session sharing).

    Sem `session_id`: sessão JARVIS estável do próprio device (comportamento
    histórico, Fase 12.3).

    Com `session_id`: a conversa deve existir e pertencer a um device CONFIÁVEL
    (PAIRED/ACTIVE) do MESMO Core — é assim que "continue no celular a conversa
    do PC". Regras aplicadas:

    - sessão inexistente ou de device não confiável → erro sanitizado (mesma
      mensagem genérica; não revela o que existe ou a quem pertence);
    - o próprio device continua confiável (já autenticou) — sem elevação;
    - emite `remote.session.shared` (auditoria + SSE) para rastreabilidade.
    """
    if session_id is None:
        return get_or_create_jarvis_session(db, authed.device)

    from sqlalchemy import select

    from app.models.remote import Device as RemoteDevice
    from app.remote.identity_events import (
        AUDIT_SESSION_SHARED,
        OPS_SESSION_SHARED,
        log_identity_event,
    )

    session = db.get(JarvisSession, session_id)
    if session is None:
        raise RemoteMessageError("sessão de conversa não disponível para continuação")

    owner = db.scalar(
        select(RemoteDevice).where(RemoteDevice.jarvis_session_id == session_id)
    )
    if owner is None or not owner.is_trusted:
        raise RemoteMessageError("sessão de conversa não disponível para continuação")

    own = session_id == get_or_create_jarvis_session(db, authed.device)
    log_identity_event(
        audit_action=AUDIT_SESSION_SHARED,
        ops_event=OPS_SESSION_SHARED,
        meta={
            "session_id": session_id,
            "source_device_id": authed.device_id,
            "target_device_id": owner.id,
            "same_device": own,
        },
    )
    return session_id