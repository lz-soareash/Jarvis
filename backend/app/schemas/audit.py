from datetime import datetime

from app.schemas.base import APIModel


class AuditEntryOut(APIModel):
    """Uma entrada do trilho de auditoria (execuções e decisões de permissão)."""

    id: int
    created_at: datetime
    session_id: str | None = None
    action: str
    tool: str | None = None
    allowed: bool | None = None
    detail: str | None = None