"""Knowledge interface para o Computer Agent (Fase 19) — fundação JARVIS→ATLAS.

Não escreve diretamente no banco do Atlas nem inventa contrato HTTP. Reutiliza o
`KnowledgeStore` da Fase 14 (ledger `KnowledgeRecord`, política `AtlasWriteMode`,
dedup/conflito, sanitização) para:
- reter candidatos de conhecimento de tarefas de Computer Use (solução validada,
  decisão arquitetural, preferência explicitada);
- NUNCA reter senhas/tokens/segredos/conteúdo privado/logs brutos.

A persistência real no Atlas continua sendo via `KnowledgeStore.promote`
(política existente: default `suggest` — não escreve sem confirmação).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.research.knowledge import KnowledgeCandidate, KnowledgeStore, sanitize_text

logger = logging.getLogger("jarvis.computer_agent.atlas")

# Tipos de candidato aceitos (exclui logs/informação temporária).
_ALLOWED_KINDS = {"decision", "rule", "preference", "solution", "documentation", "research"}

# Fragmentos de chave que indicam segredo — nunca persistir.
_SECRET_FRAGMENTS = ("password", "senha", "token", "secret", "api_key", "credential",
                     "authorization", "bearer", "private_key")


@dataclass
class KnowledgeCandidateTx:
    """Candidato de conhecimento transacional (mensagem curta)."""

    claim: str
    kind: str = "decision"
    facts: list[str] = field(default_factory=list)
    project: str = "computer-agent"
    notes: dict[str, Any] = field(default_factory=dict)

    def is_sensitive(self) -> bool:
        hay = (self.claim + " " + " ".join(self.facts) + " " + str(self.notes)).lower()
        return any(frag in hay for frag in _SECRET_FRAGMENTS)


def _kind_allowed(kind: str) -> str:
    kind = (kind or "decision").strip().lower()
    return kind if kind in _ALLOWED_KINDS else "decision"


def store_candidate(
    db: Any,
    *,
    claim: str,
    kind: str = "decision",
    facts: list[str] | None = None,
    project: str = "computer-agent",
    notes: dict[str, Any] | None = None,
) -> dict:
    """Registra um candidato de conhecimento no ledger (respeita sanitização e
    política existente). Retorna status sanitizado — nunca conteúdo sensível.
    """
    tx = KnowledgeCandidateTx(
        claim=sanitize_text((claim or "").strip(), limit=2000),
        kind=_kind_allowed(kind),
        facts=[sanitize_text(f, limit=1000) for f in (facts or [])],
        project=(project or "computer-agent")[:120],
        notes=dict(notes or {}),
    )
    if not tx.claim or tx.is_sensitive():
        return {"status": "skipped", "reason": "empty_or_sensitive"}

    candidate = KnowledgeCandidate(
        claim=tx.claim,
        facts=tx.facts,
        sources=[],
        project=tx.project,
    )
    store = KnowledgeStore(db)
    new_records, dup_records = store.record_candidates([candidate])
    return {
        "status": "recorded" if new_records else "duplicate",
        "records": len(new_records),
        "duplicates": len(dup_records),
    }


def can_write_atlas() -> bool:
    """Atlas está disponível para escrita? (política real decidida no promote)."""
    from app.core.config import settings
    return bool(settings.atlas_enabled) and settings.atlas_write_mode in ("auto", "user_confirmed")