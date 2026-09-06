"""Evidências (Fase 14) — cada afirmação da resposta deve apontar p/ evidências
reais coletadas (seção 16, 21). `Evidence` carrega hash de conteúdo (dedup),
proveniência (URL/final URL/datas) e o trecho citável.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.websearch.base import SearchResult

logger = logging.getLogger("jarvis.research.evidence")


def sha256_hex(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


@dataclass
class Evidence:
    """Uma evidência coletada de uma fonte real (nunca inventada)."""

    source_url: str
    title: str = ""
    excerpt: str = ""  # trecho de texto da página/resultado
    content_hash: str = ""
    source_provider: str = ""  # provider de busca, se originada dele
    retrieve_source: str = "search"  # search | fetch
    retrieved_at: str = ""
    rank: int = 0
    extract: dict = field(default_factory=dict)  # título/headings/texto resumido

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = sha256_hex(self.excerpt or self.source_url)
        if not self.retrieved_at:
            self.retrieved_at = datetime.now(timezone.utc).isoformat()

    def as_dict(self) -> dict:
        return {
            "source_url": self.source_url,
            "title": self.title,
            "excerpt": _clip(self.excerpt, 500),
            "content_hash": self.content_hash,
            "source_provider": self.source_provider,
            "retrieve_source": self.retrieve_source,
            "retrieved_at": self.retrieved_at,
            "rank": self.rank,
            "extract": self.extract and {k: _clip(str(v), 500) for k, v in self.extract.items()},
        }


def build_search_evidence(result: SearchResult) -> Evidence:
    """Evidência derivada de um resultado de busca (snippet como indício)."""
    return Evidence(
        source_url=result.url,
        title=result.title,
        excerpt=result.snippet,
        content_hash=sha256_hex(f"{result.url}|{result.title}|{result.snippet}"),
        source_provider=result.source,
        retrieve_source="search",
        retrieved_at=result.fetched_at,
        rank=result.rank,
        extract={"title": result.title, "snippet": result.snippet},
    )


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text[:limit]