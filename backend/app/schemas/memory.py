from datetime import datetime

from app.core.enums import MemoryKind
from app.schemas.base import APIModel


class MemoryCreate(APIModel):
    content: str
    kind: MemoryKind | None = None
    session_id: str | None = None
    project: str | None = None
    confidence: str | None = None
    source: str | None = None
    expires_at: datetime | None = None


class MemoryOut(APIModel):
    id: str
    session_id: str | None = None
    kind: str
    content: str
    project: str | None = None
    confidence: str | None = None
    source: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    score: float | None = None