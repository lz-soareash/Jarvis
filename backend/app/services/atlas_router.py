"""Roteamento modular JARVIS ↔ Atlas (Fase 10).

Camada de decisão entre o Atlas (inteligência externa) e o fluxo local (Gemini).
Em `POST /sessions/{id}/messages`, o JARVIS pergunta a esta camada se deve
delegar ao Atlas. Se o Atlas responder, retorna o resultado tipado; se estiver
desabilitado, não configurado ou indisponível, retorna `None` para o chamador
seguir com o fallback local atual — com logging explícito do fallback, sem
mascarar problemas nem expor segredos.

A lógica de decisão fica isolada aqui para evoluir sem reescrever o endpoint.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.schemas.atlas import AtlasChatResponse

from .atlas_client import (
    AtlasAuthError,
    AtlasConfigError,
    AtlasUnavailable,
    get_atlas_client,
)

logger = logging.getLogger("jarvis.atlas.router")


def should_route_to_atlas() -> bool:
    """Indica se o roteamento para o Atlas deve ser considerado.

    Primeiro filtro barato (sem rede): habilitado + credenciais presentes.
    """
    if not settings.atlas_enabled:
        return False
    return bool(settings.atlas_email and settings.atlas_password)


def build_atlas_messages(db: OrmSession, session_id: str, query: str) -> list[dict[str, Any]]:
    """Monta o histórico de mensagens a enviar ao Atlas.

    Usa a mesma janela de contexto recente do JARVIS, no formato que o
    `/assistant/chat/` do Atlas espera: [{ "role": "user|assistant", "content" }].
    """
    from .chat import build_ai_messages

    history = build_ai_messages(db, session_id)
    return [
        {"role": m.role, "content": m.content}
        for m in history
        if m.role in ("user", "assistant")
    ]


def route(db: OrmSession, session_id: str, query: str) -> AtlasChatResponse | None:
    """Tenta responder via Atlas; retorna `None` para o fallback local.

    - Não lança exceção: qualquer falha vira fallback local (None) + log.
    - Se o Atlas responder, retorna a resposta tipada.
    """
    if not should_route_to_atlas():
        if settings.atlas_enabled:
            logger.info(
                "Atlas habilitado mas não configurado (sem credenciais) — usando fluxo local."
            )
        return None

    try:
        messages = build_atlas_messages(db, session_id, query)
        client = get_atlas_client()
        response = client.chat(messages)
        logger.info(
            "Resposta via Atlas: provider=%s agent_run=%s"
            " fontes=%d proposals=%d",
            response.provider,
            (response.agent_run.id if response.agent_run else None),
            len(response.sources),
            len(response.proposals),
        )
        return response
    except AtlasUnavailable as exc:
        logger.warning("Atlas indisponível — fallback local: %s", exc)
        return None
    except AtlasAuthError as exc:
        logger.warning("Atlas: falha de autenticação — fallback local: %s", exc)
        return None
    except AtlasConfigError as exc:
        logger.warning("Atlas: configuração inválida — fallback local: %s", exc)
        return None
    except Exception:  # noqa: BLE001 — nunca derruba o chat por causa do Atlas
        logger.exception("Atlas: erro inesperado — fallback local")
        return None
