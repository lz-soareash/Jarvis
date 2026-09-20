"""Fase 28 — Remote Operations: API de operações coordenadas entre dispositivos.

Observabilidade e criação programática de operações (o caminho normal é a
ferramenta `remote_operation` do chat). Endpoints NÃO dependem do gate
REMOTE_ENABLED: operações locais (PC) funcionam sem pareamento remoto.

Desde a Fase 28.1, TODOS os endpoints HTTP exigem identidade remota
(`Authorization: Bearer` — `require_remote_device`) e são escopados à sessão
JARVIS-âncora do device autenticado: operações de outro device (ou locais sem
device) retornam 404 sem revelar existência. Nunca expõem secrets; steps/saídas
são sanitizados pelo serviço.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from app.db.session import get_db
from app.remote import operations as ops_service
from app.remote.auth import AuthenticatedDevice
from app.schemas.operations import OperationCreateIn, OperationCreateOut, OperationOut

from .remote_deps import device_anchor_session, require_remote_device

router = APIRouter(prefix="/api/remote", tags=["remote-operations"])


def _require_operation(db: OrmSession, operation_id: str) -> ops_service.RemoteOperation:
    operation = ops_service.load_by_id(db, operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    return operation


def _require_owned(
    db: OrmSession, authed: AuthenticatedDevice, operation: ops_service.RemoteOperation
) -> None:
    """Posse: a operação pertence à sessão-âncora do device autenticado.

    404 (não 403) para não revelar a existência de operações alheias.
    """
    if operation.session_id != device_anchor_session(db, authed):
        raise HTTPException(status_code=404, detail="Operação não encontrada")


@router.post("/operations", response_model=OperationCreateOut, status_code=201)
async def create_operation(
    payload: OperationCreateIn,
    authed: AuthenticatedDevice = Depends(require_remote_device),
    db: OrmSession = Depends(get_db),
) -> OperationCreateOut:
    """Cria e executa uma operação (idempotente por `operation_id`).

    A operação é SEMPRE vinculada à sessão-âncora do device autenticado — um
    `session_id` alegado que não seja a âncora é rejeitado (403).
    """
    session_id = device_anchor_session(db, authed)
    if payload.session_id and payload.session_id != session_id:
        raise HTTPException(status_code=403, detail="sessão não autorizada para este device")

    if payload.operation_id:
        existing = ops_service.load_by_id(db, payload.operation_id)
        if existing is not None:
            _require_owned(db, authed, existing)

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
            session_id=session_id,
            requested_action=payload.operation or "",
            steps=clean,
            operation_id=payload.operation_id,
        )
        outcome = await ops_service.run_operation(
            db, operation=operation, session_id=session_id, timeout_ms=payload.timeout_ms
        )
    except ops_service.OperationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    approvals: list[dict] = [a.model_dump() for a in outcome.pending_approvals] if outcome.requires_confirmation else []
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
    authed: AuthenticatedDevice = Depends(require_remote_device),
    db: OrmSession = Depends(get_db),
) -> list[OperationOut]:
    """Lista operações recentes do PRÓPRIO device (âncora de sessão)."""
    session_id = device_anchor_session(db, authed)
    return [OperationOut.model_validate(o) for o in ops_service.list_operations(db, session_id=session_id)]


@router.get("/operations/{operation_id}", response_model=OperationOut)
def get_operation(
    operation_id: str,
    authed: AuthenticatedDevice = Depends(require_remote_device),
    db: OrmSession = Depends(get_db),
) -> OperationOut:
    operation = _require_operation(db, operation_id)
    _require_owned(db, authed, operation)
    return OperationOut.model_validate(ops_service.to_out(operation))


@router.post("/operations/{operation_id}/cancel", response_model=OperationOut)
def cancel_operation(
    operation_id: str,
    authed: AuthenticatedDevice = Depends(require_remote_device),
    db: OrmSession = Depends(get_db),
) -> OperationOut:
    """Cancelamento best-effort: marca passos não executados como cancelados.

    Execução já em andamento não é interrompida no dispositivo — apenas sinalizada
    (a operação não continuará com novos passos).
    """
    operation = _require_operation(db, operation_id)
    _require_owned(db, authed, operation)
    cancelled = ops_service.cancel_operation(db, operation_id)
    return OperationOut.model_validate(ops_service.to_out(cancelled))