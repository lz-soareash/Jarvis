"""Research Agent (Fase 14 — sections 6-11, 15-19, 27).

Orquestra Busca → Coleta → Extração → Evidência → Síntese (local-first) →
Candidatos a conhecimento. Todas as etapas respeitam limites (consultas,
páginas, bytes, duração), nunca tocam a rede interna (SSRF no Fetcher) e
tratam conteúdo web como dado não-confiável (separação na síntese e detector
de prompt-injection para auditoria). A síntese usa o Router: local → gemini
→ determinístico; a coleta NUNCA usa LLM.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session as OrmSession

from app.ai.providers.base import AIProvider, AIProviderError
from app.core.config import settings
from app.models.research import ResearchRun
from app.services.atlas_client import AtlasClient

from .evidence import Evidence, build_search_evidence
from .extract import extract_html
from .fetcher import (  # noqa: F401 — re-export para conveniência dos callers
    FetchContentTypeError,
    FetchHttpError,
    FetchSsrError,
    FetchTimeoutError,
    FetchTooLargeError,
    WebFetchError,
    WebFetcher,
)
from .knowledge import (
    KnowledgeCandidate,
    KnowledgeStore,
    classify_confidence,
    sanitize_text,
)
from .synthesis import (
    build_synthesis_messages,
    deterministic_synthesis,
    parse_synthesis_answer,
)
from .ssrf import redact_url
from ..websearch.base import SearchProvider, SearchProviderError, SearchQuery

logger = logging.getLogger("jarvis.research.agent")

_INJECTION_SIGNALS = (
    "ignore all previous instructions",
    "ignore previous instructions",
    "ignore your previous",
    "disregard the instructions",
    "you are now",
    "system prompt",
    "reveal your",
    "forget all instructions",
    "break out of",
    "jailbreak",
)


@dataclass
class ResearchOutcome:
    """Resultado de uma pesquisa (usado pela API e pelo Agentic Core)."""

    run_id: str = ""
    objective: str = ""
    status: str = "completed"  # completed | partial | timeout | failed
    answer: str = ""
    facts: list[dict] = field(default_factory=list)
    inferences: list[dict] = field(default_factory=list)
    uncertainties: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)  # Evidence.as_dict()
    sources: list[dict] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    provider: str = ""
    model: str | None = None
    fallback_used: str | None = None
    error: str | None = None
    prompt_injection_suspected: bool = False
    duration_ms: int = 0
    knowledge_records: int = 0
    knowledge_duplicates: int = 0
    knowledge_conflicts: int = 0


class ExtractLike:
    """Duck-type do resultado de extract_html usado pelo agente (paciência para testes)."""


class ResearchAgent:
    """Executa uma pesquisa completa dentro dos limites de segurança."""

    def __init__(
        self,
        *,
        search_provider: SearchProvider,
        fetcher: WebFetcher,
        ai_provider: AIProvider | None = None,
        ai_router=None,
        db: OrmSession | None = None,
        atlas: AtlasClient | None = None,
        session_id: str | None = None,
        event_sink=None,
        max_queries: int | None = None,
        max_pages: int | None = None,
        max_total_bytes: int | None = None,
        max_duration: float | None = None,
        min_evidence: int | None = None,
        retries: int | None = None,
    ) -> None:
        self.search_provider = search_provider
        self.fetcher = fetcher
        self.ai_provider = ai_provider
        self.ai_router = ai_router  # Router com fallback_for(provider)
        self.db = db
        self.atlas = atlas
        self.session_id = session_id
        self.event_sink = event_sink  # async (dict) -> None (progress/research SSE)
        self.max_queries = max_queries or settings.web_research_max_queries
        self.max_pages = max_pages or settings.web_research_max_pages
        self.max_total_bytes = max_total_bytes or settings.web_research_max_total_bytes
        self.max_duration = max_duration if max_duration is not None else settings.web_research_max_duration
        self.min_evidence = min_evidence or settings.web_research_min_evidence
        self.retries = retries if retries is not None else settings.web_search_retries

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------
    async def run(self, objective: str) -> ResearchOutcome:
        start = time.monotonic()
        self._deadline = start + self.max_duration
        outcome = ResearchOutcome(objective=objective, queries=self._derive_queries(objective))
        if self.db is not None:
            run = ResearchRun(objective=objective, session_id=self.session_id, queries_json=_json(outcome.queries))
            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
            outcome.run_id = run.id
        await self._emit({"type": "research.started", "objective": objective, "queries": outcome.queries})
        try:
            collected = await self._collect(outcome)
            evidence = collected["items"]
            outcome.prompt_injection_suspected = bool(collected.get("_injected"))
            outcome.evidence = [e.as_dict() for e in evidence]
            outcome.sources = [e.as_dict() for e in evidence]
            await self._emit({"type": "research.collected", "count": len(evidence), "ttl_bytes": collected.get("ttl_bytes", 0)})
            if not evidence:
                outcome.status = "failed"
                outcome.error = "Nenhuma evidência coletada para a pesquisa."
                await self._emit({"type": "research.failed", "error": outcome.error})
            else:
                await self._synthesize(outcome, evidence, injected=outcome.prompt_injection_suspected)
                self._persist_outcome(outcome)
                self._promote_knowledge(outcome)
        except Exception as exc:  # noqa: BLE001 — agente nunca explode para o caller
            logger.exception("Research falhou inesperadamente: %s", exc)
            outcome.status = "failed"
            outcome.error = f"Falha de pesquisa: {type(exc).__name__}: {exc}"
            await self._emit({"type": "research.failed", "error": outcome.error})
        finally:
            outcome.duration_ms = int((time.monotonic() - start) * 1000)
            self._finalize_run(outcome)
            await self._emit({"type": "research.completed", "run_id": outcome.run_id, "status": outcome.status, "duration_ms": outcome.duration_ms})
        return outcome

    # ------------------------------------------------------------------
    # Fase coleta (NUNCA LLM; SSRF obrigatório)
    # ------------------------------------------------------------------
    async def _collect(self, outcome: ResearchOutcome) -> dict:
        evidence_by_url: dict[str, Evidence] = {}
        ttl_bytes = 0
        pages_fetched = 0
        injected = False
        queries_done = 0

        for terms in outcome.queries:
            if queries_done >= self.max_queries:
                break
            if self._timed_out():
                outcome.status = "partial"
                break
            queries_done += 1
            results = await self._search_with_retry(terms)
            await self._emit({"type": "research.search", "query": terms, "hits": len(results)})

            for result in results:
                if not result.url or result.url in evidence_by_url:
                    continue
                evidence_by_url[result.url] = build_search_evidence(result)

            # Busca as páginas mais bem ranqueadas (orçamento de páginas/bytes).
            ranked = sorted(
                [{"url": e.source_url, "rank": e.rank} for e in evidence_by_url.values() if e.retrieve_source == "search"],
                key=lambda r: r["rank"] or 0,
            )
            for item in ranked:
                if self._timed_out():
                    outcome.status = "partial"
                    break
                if pages_fetched >= self.max_pages:
                    break
                if ttl_bytes >= self.max_total_bytes:
                    break
                ev = evidence_by_url.get(item["url"])
                if ev is None or ev.retrieve_source == "fetch":
                    continue
                if self._enough(evidence_by_url) and pages_fetched > 0:
                    break
                page, size = await self._try_fetch(item["url"])
                if page is None:
                    continue
                ttl_bytes += size
                pages_fetched += 1
                ev.retrieve_source = "fetch"
                ev.extract = {
                    "title": page.title,
                    "snippet": _first_para(page.main_text),
                    "text": page.main_text[:8000],
                }
                if page.text:
                    ev.excerpt = _first_para(page.text) or ev.excerpt
                if _injection_probable(page.text):
                    injected = True
                await self._emit({"type": "research.page", "url": redact_url(item["url"]), "size": size, "fetched": pages_fetched, "pages_budget": self.max_pages})

            if self._enough(evidence_by_url) and pages_fetched > 0:
                break

        return {"items": list(evidence_by_url.values()), "ttl_bytes": ttl_bytes, "_injected": injected}

    async def _search_with_retry(self, terms: str) -> list:
        query = SearchQuery(terms=terms, max_results=settings.web_search_max_results)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                results = await self.search_provider.search(query)
                if attempt > 0:
                    logger.info("Busca recuperada na tentativa %d (query=%r)", attempt + 1, terms)
                return results
            except SearchProviderError as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(0.3 * (attempt + 1))
        logger.warning("Busca falhou em todas as tentativas: %s", last_error)
        return []

    async def _try_fetch(self, url: str) -> tuple[ExtractLike | None, int]:
        try:
            page = await self.fetcher.fetch(url)
        except WebFetchError as exc:  # SSRF/timeout/MIME/limite — nunca rede interna
            logger.info("Fetch rejeitado (%s): %s", getattr(exc, "category", "fetch"), redact_url(url))
            await self._emit({"type": "research.fetch.rejected", "url": redact_url(url), "category": getattr(exc, "category", "fetch"), "error": str(exc)[:300]})
            return None, 0
        extracted = extract_html(page.text, base_url=page.url, max_chars=8000)
        size = len(page.text.encode("utf-8", errors="replace")) or page.text_length
        return extracted, size

    # ------------------------------------------------------------------
    # Síntese local-first (Router: local → gemini → determinístico)
    # ------------------------------------------------------------------
    async def _synthesize(self, outcome: ResearchOutcome, evidence: list[Evidence], *, injected: bool) -> None:
        system, messages = build_synthesis_messages(outcome.objective, evidence)
        procs = self._synthesis_candidates()
        resp = None
        used_label: str | None = None
        fallback_used: str | None = None

        for candidate, label in procs:
            try:
                resp = await candidate.generate(
                    messages,
                    system=system,
                    temperature=0.2,
                    max_tokens=settings.web_research_synthesis_tokens,
                )
                used_label = label
                break
            except Exception as exc:  # noqa: BLE001 — fallback de síntese é defensivo
                logger.warning("Síntese falhou (%s): %s", candidate.name, exc)
                if not isinstance(exc, AIProviderError):
                    break  # erro inesperado não fica em loop mascarando o real

        if resp is None or not resp.text:
            parsed = deterministic_synthesis(outcome.objective, evidence)
            used_label = "deterministic"
            fallback_used = "deterministic"
        else:
            parsed = parse_synthesis_answer(resp.text, evidence)
            if not parsed.get("answer"):
                parsed = deterministic_synthesis(outcome.objective, evidence)
                fallback_used = "deterministic"
            elif used_label and used_label != procs[0][1]:
                fallback_used = used_label

        outcome.answer = parsed.get("answer") or ""
        outcome.facts = parsed.get("facts") or []
        outcome.inferences = parsed.get("inferences") or []
        outcome.uncertainties = parsed.get("uncertainties") or []
        outcome.citations = parsed.get("citations") or []
        outcome.provider = used_label or "deterministic"
        outcome.model = self._model_of(used_label)
        outcome.fallback_used = fallback_used

        if injected:
            outcome.uncertainties.append(
                {"text": "Sinal de prompt-injection detectado no conteúdo web; instruções de página não foram seguidas."}
            )
        await self._emit(
            {
                "type": "research.synthesized",
                "provider": outcome.provider,
                "fallback_used": fallback_used,
                "facts": len(outcome.facts),
                "inferences": len(outcome.inferences),
                "citations": len(outcome.citations),
            }
        )

    def _synthesis_candidates(self) -> list[tuple[AIProvider, str]]:
        """Router local-first: primário → fallback → (determinístico por fim)."""
        procs: list[tuple[AIProvider, str]] = []
        primary = self.ai_provider
        if primary is not None and primary.is_configured:
            procs.append((primary, primary.name))
            fallback = self._fallback_provider(primary)
            if fallback is not None and fallback.is_configured and fallback.name != primary.name:
                procs.append((fallback, fallback.name))
        return procs

    def _model_of(self, label: str | None) -> str | None:
        if not label or label == "deterministic":
            return "fallback-deterministic"
        for provider, name in self._synthesis_candidates():
            if name == label:
                return getattr(provider, "model", None)
        return None

    def _fallback_provider(self, primary: AIProvider | None):
        if self.ai_router is None or not hasattr(self.ai_router, "fallback_for"):
            return None
        try:
            return self.ai_router.fallback_for(primary, task="generate")
        except Exception:  # noqa: BLE001 — fallback nunca deve quebrar síntese
            return None

    # ------------------------------------------------------------------
    # Persistência + conhecimento
    # ------------------------------------------------------------------
    def _persist_outcome(self, outcome: ResearchOutcome) -> None:
        if self.db is None or not outcome.run_id:
            return
        run = self.db.get(ResearchRun, outcome.run_id)
        if run is None:
            return
        run.status = outcome.status
        run.answer = outcome.answer
        run.facts_json = _json(outcome.facts)
        run.inferences_json = _json(outcome.inferences)
        run.uncertainties_json = _json(outcome.uncertainties)
        run.citations_json = _json(outcome.citations)
        run.provider = outcome.provider
        run.model = outcome.model
        run.fallback_used = outcome.fallback_used
        run.error = outcome.error
        run.duration_ms = outcome.duration_ms
        self.db.commit()

    def _finalize_run(self, outcome: ResearchOutcome) -> None:
        if self.db is None or not outcome.run_id:
            return
        run = self.db.get(ResearchRun, outcome.run_id)
        if run is None:
            return
        run.status = outcome.status or run.status
        run.duration_ms = outcome.duration_ms
        if outcome.error and not run.error:
            run.error = outcome.error[:2000]
        self.db.commit()

    def _promote_knowledge(self, outcome: ResearchOutcome) -> None:
        """Registra candidatos a conhecimento no ledger (política do Atlas aplica).

        Evidências da MESMA claim são fundidas: 2+ fontes independentes sobem a
        confiança para MULTI_SOURCE_CONFIRMED. Dedup e conflito nunca
        sobrescrevem registros validados existentes — apenas registram/notificam.
        """
        if self.db is None:
            return
        candidates = _merge_candidates(outcome.evidence)
        if not candidates:
            return
        store = KnowledgeStore(self.db)
        new_records, dups = store.record_candidates(candidates, session_id=self.session_id, research_id=outcome.run_id)
        outcome.knowledge_records = len(new_records)
        outcome.knowledge_duplicates = len(dups)
        conflicts = store.find_conflicts(candidates)
        outcome.knowledge_conflicts = len(conflicts)
        if conflicts:
            logger.info("%d conflitos de conhecimento registrados (sem sobrescrita silenciosa).", len(conflicts))
            for cand, existing in conflicts[:5]:
                logger.debug("Conflito (cand=%s vs ledger=%s): %s", cand.content_hash()[:12], existing.content_hash[:12], cand.claim[:120])

    def _enough(self, evidence_by_url: dict[str, Evidence]) -> bool:
        return len({e.source_url for e in evidence_by_url.values() if e.source_url}) >= self.min_evidence

    def _timed_out(self) -> bool:
        return time.monotonic() >= getattr(self, "_deadline", float("inf"))

    def _derive_queries(self, objective: str) -> list[str]:
        seed = sanitize_text(objective)[:300]
        queries = []
        for t in (seed,) + self._extra_terms(objective):
            t = t.strip()
            if t and t not in queries:
                queries.append(t)
        return queries[: self.max_queries]

    @staticmethod
    def _extra_terms(objective: str) -> tuple:
        # Heurística leve sem LLM: expande markdown/números quando curto.
        return ()

    # ------------------------------------------------------------------
    async def _emit(self, payload: dict) -> None:
        if self.event_sink is not None:
            try:
                await self.event_sink(payload)
            except Exception:  # noqa: BLE001 — eventos nunca quebram coleta
                pass


def _merge_candidates(evidence: list[dict]) -> list[KnowledgeCandidate]:
    """Funde evidências pela mesma claim (2+ fontes ⇒ MULTI_SOURCE_CONFIRMED)."""
    groups: dict[str, list[Evidence]] = {}
    for raw in evidence:
        ev = _evidence_from_dict(raw)
        claim = sanitize_text(f"{ev.title} — {ev.excerpt}")
        if len(claim) < 40:
            continue
        groups.setdefault(claim, []).append(ev)
    candidates: list[KnowledgeCandidate] = []
    for claim, evs in groups.items():
        candidates.append(
            KnowledgeCandidate(
                claim=claim[:2000],
                facts=[sanitize_text(ev.excerpt)[:500] for ev in evs],
                confidence=classify_confidence(evs),
                sources=evs,
                project="atlas",
            )
        )
    return candidates


def _evidence_from_dict(d: dict) -> Evidence:
    return Evidence(
        source_url=d.get("source_url") or "",
        title=d.get("title") or "",
        excerpt=d.get("excerpt") or "",
        content_hash=d.get("content_hash") or "",
        source_provider=d.get("source_provider") or "",
        retrieve_source=d.get("retrieve_source") or "fetch",
        retrieved_at=d.get("retrieved_at") or "",
        rank=int(d.get("rank") or 0),
        extract=d.get("extract") or {},
    )


def _first_para(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    return (blocks[0] if blocks else text)[:500]


def _injection_probable(text: str) -> bool:
    lower = (text or "").lower()
    return any(s in lower for s in _INJECTION_SIGNALS)


def _json(data) -> str | None:
    import json

    try:
        return json.dumps(data, ensure_ascii=True)
    except (TypeError, ValueError):
        return None