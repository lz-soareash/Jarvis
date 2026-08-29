from datetime import datetime

from app.schemas.base import APIModel


class ApprovalOut(APIModel):
    """Um pedido de aprovação exposto à API e ao frontend."""

    id: str
    session_id: str
    tool_name: str
    arguments: dict = {}
    risk: str = "low"
    permission_level: int = 0
    status: str = "pending"
    created_at: datetime
    expires_at: datetime


class ApprovalRespond(APIModel):
    """Decisão do usuário sobre um pedido de aprovação."""

    approved: bool