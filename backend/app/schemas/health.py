from datetime import datetime

from app.schemas.base import APIModel


class HealthResponse(APIModel):
    status: str
    app: str
    version: str
    env: str
    database: str
    api_port: int
    ws_port: int
    time: datetime


class AIHealthResponse(APIModel):
    status: str
    provider: str
    model: str | None = None
    detail: str | None = None
    code: str | None = None