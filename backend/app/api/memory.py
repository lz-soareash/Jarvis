from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider
from app.core.enums import MemoryKind
from app.db.session import get_db
from app.schemas.memory import MemoryCreate, MemoryOut
from app.services import chat as chat_service
from app.services import memory as memory_service

from .deps import get_ai_provider

router = APIRouter(prefix="/api", tags=["memory"])


@router.post("/memories", response_model=MemoryOut, status_code=201)
async def create_memory(
    payload: MemoryCreate,
    db: OrmSession = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> MemoryOut:
    if payload.session_id and chat_service.get_session(db, payload.session_id) is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    memory = await memory_service.create_memory(
        db,
        content=payload.content,
        kind=payload.kind,
        session_id=payload.session_id,
        provider=provider,
    )
    return MemoryOut.model_validate(memory)


@router.get("/memories", response_model=list[MemoryOut])
async def list_memories(
    session_id: str | None = None,
    kind: MemoryKind | None = None,
    query: str | None = None,
    limit: int = 20,
    db: OrmSession = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> list[MemoryOut]:
    """Lista memórias ou busca por relevância quando `query` é informada."""
    if query:
        results = await memory_service.search_memories(
            db,
            query=query,
            session_id=session_id,
            include_global=session_id is None,
            limit=min(limit, 50),
            provider=provider,
        )
        return [
            MemoryOut.model_validate(m).model_copy(update={"score": round(score, 4)})
            for m, score in results
        ]
    memories = memory_service.list_memories(db, session_id=session_id, kind=kind)
    return [MemoryOut.model_validate(m) for m in memories[: min(limit, 50)]]


@router.get("/memories/{memory_id}", response_model=MemoryOut)
def get_memory(memory_id: str, db: OrmSession = Depends(get_db)) -> MemoryOut:
    memory = memory_service.get_memory(db, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memória não encontrada")
    return MemoryOut.model_validate(memory)


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: str, db: OrmSession = Depends(get_db)) -> None:
    memory = memory_service.get_memory(db, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memória não encontrada")
    memory_service.delete_memory(db, memory)