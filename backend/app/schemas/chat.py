from datetime import datetime

from app.schemas.base import APIModel


class SessionCreate(APIModel):
    title: str | None = None


class SessionOut(APIModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class MessageOut(APIModel):
    id: int
    role: str
    content: str
    created_at: datetime


class ChatRequest(APIModel):
    content: str
    stream: bool = False


class ChatResponse(APIModel):
    session_id: str
    message: MessageOut