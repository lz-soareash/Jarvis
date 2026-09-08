"""Memória estruturada de longo prazo.

Criação/consulta/exclusão de memórias e busca por relevância:
- semântica (cosseno sobre embeddings) quando o provedor tem credencial;
- lexical (ocorrências de termos) como fallback local, sem depender de chave.

Fase 19.5 (VEGA Memory Core):
- `content` é sanitizado ANTES da persistência (write policy — nunca secrets);
- metadata de política: `project`, `confidence`, `source`, `expires_at` (TTL);
- classificação determinística de categoria (`infer_memory_kind`);
- `search_knowledge` injeta conhecimento validado do ledger local (mais abaixo).
"""

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
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

# Classificação determinística de categoria (write policy VEGA).
_PREFERENCE_PAT = re.compile(
    r"\b(prefiro|prefer[ea]s?|gosto de|gostaria de|n[aã]o gosto de|odeio|adoro|"
    r"costumo|curto|favorit[ao])\b",
    re.IGNORECASE,
)
_DECISION_PAT = re.compile(
    r"\b(decidimos?|escolhemos?|optamos|decidimos usar|vamos usar|vamos adotar|"
    r"ficamos? com|adotamos|padronizamos)\b",
    re.IGNORECASE,
)
_PROJECT_PAT = re.compile(r"\b(no|do|para o|este|nosso)\s+projeto\b|\bprojeto\b",
                          re.IGNORECASE)


def _kind_value(kind) -> str:
    return kind.value if isinstance(kind, MemoryKind) else kind


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[^\W\d_]+", text.lower())
    normalized = (unicodedata.normalize("NFD", w).encode("ascii", "ignore").decode() for w in words)
    return [w for w in normalized if len(w) > 1 and w not in _STOPWORDS]


def _is_expired(memory: Memory, now: datetime) -> bool:
    """TTL — memória expirou? `None` = nunca expira (robusto a fuso naive/aware)."""
    exp = memory.expires_at
    if exp is None:
        return False
    if exp.tzinfo is None:
        return exp <= now.replace(tzinfo=None)
    return exp <= now


def infer_memory_kind(content: str) -> MemoryKind:
    """Categoria da política de escrita (determinística, sem LLM).

    Padrão FACT quando nada é detectado — classificação é heurística simples,
    nunca substitui a categoria explícita pedida pelo chamador/usuário.
    """
    text = (content or "").strip().lower()
    if _PREFERENCE_PAT.search(text):
        return MemoryKind.PREFERENCE
    if _DECISION_PAT.search(text):
        return MemoryKind.DECISION
    if _PROJECT_PAT.search(text):
        return MemoryKind.PROJECT
    return MemoryKind.FACT


def sanitize_memory_text(content: str) -> str:
    """Máscara segredos/trunca ANTES de persistir (reuso do sanitizador único).

    Implementação viva em `app.research.knowledge.sanitize_text` (Fase 14) —
    aqui apenas o reuso declarado, sem duplicar regex de secretos.
    """
    from app.research.knowledge import sanitize_text  # import lazy (desacoplamento)

    return sanitize_text(content or "", limit=3000)


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
    kind: MemoryKind | str | None = None,
    session_id: str | None = None,
    provider: AIProvider | None = None,
    project: str | None = None,
    confidence: str = "unverified",
    source: str | None = None,
    expires_at: datetime | None = None,
    sanitize: bool = True,
) -> Memory:
    """Cria uma memória; gera o embedding quando há provedor configurado.

    Fase 19.5 — write policy:
    - `content` é SANITIZADO por padrão (nunca secrets no storage/contexto);
    - `kind=None` classifica deterministicamente (`infer_memory_kind`);
    - metadata opcional: projeto, confiança, origem e expiração (TTL).
    """
    content = _clean_content(content)
    if sanitize:
        content = sanitize_memory_text(content)
    if kind is None:
        kind = infer_memory_kind(content)
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
        project=(project or "").strip()[:200] or None,
        confidence=confidence or "unverified",
        source=(source or "").strip()[:60] or None,
        expires_at=expires_at,
        embedding=embedding,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def _clean_content(content: str) -> str:
    return (content or "").strip() or ""


def list_memories(
    db: OrmSession,
    session_id: str | None = None,
    kind: MemoryKind | str | None = None,
    project: str | None = None,
    include_expired: bool = False,
) -> list[Memory]:
    stmt = select(Memory).order_by(Memory.created_at.desc())
    if session_id is not None:
        stmt = stmt.where(Memory.session_id == session_id)
    if kind is not None:
        stmt = stmt.where(Memory.kind == _kind_value(kind))
    if project:
        stmt = stmt.where(Memory.project == project)
    rows = list(db.scalars(stmt).all())
    now = datetime.now(timezone.utc)
    if not include_expired:
        rows = [m for m in rows if not _is_expired(m, now)]
    return rows


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
    project: str | None = None,
) -> list[tuple[Memory, float]]:
    """Retorna memórias relevantes para a consulta, ordenadas por relevância.

    Fase 19.5 — memórias EXPIRADAS (TTL) são excluídas; `project` filtra a
    associação de projeto quando informado.
    """
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

    now = datetime.now(timezone.utc)
    rows = [m for m in rows if not _is_expired(m, now)]
    if project:
        rows = [m for m in rows if m.project == project]

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


# ---------------------------------------------------------------------------
# Conhecimento validado (ledger local) — retrieval seletivo p/ o prompt
# ---------------------------------------------------------------------------

def search_knowledge(
    db: OrmSession,
    *,
    query: str,
    session_id: str | None = None,
    project: str | None = None,
    limit: int = 3,
) -> list:
    """Conhecimento validado do ledger local relevante à consulta (Fase 19.5).

    Delega pontualmente a `KnowledgeStore.search` (Fase 14) — a lógica de
    scoring lexical/de up vive no ledger; aqui a VEGA apenas expõe o mesmo
    ponto de recuperação usado pelo chat/composer (sem duplicar implementação).
    """
    from app.research.knowledge import KnowledgeStore

    return KnowledgeStore(db).search(
        query=query,
        session_id=session_id,
        project=project,
        limit=limit,
    )