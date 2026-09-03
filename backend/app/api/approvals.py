from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
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

    approval, decided = approval_service.mark_decided(db, approval, approved=body.approved)

    # Fase 12.4 — aprovação de um comando REMOTO: retoma o comando exato do
    # device (persiste de forma transacional e entrega best-effort à Gateway).
    # NÃO roda o loop do agente local: o comando remoto é executado pelo
    # pipeline interno e o resultado é consultável via /remote/commands.
    #
    # Fase 12.4-R1: `mark_decided` é ATÔMICO (WHERE status=pending), então sob
    # decisões concorrentes apenas uma efetiva; o comando é localizado por
    # `approval_id` em QUALQUER estado, evitando que um pedido concorrente caia
    # no loop local do agente; e `mark_applied` só ocorre APÓS a retomada bem
    # sucedida (nunca `applied=True` com comando ainda PENDING_APPROVAL).
    from app.models import Device
    from app.remote import resume as resume_service
    from app.remote.jarvis_session import get_or_create_jarvis_session
    from app.remote.runtime import deliver_command_result

    command = resume_service.resolve_command_for_approval(db, approval.id)
    if command is not None:
        device = db.get(Device, command.device_id)
        if device is None or not device.is_active:
            raise HTTPException(status_code=409, detail="Comando remoto inválido: device não ativo")
        jarvis_session_id = get_or_create_jarvis_session(db, device)
        try:
            result = await resume_service.resume_remote_command(
                db,
                command=command,
                device=device,
                jarvis_session_id=jarvis_session_id,
                approval_status=approval.status,
            )
        except resume_service.RemoteResumeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        approval_service.mark_applied(db, approval)
        deliver_command_result(device.id, command.command_id, result)
        return JSONResponse({"status": "decided", "command_id": command.command_id, "result": result})

    if not decided:
        raise HTTPException(status_code=409, detail="Pedido de aprovação já decidido")

    generator = agent.run_agent(approval.session_id, provider, db, user_text="")
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)