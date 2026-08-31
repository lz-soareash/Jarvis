from app.core.enums import DeviceType
from app.schemas.base import APIModel


class DeviceInfoResponse(APIModel):
    device: DeviceType
    touch: bool
