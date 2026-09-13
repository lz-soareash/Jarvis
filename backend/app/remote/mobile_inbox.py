"""Fase 26 — VEGA Mobile Agent Orchestration: fila LAN de comandos de dispositivo.

O CoreLink (WAN) já entrega MOBILE_COMMAND via relé WebSocket. Para o padrão
LAN (Device Bridge HTTP — ex.: `adb reverse` ou Wi-Fi), o Core precisa de um
canal de entrega HTTP: o móvel faz POLL dos comandos pendentes por dispositivo
e POST do resultado. Este módulo é a fila em memória (bounded, idempotente por
`command_id`/device) desse canal.

Segurança:
- NUNCA persiste, nunca expõe secrets/payloads sensíveis aos logs/SSE.
- Transporta apenas comandos JÁ validados pelo registry (`mobile_capabilities`).
- O dispositivo que executa é o do POLL autenticado (o endpoint casa o Bearer
  com o `device_id` da URL) — o Core nunca confia no `device_id` alegado.
- `submit_result` só aceita resultados de comandos conhecidos deste device
  (pendente OU já resolvido — idempotente); resultados estranhos são contados
  e descartados, exatamente como o CoreLink faz no WAN.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.models.session import utcnow
from app.remote.mobile_capabilities import MOBILE_COMMAND_STATUSES

# Tetos anti-vazamento de memória (espelham o CoreLink / fase 25).
_MAX_PENDING_PER_DEVICE = 20
_MAX_RESULTS_PER_DEVICE = 50

# Status que encerram a espera do agente (terminal).
TERMINAL_STATUSES = frozenset(
    {"success", "failed", "denied", "unsupported", "timeout", "cancelled"}
)


class MobileInboxError(ValueError):
    """Falha de validação do canal LAN de comandos."""


@dataclass(frozen=True, slots=True)
class InboxCounts:
    pending: int = 0
    results: int = 0
    dropped: int = 0


class MobileInbox:
    """Fila em memória de comandos de dispositivo por device (canal LAN).

    Idempotência por `command_id`:
    - re-enqueue de um comando pendente devolve o estado PENDING atual;
    - re-enqueue de um comando já resolvido devolve o estado final (não executa
      de novo — o móvel, se re-consultar pendentes, não o verá mais);
    - re-submit de resultado devolve o estado final já armazenado.
    """

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, dict[str, Any]]] = {}
        self._results: dict[str, dict[str, dict[str, Any]]] = {}
        self._dropped: int = 0

    # -- helpers internos ----------------------------------------------------

    @staticmethod
    def _now() -> str:
        return utcnow().isoformat()

    @staticmethod
    def _monotonic() -> float:
        return time.monotonic()

    def _summary(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "command_id": entry["command_id"],
            "device_id": entry["device_id"],
            "capability": entry["capability"],
            "status": entry["status"],
            "timeout_ms": entry.get("timeout_ms"),
            "arg_usages": entry.get("arg_usages"),
            "enqueued_at": entry.get("enqueued_at"),
            "started_at": entry.get("started_at"),
            "finished_at": entry.get("finished_at"),
            "result": entry.get("result"),
            "error": entry.get("error"),
        }

    # -- API pública ---------------------------------------------------------

    def enqueue(
        self,
        *,
        device_id: str,
        capability: str,
        args: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """Enfileira um comando validado p/ o móvel (idempotente por command_id).

        Devolve sempre um resumo sanitizado (status pending/final). O `command_id`
        é gerado aqui quando ausente (retry de transporte nunca duplica).
        """
        if not isinstance(device_id, str) or not device_id:
            raise MobileInboxError("device_id inválido")
        if not isinstance(capability, str) or not capability:
            raise MobileInboxError("capability inválida")

        cid = command_id or f"mc_{uuid4().hex[:16]}"
        bucket = self._pending.setdefault(device_id, {})
        existing = bucket.get(cid)
        if existing is not None:
            return self._summary(existing)
        resolved = self._results.get(device_id, {}).get(cid)
        if resolved is not None:
            return self._summary(resolved)  # já resolvido — não re-executa

        if len(bucket) >= _MAX_PENDING_PER_DEVICE:
            oldest = min(bucket, key=lambda k: bucket[k]["enqueued_monotonic"])
            bucket.pop(oldest, None)
            self._dropped += 1

        entry: dict[str, Any] = {
            "command_id": cid,
            "device_id": device_id,
            "capability": capability,
            "args": dict(args or {}),
            "timeout_ms": timeout_ms,
            "status": "pending",
            "enqueued_at": self._now(),
            "enqueued_monotonic": self._monotonic(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        bucket[cid] = entry
        return self._summary(entry)

    def pending_for(self, device_id: str) -> list[dict[str, Any]]:
        """Snapshot dos comandos ainda pendentes deste device (p/ o POLL).

        NÃO remove da fila: o móvel pode não ter executado ainda — o próprio
        executor Android deduplica por `command_id`. Um comando só sai da fila
        quando o resultado chega via `submit_result`.
        """
        bucket = self._pending.get(device_id)
        if not bucket:
            return []
        return [
            {
                "command_id": e["command_id"],
                "capability": e["capability"],
                "args": e["args"],
                "timeout_ms": e.get("timeout_ms"),
            }
            for e in bucket.values()
        ]

    def submit_result(
        self,
        *,
        device_id: str,
        command_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Registra o resultado de um comando pollado por este device.

        Idempotente: re-submit devolve o estado final já armazenado. Comandos
        desconhecidos (nunca expedidos para este device) são contados como
        `dropped` e devolvem `{"dropped": True, "ok": False}`.
        """
        status = (status or "").lower()
        if status not in MOBILE_COMMAND_STATUSES:
            raise MobileInboxError(f"status inválido: {status!r}")

        resolved = self._results.get(device_id, {}).get(command_id)
        if resolved is not None:
            return self._summary(resolved)

        bucket = self._pending.get(device_id)
        entry = (bucket or {}).get(command_id)
        if entry is None:
            self._dropped += 1
            return {"dropped": True, "ok": False, "command_id": command_id}

        entry["status"] = status
        entry["started_at"] = entry.get("started_at") or self._now()
        entry["finished_at"] = self._now()
        entry["result"] = result if status == "success" else None
        entry["error"] = error
        bucket.pop(command_id, None)

        hist = self._results.setdefault(device_id, {})
        hist[command_id] = entry
        if len(hist) > _MAX_RESULTS_PER_DEVICE:
            oldest = min(hist, key=lambda k: hist[k]["finished_at"])
            hist.pop(oldest, None)
        return self._summary(entry)

    def summary_for(self, device_id: str, command_id: str) -> dict[str, Any] | None:
        """Resumo atual do comando (pendente primeiro; senão o final)."""
        entry = self._pending.get(device_id, {}).get(command_id)
        if entry is not None:
            return self._summary(entry)
        entry = self._results.get(device_id, {}).get(command_id)
        if entry is not None:
            return self._summary(entry)
        return None

    def counts(self) -> InboxCounts:
        return InboxCounts(
            pending=sum(len(b) for b in self._pending.values()),
            results=sum(len(b) for b in self._results.values()),
            dropped=self._dropped,
        )

    def reset(self) -> None:
        """Hermeticidade entre testes."""
        self._pending.clear()
        self._results.clear()
        self._dropped = 0


_inbox: MobileInbox | None = None


def get_mobile_inbox() -> MobileInbox:
    global _inbox
    if _inbox is None:
        _inbox = MobileInbox()
    return _inbox


def reset_mobile_inbox() -> None:
    get_mobile_inbox().reset()