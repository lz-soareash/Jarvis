"""Computer Verifier (Fase 19) — a ação ALCANÇOU o efeito esperado?

Verificação ESTRUTURAL (nunca screenshots/OCR indiscriminados). Compara o
critério `expected` da intenção com a observação obtida após a ação:
- `window_title_contains`: substring no título da janela ativa (case-insens.)
- `active_window_contains`: substring no título+processo da janela ativa
- `process`: processo esperado rodando (lista de processos)
- `action`: ok se a ação foi executada (fallback sem observação estrutural)

`structural=False` sinaliza verificação fraca (sem critério estrutural).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import IntendedAction


@dataclass
class VerificationResult:
    ok: bool
    reason: str
    detail: str = ""
    structural: bool = True

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "detail": self.detail[:400],
                "structural": self.structural}


class ComputerVerifier:
    def verify(self, observation: dict | None, intended: IntendedAction) -> VerificationResult:
        expected = intended.expected or {}
        obs = observation or {}

        title = _title_of(obs)
        process = _process_of(obs)
        procs = obs.get("processes") or []

        if "window_title_contains" in expected:
            needle = str(expected["window_title_contains"] or "").strip()
            got = needle.lower() in title.lower() if title and needle else False
            return VerificationResult(got, "window_title_contains" if got else "window_title_missing",
                                      detail=f"esperado {needle!r} ativo")
        if "active_window_contains" in expected:
            needle = str(expected["active_window_contains"] or "").strip()
            hay = f"{title} {process}"
            got = bool(needle and needle.lower() in hay.lower())
            return VerificationResult(got, "active_window_contains" if got else "active_window_missing",
                                      detail=f"esperado {needle!r} na janela ativa")
        if "process" in expected:
            needle = str(expected["process"] or "").strip().lower()
            if not needle:
                return VerificationResult(True, "process_skip", "sem filtro de processo", structural=True)
            got = any((p.get("name") or "").lower().find(needle) >= 0 for p in procs)
            return VerificationResult(got, "process_running" if got else "process_not_running",
                                      detail=f"processo {needle!r}")
        # Sem critério estrutural → aceita com ressalva (structural=False).
        return VerificationResult(True, "action_ok",
                                 detail="verificação estrutural indisponível para esta ação",
                                 structural=False)


def _title_of(obs: dict) -> str:
    active = obs.get("active_window") or {}
    return str(active.get("title") or "")


def _process_of(obs: dict) -> str:
    active = obs.get("active_window") or {}
    return str(active.get("process_name") or "")