from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider
from app.core.enums import ApprovalStatus
from app.db.session import get_db
from app.schemas.approvals import ApprovalOut, ApprovalRespond
from app.services import agent
from app.services import approvals as approval_service
from app.services import chat as chat_service

from .deps import get_ai_provider

router = APIRouter(prefix="/api", tags=["approvals"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@router.get("/approvals/pending", response_model=list[ApprovalOut])
def list_pending(
    session_id: str | None = None,
    db: OrmSession = Depends(get_db),
) -> list[ApprovalOut]:
    """Pedidos de aprovação aguardando decisão (opcionalmente por sessão)."""
    if session_id and chat_service.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return [
        approval_service.to_out(a)
        for a in approval_service.pending_for_session(db, session_id)
    ]


@router.post("/approvals/{approval_id}/respond", response_class=StreamingResponse)
async def respond(
    approval_id: str,
    body: ApprovalRespond,
    db: OrmSession = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
):
    """Decide um pedido de aprovação e retoma o turno do agente (SSE).

    A decisão é registrada e o `run_agent` é reexecutado para que a resposta
    final flua em tempo real: pedidos aprovados aparecem como executions e os
    negados como recusas no contexto do modelo.
    """
    approval = approval_service.get_approval(db, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Pedido de aprovação não encontrado")
    if approval.status != ApprovalStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="Pedido de aprovação já decidido")
    if approval_service.is_expired(approval):
        raise HTTPException(status_code=410, detail="Pedido de aprovação expirou")

    approval_service.mark_decided(db, approval, approved=body.approved)

    generator = agent.run_agent(approval.session_id, provider, db, user_text="")
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)