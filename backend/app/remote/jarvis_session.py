"""Mapeamento device remoto → Sessão JARVIS (Fase 12.3).

O `ApprovalRequest` exige FK para `sessions.id` (a sessão de conversa do JARVIS).
Para comandos remotos, criamos UMA sessão JARVIS estável por device (não por
conexão): permite rastrear approvals e auditoria de forma contínua entre
reconnects/emparelhamentos, sem multiplicar sessões a cada conexão.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session as OrmSession

from app.models import Device, Session as JarvisSession
from app.models.session import utcnow

logger = logging.getLogger("jarvis.remote.jarvis")


def get_or_create_jarvis_session(db: OrmSession, device: Device) -> str:
    """Retorna o `jarvis_session_id` do device, criando a sessão na primeira vez.

    A associação é persistida em `Device.jarvis_session_id`. Retorna o id da
    sessão JARVIS (tabela `sessions`) usada como alvo de approvals/audit.
    """
    if device.jarvis_session_id is not None:
        existing = db.get(JarvisSession, device.jarvis_session_id)
        if existing is not None:
            return existing.id

    jarvis = JarvisSession(
        title=f"Controle remoto — {device.name}",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(jarvis)
    db.flush()
    device.jarvis_session_id = jarvis.id
    db.commit()
    return jarvis.id
