"""API de identidade remota (Fase 12.2).

Todos os endpoints respondem 503 quando `REMOTE_ENABLED=false` (padrão),
mantendo o comportamento atual inalterado. Nenhuma rota expõe segredos: token
bruto aparece apenas na resposta de pairing (emissão única) e em `/remote/auth`
(entrada legítima do transporte). `GET /remote/status` não expõe configuração
sensível.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.db.session import get_db
from app.remote.auth import RemoteAuthError, authenticate_bearer
from app.remote.credentials import CredentialLimitError, list_credentials, revoke_credential
from app.remote.devices import (
    list_devices,
    load_meta,
    revoke_device,
)
from app.remote.pairing import (
    PairingError,
    PairingInvalid,
    PairingLocked,
    PairingRateLimited,
    create_pairing,
    list_pairings,
    submit_code,
)
from app.remote.sessions import end_session, list_sessions
from app.schemas.remote import (
    AuthIn,
    AuthOut,
    CredentialOut,
    DeviceOut,
    Empty,
    PairingCreateOut,
    PairingOut,
    PairingSubmitIn,
    PairingSubmitOut,
    RemoteStatusOut,
)

router = APIRouter(prefix="/api", tags=["remote"])


def _require_remote() -> None:
    if not settings.remote_enabled:
        raise HTTPException(status_code=503, detail="Remote desabilitado (REMOTE_ENABLED=false)")


def _pairing_error_to_status(exc: PairingError) -> HTTPException:
    if isinstance(exc, PairingRateLimited):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, PairingLocked):
        return HTTPException(status_code=423, detail=str(exc))
    if isinstance(exc, (PairingInvalid,)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/remote/status", response_model=RemoteStatusOut)
def remote_status() -> RemoteStatusOut:
    enabled = bool(settings.remote_enabled)
    configured = bool(enabled and settings.remote_device_id)
    out: dict = {
        "enabled": enabled,
        "configured": configured,
        "detail": (
            "habilitado e configurado"
            if configured
            else ("habilitado sem device_id" if enabled else "desabilitado")
        ),
    }
    if enabled:
        from app.remote.runtime import get_remote_status

        snapshot = get_remote_status()
        if snapshot is not None:
            out.update(snapshot)  # observabilidade sanitizada (sem secrets)
    return RemoteStatusOut(**out)


@router.post("/remote/pairings", response_model=PairingCreateOut, status_code=201)
def remote_create_pairing(db: OrmSession = Depends(get_db)) -> PairingCreateOut:
    _require_remote()
    try:
        request, code = create_pairing(db)
    except PairingError as exc:
        raise _pairing_error_to_status(exc) from exc
    return PairingCreateOut(
        pairing_id=request.id,
        code=code,
        expires_at=request.expires_at,
        ttl_seconds=settings.remote_pairing_ttl_seconds,
    )


@router.get("/remote/pairings", response_model=list[PairingOut])
def remote_list_pairings(
    active_only: bool = False, db: OrmSession = Depends(get_db)
) -> list:
    _require_remote()
    return list_pairings(db, active_only=active_only)


@router.post(
    "/remote/pairings/validate",
    response_model=PairingSubmitOut,
    status_code=201,
)
def remote_submit_pairing(
    body: PairingSubmitIn, db: OrmSession = Depends(get_db)
) -> PairingSubmitOut:
    _require_remote()
    try:
        device, token = submit_code(
            db,
            code=body.code,
            device_name=body.device_name,
            device_type=body.device_type,
            pairing_id=body.pairing_id,
            metadata=body.metadata,
        )
    except PairingError as exc:
        raise _pairing_error_to_status(exc) from exc
    return PairingSubmitOut(device_id=device.id, token=token, device=DeviceOut(**device_out(device)))


@router.get("/remote/devices", response_model=list[DeviceOut])
def remote_list_devices(
    active_only: bool = False, db: OrmSession = Depends(get_db)
) -> list:
    _require_remote()
    return [DeviceOut(**device_out(d)) for d in list_devices(db, active_only=active_only)]


@router.post("/remote/devices/{device_id}/revoke", response_model=DeviceOut)
def remote_revoke_device(device_id: str, db: OrmSession = Depends(get_db)) -> DeviceOut:
    _require_remote()
    try:
        device = revoke_device(db, device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DeviceOut(**device_out(device))


@router.get("/remote/credentials", response_model=list[CredentialOut])
def remote_list_credentials(
    device_id: str | None = None, db: OrmSession = Depends(get_db)
) -> list:
    _require_remote()
    return [
        CredentialOut(
            id=c.id,
            device_id=c.device_id,
            active=c.is_active,
            created_at=c.created_at,
            last_used_at=c.last_used_at,
            expires_at=c.expires_at,
            revoked_at=c.revoked_at,
        )
        for c in list_credentials(db, device_id=device_id)
    ]


@router.post("/remote/credentials/{credential_id}/revoke", response_model=CredentialOut)
def remote_revoke_credential(
    credential_id: str, db: OrmSession = Depends(get_db)
) -> CredentialOut:
    _require_remote()
    try:
        credential = revoke_credential(db, credential_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CredentialOut(
        id=credential.id,
        device_id=credential.device_id,
        active=credential.is_active,
        created_at=credential.created_at,
        last_used_at=credential.last_used_at,
        expires_at=credential.expires_at,
        revoked_at=credential.revoked_at,
    )


@router.get("/remote/sessions", response_model=list)
def remote_list_sessions_endpoint(
    device_id: str | None = None,
    active_only: bool = False,
    db: OrmSession = Depends(get_db),
) -> list:
    _require_remote()
    sessions = list_sessions(db, device_id=device_id, active_only=active_only)
    return [
        {
            "id": s.id,
            "device_id": s.device_id,
            "credential_id": s.credential_id,
            "status": s.status,
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            "ended_at": s.ended_at,
            "revoked_at": s.revoked_at,
        }
        for s in sessions
    ]


@router.post("/remote/sessions/{session_id}/end", response_model=Empty)
def remote_end_session(session_id: str, db: OrmSession = Depends(get_db)) -> Empty:
    _require_remote()
    try:
        end_session(db, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Empty(ok=True)


@router.post("/remote/auth", response_model=AuthOut)
def remote_authenticate(body: AuthIn, db: OrmSession = Depends(get_db)) -> AuthOut:
    """Autentica um Bearer token (integração futura do transporte)."""
    _require_remote()
    try:
        authed = authenticate_bearer(
            db,
            body.token,
            transport_meta=body.transport_meta,
            claimed_device_id=body.claimed_device_id,
        )
    except (RemoteAuthError, CredentialLimitError) as exc:
        raise HTTPException(status_code=401, detail="autenticação negada") from exc
    return AuthOut(
        authenticated=True,
        device=DeviceOut(**device_out(authed.device)),
        session_id=authed.session_id,
        credential_id=authed.credential.id,
    )


def device_out(device) -> dict:
    return {
        "id": device.id,
        "name": device.name,
        "device_type": device.device_type,
        "status": device.status,
        "created_at": device.created_at,
        "last_seen_at": device.last_seen_at,
        "revoked_at": device.revoked_at,
        "metadata": load_meta(device.metadata_json),
    }