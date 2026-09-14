"""Fase 28 — Remote Operations: API de operações coordenadas entre dispositivos.

Observabilidade e criação programática de operações (o caminho normal é a
ferramenta `remote_operation` do chat). Endpoints NÃO dependem do gate
REMOTE_ENABLED: operações locais (PC) funcionam sem pareamento remoto.

Nunca expõem secrets; steps/saídas são sanitizados pelo serviço.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from app.db.session import get_db
from app.remote import operations as ops_service
from app.schemas.operations import OperationCreateIn, OperationCreateOut, OperationOut

router = APIRouter(prefix="/api/remote", tags=["remote-operations"])


def _require_operation(db: OrmSession, operation_id: str) -> ops_service.RemoteOperation:
    operation = ops_service.load_by_id(db, operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    return operation


@router.post("/operations", response_model=OperationCreateOut, status_code=201)
async def create_operation(
    payload: OperationCreateIn,
    db: OrmSession = Depends(get_db),
) -> OperationCreateOut:
    """Cria e executa uma operação (idempotente por `operation_id`)."""
    clean: list[dict] = []
    for step in payload.steps:
        action = ops_service.get_action(step.action)
        if action is None:
            clean.append(
                {
                    "target": step.target,
                    "action": step.action,
                    "params": {},
                    "message": f"ação desconhecida: {step.action}",
                }
            )
            continue
        clean.append(
            {
                "target": step.target,
                "action": action.name,
                "params": ops_service._sanitize_params(step.params),  # noqa: SLF001
                "timeout_ms": step.timeout_ms,
            }
        )
    try:
        operation = ops_service.create_or_get_operation(
            db,
            session_id=payload.session_id,
            requested_action=payload.operation or "",
            steps=clean,
            operation_id=payload.operation_id,
        )
        outcome = await ops_service.run_operation(
            db, operation=operation, session_id=payload.session_id, timeout_ms=payload.timeout_ms
        )
    except ops_service.OperationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from app.services import approvals as approval_service

    approvals: list[Any] = []
    if outcome.requires_confirmation:
        for approval in outcome.pending_approvals:
            if isinstance(approval, dict):
                approvals.append(approval)
            else:
                approvals.append(approval_service.to_out(approval))
    return OperationCreateOut(
        accepted=True,
        operation=OperationOut.model_validate(ops_service.to_out(operation)),
        message=outcome.message,
        requires_confirmation=outcome.requires_confirmation,
        needs_input=outcome.needs_input,
        approvals=approvals,
    )


@router.get("/operations", response_model=list[OperationOut])
def list_operations(
    session_id: str | None = None,
    db: OrmSession = Depends(get_db),
) -> list[OperationOut]:
    """Lista operações recentes (opcionalmente por sessão)."""
    return [OperationOut.model_validate(o) for o in ops_service.list_operations(db, session_id=session_id)]


@router.get("/operations/{operation_id}", response_model=OperationOut)
def get_operation(operation_id: str, db: OrmSession = Depends(get_db)) -> OperationOut:
    operation = _require_operation(db, operation_id)
    return OperationOut.model_validate(ops_service.to_out(operation))


@router.post("/operations/{operation_id}/cancel", response_model=OperationOut)
def cancel_operation(operation_id: str, db: OrmSession = Depends(get_db)) -> OperationOut:
    """Cancelamento best-effort: marca passos não executados como cancelados.

    Execução já em andamento não é interrompida no dispositivo — apenas sinalizada
    (a operação não continuará com novos passos).
    """
    operation = _require_operation(db, operation_id)
    cancelled = ops_service.cancel_operation(db, operation_id)
    return OperationOut.model_validate(ops_service.to_out(cancelled))