from datetime import datetime

from app.core.enums import MemoryKind
from app.schemas.base import APIModel


class MemoryCreate(APIModel):
    content: str
    kind: MemoryKind = MemoryKind.FACT
    session_id: str | None = None


class MemoryOut(APIModel):
    id: str
    session_id: str | None = None
    kind: str
    content: str
    created_at: datetime
    updated_at: datetime
    score: float | None = None