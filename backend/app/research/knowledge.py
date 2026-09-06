"""Knowledge Integration (Fase 14 — sections 20-26).

Candidatos a conhecimento só nascem de evidências reais; confiança/proveniência
são explícitas; dedup e conflito usam o ledger local (`KnowledgeRecord`); a
escrita no Atlas é regulada pela política `AtlasWriteMode` — default
`SUGGEST` (NUNCA escreve automaticamente). Textos são sanitizados e truncados
antes da persistência (nunca secrets no banco/Atlas).
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.enums import AtlasWriteMode, KnowledgeConfidence
from app.models.research import KnowledgeRecord
from app.services.atlas_client import AtlasClient

from .evidence import Evidence, sha256_hex

logger = logging.getLogger("jarvis.research.knowledge")

# Senhas/tokens/segredos mascarados ANTES de ir para o banco/Atlas.
_SECRET_KEY_VALUE = re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|token|authorization|bearer)\b\s*[:=]\s*(?:bearer\s+)?)\S+")
_SECRET_PLAIN = re.compile(r"(?i)\b(aws_access_key_id|aws_secret_access_key)\b\s*[:=]\s*\S+")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}")
_TOKEN_STYLE = re.compile(r"(?i)\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9]{10,})")


@dataclass
class KnowledgeCandidate:
    """Uma afirmação extraída de evidências (com proveniência e confiança)."""

    claim: str
    facts: list[str] = field(default_factory=list)
    confidence: KnowledgeConfidence = KnowledgeConfidence.SOURCE_CONFIRMED
    sources: list[Evidence] = field(default_factory=list)
    project: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        self.claim = sanitize_text(self.claim)

    def content_hash(self) -> str:
        return sha256_hex(f"{self.claim}|{sorted(f for f in self.facts)}")

    def source_hashes(self) -> list[str]:
        return [e.content_hash for e in self.sources]

    def is_valid(self) -> bool:
        valid = bool(self.claim.strip()) and len(self.sources) >= 1 and self.confidence != KnowledgeConfidence.UNVERIFIED
        return valid and all(s.source_url for s in self.sources)

    def to_dict(self) -> dict:
        return {
            "claim": self.claim,
            "facts": [sanitize_text(f) for f in self.facts],
            "confidence": self.confidence.value,
            "sources": [e.as_dict() for e in self.sources],
            "project": self.project,
            "content_hash": self.content_hash(),
        }


def sanitize_text(text: str, *, limit: int = 3000) -> str:
    """Mascara segredos e trunca. É o ÚLTIMO filtro antes da persistência."""
    text = (text or "").strip()
    if not text:
        return ""
    text = _SECRET_KEY_VALUE.sub(r"\1[REDACTED]", text)
    text = _SECRET_PLAIN.sub(r"\1=[REDACTED]", text)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = _TOKEN_STYLE.sub("[REDACTED]", text)
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def classify_confidence(sources: list[Evidence], model_inferred: bool = False) -> KnowledgeConfidence:
    """Confiança a partir da quantidade de fontes independentes (seção 20)."""
    urls = {e.source_url for e in sources if e.source_url}
    if len(urls) >= 2:
        return KnowledgeConfidence.MULTI_SOURCE_CONFIRMED
    if model_inferred:
        return KnowledgeConfidence.MODEL_INFERRED
    if urls:
        return KnowledgeConfidence.SOURCE_CONFIRMED
    return KnowledgeConfidence.UNVERIFIED


# ---------------------------------------------------------------------------
# Ledger local + escrita no Atlas
# ---------------------------------------------------------------------------
class KnowledgeStore:
    """Ledger local (SQLite) de conhecimento validado e política de escrita."""

    def __init__(self, db: OrmSession, *, mode: AtlasWriteMode | None = None) -> None:
        self.db = db
        self.mode = mode or _mode_from_settings()

    # -- escritas -----------------------------------------------------------
    def record_candidates(
        self,
        candidates: list[KnowledgeCandidate],
        *,
        session_id: str | None = None,
        research_id: str | None = None,
    ) -> tuple[list[KnowledgeRecord], list[KnowledgeRecord]]:
        """Registra candidatos no ledger. Retorna (novos, duplicados)."""
        new_records: list[KnowledgeRecord] = []
        dup_records: list[KnowledgeRecord] = []
        for cand in candidates:
            if not cand.is_valid():
                continue
            existing = self._find_duplicate(cand)
            if existing:
                dup_records.append(existing)
                continue
            record = KnowledgeRecord(
                session_id=session_id,
                research_id=research_id,
                claim=cand.claim,
                facts_json=_json_or_none([sanitize_text(f) for f in cand.facts]),
                confidence=cand.confidence.value,
                source_url=(cand.sources[0].source_url if cand.sources else None),
                source_provider=(cand.sources[0].source_provider if cand.sources else None),
                content_hash=cand.content_hash(),
                project=cand.project,
                status="validated" if cand.confidence in (KnowledgeConfidence.SOURCE_CONFIRMED, KnowledgeConfidence.MULTI_SOURCE_CONFIRMED) else "suggested",
            )
            self.db.add(record)
            new_records.append(record)
        self.db.commit()
        for record in new_records:
            self.db.refresh(record)
        return new_records, dup_records

    def find_conflicts(self, candidates: list[KnowledgeCandidate]) -> list[tuple[KnowledgeCandidate, KnowledgeRecord]]:
        """Candidato com CLAIM idêntico mas fonte divergente (registro de conflito)."""
        conflicts: list[tuple[KnowledgeCandidate, KnowledgeRecord]] = []
        for cand in candidates:
            stmt = select(KnowledgeRecord).where(
                KnowledgeRecord.claim == cand.claim,
                KnowledgeRecord.confidence.in_(("source_confirmed", "multi_source_confirmed", "user_confirmed")),
            )
            for record in self.db.scalars(stmt.limit(20)).all():
                if record.content_hash != cand.content_hash() and record.source_url != (cand.sources[0].source_url if cand.sources else None):
                    conflicts.append((cand, record))
        return conflicts

    def promote(
        self,
        candidate_ids: list[str],
        *,
        atlas: AtlasClient | None = None,
        project: str | None = None,
    ) -> list[dict]:
        """Aplica a política de escrita ao Atlas para candidatos escolhidos.

        Modo `suggest`: apenas NÃO persiste nada (retorna status 'suggested').
        Modos `auto`/`user_confirmed`: persiste no Atlas e marca o ledger.
        """
        results: list[dict] = []
        for record_id in candidate_ids:
            record = self.db.get(KnowledgeRecord, record_id)
            if record is None:
                results.append({"id": record_id, "status": "not_found"})
                continue
            if project:
                record.project = project
            record.status = "confirmed"
            if self.mode in (AtlasWriteMode.AUTO, AtlasWriteMode.USER_CONFIRMED) and atlas is not None:
                outcome = _write_to_atlas(atlas, record)
                record.atlas_status = outcome["status"]
                if outcome.get("error"):
                    record.atlas_error = outcome["error"][:2000]
            else:
                record.atlas_status = "suggested"
            self.db.commit()
            self.db.refresh(record)
            results.append(
                {
                    "id": record.id,
                    "claim": record.claim,
                    "status": record.status,
                    "atlas_status": record.atlas_status,
                }
            )
        return results

    def _find_duplicate(self, cand: KnowledgeCandidate) -> KnowledgeRecord | None:
        stmt = select(KnowledgeRecord).where(KnowledgeRecord.content_hash == cand.content_hash())
        return self.db.scalar(stmt.limit(1))

    def list_records(self, *, limit: int = 50) -> list[KnowledgeRecord]:
        stmt = select(KnowledgeRecord).order_by(KnowledgeRecord.created_at.desc()).limit(min(limit, 200))
        return list(self.db.scalars(stmt))


def _write_to_atlas(atlas: AtlasClient, record: KnowledgeRecord) -> dict:
    try:
        response = atlas.store_knowledge(
            claim=record.claim,
            facts=_load_facts(record.facts_json),
            sources=[record.source_url] if record.source_url else [],
            confidence=record.confidence,
            project=record.project,
        )
        return {"status": "synced", "error": None, "response": _clip_for_log(response)}
    except Exception as exc:  # noqa: BLE001 — falha do Atlas NUNCA quebra a pesquisa
        logger.warning("Escrita no Atlas falhou (record=%s): %s", record.id, exc)
        return {"status": "error", "error": str(exc), "response": None}


def _load_facts(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        import json

        data = json.loads(raw)
        return [str(f) for f in data] if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _json_or_none(data) -> str | None:
    import json

    try:
        return json.dumps(data)
    except TypeError:
        return None


def _clip_for_log(value) -> str | None:
    return str(value)[:500] if value is not None else None


def _mode_from_settings() -> AtlasWriteMode:
    from app.core.config import settings

    try:
        return AtlasWriteMode(settings.atlas_write_mode)
    except (AttributeError, ValueError):
        return AtlasWriteMode.SUGGEST