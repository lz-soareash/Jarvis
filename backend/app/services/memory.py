"""Memória estruturada de longo prazo.

Criação/consulta/exclusão de memórias e busca por relevância:
- semântica (cosseno sobre embeddings) quando o provedor tem credencial;
- lexical (ocorrências de termos) como fallback local, sem depender de chave.
"""

import json
import logging
import re
import unicodedata
from math import sqrt

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider
from app.core.enums import MemoryKind
from app.models import Memory

logger = logging.getLogger("jarvis.memory")

_STOPWORDS = {
    "a",
    "ao",
    "aos",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "me",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "para",
    "por",
    "que",
    "se",
    "ser",
    "sobre",
    "um",
    "uma",
}


def _kind_value(kind) -> str:
    return kind.value if isinstance(kind, MemoryKind) else kind


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[^\W\d_]+", text.lower())
    normalized = (unicodedata.normalize("NFD", w).encode("ascii", "ignore").decode() for w in words)
    return [w for w in normalized if len(w) > 1 and w not in _STOPWORDS]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sqrt(sum(x * x for x in a))
    nb = sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_memory(
    db: OrmSession,
    *,
    content: str,
    kind: MemoryKind = MemoryKind.FACT,
    session_id: str | None = None,
    provider: AIProvider | None = None,
) -> Memory:
    """Cria uma memória; gera o embedding quando há provedor configurado."""
    embedding = None
    if provider is not None and provider.is_configured:
        try:
            embedding = json.dumps(await provider.embed(content))
        except Exception as exc:  # noqa: BLE001 — embedding é best-effort
            logger.warning("Embedding indisponível ao criar memória: %s", exc)
    memory = Memory(
        session_id=session_id,
        kind=_kind_value(kind),
        content=content,
        embedding=embedding,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def list_memories(
    db: OrmSession,
    session_id: str | None = None,
    kind: MemoryKind | str | None = None,
) -> list[Memory]:
    stmt = select(Memory).order_by(Memory.created_at.desc())
    if session_id is not None:
        stmt = stmt.where(Memory.session_id == session_id)
    if kind is not None:
        stmt = stmt.where(Memory.kind == _kind_value(kind))
    return list(db.scalars(stmt).all())


def get_memory(db: OrmSession, memory_id: str) -> Memory | None:
    return db.get(Memory, memory_id)


def delete_memory(db: OrmSession, memory: Memory) -> None:
    db.delete(memory)
    db.commit()


# ---------------------------------------------------------------------------
# Busca por relevância
# ---------------------------------------------------------------------------

async def search_memories(
    db: OrmSession,
    *,
    query: str,
    session_id: str | None = None,
    include_global: bool = True,
    limit: int = 10,
    provider: AIProvider | None = None,
) -> list[tuple[Memory, float]]:
    """Retorna memórias relevantes para a consulta, ordenadas por relevância."""
    clauses: list = []
    if include_global:
        clauses.append(Memory.session_id.is_(None))
    if session_id is not None:
        clauses.append(Memory.session_id == session_id)
    stmt = select(Memory)
    if clauses:
        stmt = stmt.where(or_(*clauses))
    stmt = stmt.order_by(Memory.created_at.desc())
    rows = list(db.scalars(stmt).all())

    embedded = [m for m in rows if m.embedding]
    qvec: list[float] | None = None
    if provider is not None and provider.is_configured and embedded:
        try:
            qvec = await provider.embed(query)
        except Exception as exc:  # noqa: BLE001 — cai no fallback lexical
            logger.warning("Embedding indisponível na busca: %s", exc)

    if qvec is not None:
        scored = []
        for memory in embedded:
            try:
                vector = json.loads(memory.embedding or "[]")
            except ValueError:
                continue
            similarity = _cosine(qvec, vector)
            if similarity > 0.0:
                scored.append((memory, similarity))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    tokens = _tokens(query)
    if not tokens:
        return []
    scored = []
    for memory in rows:
        content_tokens = _tokens(memory.content)
        count = sum(1 for tok in tokens if tok in content_tokens)
        if count:
            scored.append((memory, float(count)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]