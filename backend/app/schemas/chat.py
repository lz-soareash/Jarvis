from datetime import datetime

import json
from pydantic import model_validator

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
    metadata: dict | None = None

    @model_validator(mode="before")
    @classmethod
    def _lift_metadata_json(cls, data):
        """Converte `metadata_json` (coluna no ORM) em `metadata` (dict).

        O model guarda o JSON em `metadata_json`; este schema expõe `metadata`.
        Aceita objeto ORM ou dict e devolve sempre um dict pronto para validar.
        """
        if isinstance(data, dict):
            if "metadata" not in data and data.get("metadata_json") is not None:
                raw = data["metadata_json"]
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except ValueError:
                        raw = None
                data["metadata"] = raw
            return data

        meta = getattr(data, "metadata_json", None)
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = None
        return {
            "id": data.id,
            "role": data.role,
            "content": data.content,
            "created_at": data.created_at,
            "metadata": meta,
        }


class ChatRequest(APIModel):
    content: str
    stream: bool = False
    tools: bool = False


class ChatResponse(APIModel):
    session_id: str
    message: MessageOut