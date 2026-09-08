"""Endpoints da VEGA — identidade e presença (Fase 19.5).

Identidade oficial user-facing é `VEGA`; o nome técnico interno permanece JARVIS.
Presença é DERIVADA de sinais reais (nunca fingida) — ver `app.services.presence`.
Nenhum endpoint aqui expõe secrets, conteúdo de mensagens ou permissões.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.db.session import get_db
from app.services import presence

router = APIRouter(prefix="/api/vega", tags=["vega"])


@router.get("/identity")
def vega_identity() -> dict:
    """Resumo sanitizado da identidade (nome de exibição + persona)."""
    from app.identity import get_identity, to_dict

    profile = get_identity()
    data = to_dict()
    data["name"] = profile.name or "VEGA"
    data["internal_name"] = "JARVIS"
    data["kind"] = "agente pessoal multiplataforma (local-first)"
    data["display_name"] = profile.name
    return data


@router.get("/state")
def vega_state(db: OrmSession = Depends(get_db)) -> dict:
    """Estado de presença corrente, derivado de sinais reais (veja o serviço)."""
    return presence.compute_presence(db)


@router.get("/state/labels")
def vega_state_labels() -> dict:
    """Vocabulário de estados suportados (para o frontend renderizar sem hardcode)."""
    return {
        "states": list(presence.valid_states),
        "labels": presence.valid_labels(),
        "enabled": bool(settings.vega_presence_enabled),
    }