"""Fase 26 — VEGA Mobile Agent Orchestration: ponte AGENTE ↔ comandos móveis.

Transforma as capabilities móveis da Fase 25 em capacidades reais do AI Core:
um dispatcher (herege) que (1) descobre os dispositivos móveis confiáveis,
(2) resolve o alvo com política segura de seleção, (3) escolhe o transporte
(WAN via CoreLink ou LAN via `mobile_inbox`), (4) espera o status terminal
com idempotência por `command_id` e (5) devolve um resultado estruturado,
sanitizado, para a tool reportar ao modelo.

Segurança:
- O LLM NUNCA escolhe transportes, command ids, device ids ou credenciais —
  recebe apenas o nome amigável opcional (`device`) do visor.
- A seleção de dispositivo é determinística e conservadora: hint por nome >
  dispositivo atrelado à sessão > único dispositivo entregável > ambiguidade
  (pedido de confirmação com os nomes). NUNCA executa em dispositivo desconhecido.
- Não existe elevação de permissão por aqui: as tools mobile são LEVEL_0/LEVEL_1
  (risk LOW) e passam pelo Tool/Permission Engine existente do Core.
- Resultados/erros são sanitizados (nunca tokens, payloads brutos nem secrets).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.models.remote import Device
from app.models.session import ensure_utc, utcnow
from app.remote import mobile_inbox
from app.remote.mobile_capabilities import MobileCommandError, validate_command

# Tempo padrão de espera por um comando de dispositivo (espelha o CoreLink).
_DEFAULT_MOBILE_TIMEOUT_MS = 15_000
# Intervalo de poll do status do comando enquanto espera.
_WAIT_POLL_S = 0.4
# Etiqueta humana por capability (para resumos legíveis ao usuário/modelo).
_HUMAN_LABEL: dict[str, str] = {
    "DEVICE_INFO": "informações do dispositivo",
    "BATTERY_STATUS": "bateria",
    "NETWORK_STATUS": "rede",
    "MEDIA_STATUS": "mídia",
    "OPEN_URL": "abertura de URL",
    "VIBRATE": "vibração",
    "SET_VOLUME": "volume",
    "OPEN_APP": "abertura de app",
    "SET_BRIGHTNESS": "brilho",
}


def human_label(capability: str) -> str:
    """Nome legível de uma capability (Fase 27) — uso em contextos/prompt."""
    return _HUMAN_LABEL.get(capability, capability)

# Dispositivos móveis/tablets reconhecidos pelo Agent Orchestration.
_MOBILE_DEVICE_TYPES = ("mobile", "tablet")


@dataclass(frozen=True, slots=True)
class MobileDeviceView:
    """Visão sanitizada de um dispositivo móvel p/ o orquestrador."""

    id: str
    name: str
    platform: str
    status: str
    online_wan: bool
    online_lan: bool
    session_linked: bool

    @property
    def deliverable(self) -> bool:
        return self.online_wan or self.online_lan


@dataclass(frozen=True, slots=True)
class TargetResolution:
    """Resultado da seleção de dispositivo."""

    device: Device | None = None
    view: MobileDeviceView | None = None
    message: str = ""
    ok: bool = False


@dataclass(frozen=True, slots=True)
class MobileOutcome:
    """Resultado estruturado/sanitizado de um comando de dispositivo."""

    ok: bool
    status: str
    capability: str
    command_id: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    transport: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    summary: str = ""
    latency_ms: int | None = None
    finished_at: str | None = None
    devices: list[dict[str, Any]] | None = None


def _publish(event_type: str, meta: dict[str, Any]) -> None:
    try:
        from app.remote.events import publish_event

        publish_event(event_type, meta)
    except Exception:  # noqa: BLE001 — observação nunca derruba o comando
        pass


def _staleness_seconds() -> int:
    value = getattr(settings, "device_heartbeat_seconds", 30)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 30


def _device_view(device: Device, link, now, session_id: str | None) -> MobileDeviceView:
    online_wan = False
    if link is not None:
        try:
            online_wan = link.is_device_bound(device.id)
        except Exception:  # noqa: BLE001
            online_wan = False
    last_seen = device.last_seen_at
    online_lan = False
    if last_seen is not None:
        try:
            window = _staleness_seconds()
            online_lan = (ensure_utc(now) - ensure_utc(last_seen)).total_seconds() <= window
        except Exception:  # noqa: BLE001
            online_lan = False
    return MobileDeviceView(
        id=device.id,
        name=device.name,
        platform=device.platform or "mobile",
        status=device.status,
        online_wan=online_wan,
        online_lan=online_lan,
        session_linked=bool(session_id and device.jarvis_session_id == session_id),
    )


def mobile_devices(
    db: OrmSession,
    link=None,
    *,
    session_id: str | None = None,
) -> list[tuple[Device, MobileDeviceView]]:
    """Descobre os dispositivos móveis/tablets confiáveis do Device Bridge."""
    from app.remote.devices import list_devices

    now = utcnow()
    out: list[tuple[Device, MobileDeviceView]] = []
    for device in list_devices(db):
        if not device.is_trusted:
            continue
        if device.device_type not in _MOBILE_DEVICE_TYPES:
            continue
        out.append((device, _device_view(device, link, now, session_id)))
    return out


def _friendly_names(items: list[tuple[Device, MobileDeviceView]]) -> str:
    names = [d.name for d, _ in items]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " ou " + names[-1]


def resolve_target(
    db: OrmSession,
    link=None,
    *,
    hint: str | None = None,
    session_id: str | None = None,
) -> TargetResolution:
    """Política de seleção de dispositivo (determinística e conservadora).

    Ordem: hint por nome > fluxo da sessão > único entregável > ambiguidade
    (com os nomes para o usuário escolher). Nunca executa sem alvo inequívoco.
    """
    items = mobile_devices(db, link, session_id=session_id)
    if not items:
        return TargetResolution(message="Nenhum dispositivo móvel pareado (VEGA Mobile).")

    if hint:
        probe = hint.strip().lower()
        exact = [t for t in items if t[0].name.strip().lower() == probe]
        fuzzy = [t for t in items if probe in t[0].name.strip().lower()] if not exact else []
        matches = exact or fuzzy
        if not matches:
            available = _friendly_names(items)
            return TargetResolution(
                message=(
                    f"Não encontrei um celular pareado com o nome '{hint}'. "
                    f"Dispositivos disponíveis: {available}."
                )
            )
        if len(matches) > 1:
            names = _friendly_names(matches)
            return TargetResolution(
                message=(
                    f"Vários dispositivos correspondem a '{hint}'. "
                    f"Qual você quer usar? {names}."
                )
            )
        device, view = matches[0]
        if not view.deliverable:
            return TargetResolution(
                device=device,
                view=view,
                message=(
                    f"O dispositivo '{device.name}' não está conectado agora "
                    "(sem conexão WAN e sem sinal recente via LAN)."
                ),
            )
        return TargetResolution(device=device, view=view, ok=True)

    deliverable = [t for t in items if t[1].deliverable]
    if not deliverable:
        available = _friendly_names(items)
        return TargetResolution(
            message=f"Nenhum dispositivo móvel conectado agora. Pareados: {available}."
        )

    if len(deliverable) == 1:
        device, view = deliverable[0]
        return TargetResolution(device=device, view=view, ok=True)

    session_linked = [t for t in deliverable if t[1].session_linked]
    if len(session_linked) == 1:
        device, view = session_linked[0]
        return TargetResolution(device=device, view=view, ok=True)

    names = _friendly_names(deliverable)
    return TargetResolution(
        message=(
            f"Vários dispositivos móveis estão conectados ({names}). "
            "Qual você quer que eu use?"
        )
    )


def _command_id() -> str:
    return f"mc_{uuid4().hex[:16]}"


async def _wait_terminal(
    *,
    transport: str,
    link=None,
    device_id: str,
    command_id: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Poll até status terminal (com a mesma semântica de TIMEOUT do Core)."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            if transport == "wan":
                summary = link.pending_command_summary(command_id)
            else:
                summary = mobile_inbox.get_mobile_inbox().summary_for(
                    device_id, command_id
                )
            if summary is not None and summary.get("status") in mobile_inbox.TERMINAL_STATUSES:
                return summary
        except Exception:  # noqa: BLE001 — trata como ainda pendente
            pass
        await asyncio.sleep(_WAIT_POLL_S)
    return {"command_id": command_id, "status": "timeout"}


async def dispatch_mobile(
    db: OrmSession,
    *,
    capability: str,
    args: dict[str, Any] | None = None,
    session_id: str | None = None,
    hint: str | None = None,
    timeout_ms: int | None = None,
    link=None,
) -> MobileOutcome:
    """Despacha um comando de dispositivo ao móvel e espera o resultado.

    Retorna `MobileOutcome` sempre (sem exceptions para o chamador): erros de
    validação/seleção/transporte viram `ok=False` com mensagem sanitizada.
    Publica eventos de observabilidade `mobile.command.dispatch` e
    `mobile.command.result` (nunca secrets/payloads brutos).
    """
    started = time.monotonic()

    try:
        spec = validate_command(capability, args)
        capability = spec.name
    except MobileCommandError as exc:
        return MobileOutcome(
            ok=False,
            status="failed",
            capability=capability or "",
            error=str(exc),
            summary=f"Capability de dispositivo inválida: {exc}.",
            latency_ms=0,
        )

    if not spec.executable_on_mobile:
        return MobileOutcome(
            ok=False,
            status="unsupported",
            capability=capability,
            error="capability declarável mas não executável nesta fase",
            summary="Essa capacidade de dispositivo ainda não é executável.",
            latency_ms=0,
        )

    resolution = resolve_target(db, link, hint=hint, session_id=session_id)
    if not resolution.ok or resolution.device is None or resolution.view is None:
        return MobileOutcome(
            ok=False,
            status="failed",
            capability=capability,
            error=resolution.message,
            summary=resolution.message,
            latency_ms=0,
            devices=[
                {"name": d.name, "deliverable": v.deliverable}
                for d, v in mobile_devices(db, link, session_id=session_id)
            ]
            or None,
        )

    device = resolution.device
    view = resolution.view
    wait_ms = max(int(timeout_ms or _DEFAULT_MOBILE_TIMEOUT_MS), 1_000)
    command_id = _command_id()
    clean_args: dict[str, Any] = dict(args or {})

    # Transporte: WAN (atrelado ao CoreLink) preferido; LAN (inbox) em seguida.
    transport: str | None = None
    if view.online_wan and link is not None:
        transport = "wan"
    elif view.online_lan:
        transport = "lan"

    if transport is None:
        message = f"O dispositivo {device.name} não está disponível agora (WAN/LAN)."
        return MobileOutcome(
            ok=False,
            status="failed",
            capability=capability,
            error=message,
            summary=message,
            latency_ms=0,
        )

    _publish(
        "mobile.command.dispatch",
        {
            "command_id": command_id,
            "device_id": device.id,
            "capability": capability,
            "transport": transport,
            "timeout_ms": wait_ms,
        },
    )

    try:
        if transport == "wan":
            summary = await link.dispatch_mobile_command(
                command_id=command_id,
                device_id=device.id,
                capability=capability,
                args=clean_args,
                timeout_ms=wait_ms,
            )
        else:
            summary = mobile_inbox.get_mobile_inbox().enqueue(
                device_id=device.id,
                capability=capability,
                args=clean_args,
                timeout_ms=wait_ms,
                command_id=command_id,
            )
    except Exception as exc:  # noqa: BLE001 — versão sanitizada para o modelo
        error = _brief(exc)
        human = _HUMAN_LABEL.get(capability, capability).lower()
        return MobileOutcome(
            ok=False,
            status="failed",
            capability=capability,
            error=error,
            summary=(
                f"Não consegui despachar {human} para {device.name}: {error}."
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    final = await _wait_terminal(
        transport=transport,
        link=link,
        device_id=device.id,
        command_id=command_id,
        timeout_ms=wait_ms,
    )

    status = final.get("status", "timeout")
    ok = status == "success"
    latency_ms = int((time.monotonic() - started) * 1000)
    result = final.get("result") if ok else None
    error = final.get("error") if not ok else None

    human = _HUMAN_LABEL.get(capability, capability).lower()
    if ok:
        summary = f"{human} de {device.name}: sucesso."
    elif status == "timeout":
        summary = f"{human} de {device.name} não respondeu a tempo (TIMEOUT)."
    elif status == "denied":
        summary = (
            f"{human} de {device.name}: negado pelo dispositivo "
            "(permissão/allowlist local)."
        )
    elif status == "unsupported":
        summary = f"{human} de {device.name}: não suportada pelo dispositivo."
    else:
        summary = f"{human} de {device.name}: falhou ({error or status})."

    _publish(
        "mobile.command.result",
        {
            "command_id": command_id,
            "device_id": device.id,
            "capability": capability,
            "transport": transport,
            "status": status,
            "latency_ms": latency_ms,
        },
    )

    return MobileOutcome(
        ok=ok,
        status=status,
        capability=capability,
        command_id=command_id,
        device_id=device.id,
        device_name=device.name,
        transport=transport,
        result=result,
        error=error,
        summary=summary,
        latency_ms=latency_ms,
        finished_at=utcnow().isoformat(),
    )


def _brief(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text[:240]