from fastapi import APIRouter, Header

from app.schemas.device import DeviceInfoResponse
from app.services.device import detect_device, detect_touch

router = APIRouter(prefix="/api", tags=["device"])


@router.get("/device/info", response_model=DeviceInfoResponse)
def device_info(
    user_agent: str | None = Header(default=None, alias="User-Agent"),
) -> DeviceInfoResponse:
    """Tipo de dispositivo detectado pelo User-Agent (somente leitura).

    Permite à UI/ao cliente ajustar a experiência por classe de device
    (desktop, mobile, tablet/web) sem fingerprint ou persistência.
    """
    return DeviceInfoResponse(
        device=detect_device(user_agent),
        touch=detect_touch(user_agent),
    )
