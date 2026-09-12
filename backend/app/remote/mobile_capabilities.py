"""VEGA Mobile Control (Fase 25) — modelo de capabilities de dispositivo.

Este módulo é PURAMENTE declarativo e hermétero (sem rede, sem I/O, sem Android):

- `CAPABILITIES` é o registry único das capabilities conhecidas do Core;
- `validate_command()` valida capability + argumentos ANTES de despachar ao
  móvel (fail-fast no Core; o móvel ainda aplica a allowlist LOCAL + permissões
  do SO — validação em DUAS camadas);
- `MOBILE_COMMAND_STATUSES` é o vocabulário de status aceito no resultado.

Segurança:
- capabilities de Fase 25 são todas de baixo risco (informação e ajustes
  locais de mídia/brilho/vibração/pacotes com allowlist explícita);
- NENHUMA capability aqui concede controle profundo de tela (tap/swipe/digição)
  — isso pertence a uma fase futura e NUNCA entra por este registry;
- args são validados por schema rígido (tipos exatos; extra é rejeitado).

O vocabulário espelha o usado pelo cliente Android (executor) para que Core e
móvel falem o mesmo idioma sem drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MobileCommandStatus(str, Enum):
    """Status de um comando de dispositivo (Core ↔ móvel)."""

    PENDING = "pending"       # despachado (in-memory; aguardando resultado)
    RUNNING = "running"       # executando no móvel
    SUCCESS = "success"       # executado com sucesso (result opcional)
    FAILED = "failed"         # execução falhou (error sanitizado)
    DENIED = "denied"         # bloqueado por permissão/allowlist local
    UNSUPPORTED = "unsupported"  # capability declarada mas não implementada no móvel
    TIMEOUT = "timeout"       # sem resposta dentro do timeout_ms
    CANCELLED = "cancelled"   # cancelado (móvel/usuário/sistema)


# Vocabulário canônico (maiúsculas são aceitas como alias no resultado).
MOBILE_COMMAND_STATUSES = frozenset(s.value for s in MobileCommandStatus)


class MobileCommandError(ValueError):
    """Validação falhou (capability desconhecida/args inválidos)."""


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """Spec declarativa de uma capability de dispositivo (Fase 25)."""

    name: str
    description: str
    risk: str  # "low" | "medium" | "high" — Fase 25 só expõe low
    # Schema de args: nome -> ("str" | "int" | "bool" | "float", obrigatório?).
    args: dict[str, tuple[str, bool]] | None = None
    # Precisam de permissão de sistema concedida pelo usuário no Android.
    needs_system_permission: str | None = None
    # `executable_on_mobile`: False => capability DECLARADA mas o executor
    # Android responde UNSUPPORTED (ex.: ACCESSIBILITY_CONTROL nesta fase).
    executable_on_mobile: bool = True

    def validate_args(self, args: dict[str, Any] | None) -> dict[str, Any]:
        """Valida/retorna os args (exigidos + tipos exatos; extra rejeitado)."""
        args = dict(args or {})
        unknown = set(args) - set(self.args or {})
        if unknown:
            raise MobileCommandError(f"argumentos desconhecidos: {sorted(unknown)}")
        for key, (kind, required) in (self.args or {}).items():
            if key not in args:
                if required:
                    raise MobileCommandError(f"argumento obrigatório ausente: {key}")
                continue
            raw = args[key]
            if kind == "int":
                if isinstance(raw, bool) or not isinstance(raw, int):
                    raise MobileCommandError(f"argumento {key}: esperado int")
            elif kind == "float":
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    raise MobileCommandError(f"argumento {key}: esperado float")
                args[key] = float(raw)
            elif kind == "bool":
                if not isinstance(raw, bool):
                    raise MobileCommandError(f"argumento {key}: esperado bool")
            elif kind == "str":
                if not isinstance(raw, str) or not raw:
                    raise MobileCommandError(f"argumento {key}: esperado str não vazio")
                args[key] = raw.strip()
        return args


# Registry canônico de capabilities (Fase 25 — baixo risco; docs em README).
CAPABILITIES: dict[str, CapabilitySpec] = {
    cap.name: cap
    for cap in [
        CapabilitySpec(
            name="DEVICE_INFO",
            description="Informações básicas do dispositivo (modelo, fabricante, versão Android; nunca IDs de HW).",
            risk="low",
        ),
        CapabilitySpec(
            name="BATTERY_STATUS",
            description="Nível de bateria, estado de carga e temperatura aproximada.",
            risk="low",
        ),
        CapabilitySpec(
            name="NETWORK_STATUS",
            description="Tipo de rede (wifi/mobile/offline) e conectividade atual.",
            risk="low",
        ),
        CapabilitySpec(
            name="OPEN_URL",
            description="Abre uma URL num navegador/modo externo do dispositivo.",
            risk="low",
            args={
                "url": ("str", True),
                "external": ("bool", False),
            },
        ),
        CapabilitySpec(
            name="VIBRATE",
            description="Vibra o dispositivo por um intervalo (ms).",
            risk="low",
            args={
                "duration_ms": ("int", True),
            },
        ),
        CapabilitySpec(
            name="SET_VOLUME",
            description="Ajusta o volume do stream de mídia/alarme/toque (0-100).",
            risk="low",
            args={
                "stream": ("str", False),
                "level": ("int", True),
            },
        ),
        CapabilitySpec(
            name="MEDIA_STATUS",
            description="Estado atual de mídia tocando no dispositivo (sanitizado).",
            risk="low",
        ),
        CapabilitySpec(
            name="OPEN_APP",
            description="Abre um pacote da allowlist explícita configurada no Core.",
            risk="low",
            args={
                "package_name": ("str", True),
            },
        ),
        CapabilitySpec(
            name="SET_BRIGHTNESS",
            description="Ajusta o brilho da tela (0-100). Exige WRITE_SETTINGS no móvel.",
            risk="low",
            args={
                "level": ("int", True),
            },
            needs_system_permission="android.permission.WRITE_SETTINGS",
        ),
        CapabilitySpec(
            name="ACCESSIBILITY_CONTROL",
            description="Controle por acessibilidade (Fase futura) — DECLARADA, não executável nesta fase.",
            risk="medium",
            executable_on_mobile=False,
        ),
    ]
}

# Allowlist de pacotes de apps que o móvel pode abrir via OPEN_APP.
DEFAULT_OPEN_APP_ALLOWLIST: frozenset[str] = frozenset(
    {
        "com.android.settings",
        "com.android.chrome",
        "org.mozilla.firefox",
    }
)


def get_capability(name: str) -> CapabilitySpec | None:
    """Devolve a spec canônica da capability (case-insensitive) ou None."""
    if not isinstance(name, str) or not name:
        return None
    return CAPABILITIES.get(name.strip().upper())


def validate_command(capability: str, args: dict[str, Any] | None) -> CapabilitySpec:
    """Valida capability + args no Core (fail-fast). Levanta MobileCommandError.

    A capability DEVE existir no registry; os args são validados pelo schema
    da spec (tipos exatos, obrigatórios, sem extras). O móvel faz a validação
    LOCAL (allowlist/permissões) no momento da execução — duas camadas.
    """
    spec = get_capability(capability)
    if spec is None:
        raise MobileCommandError(
            f"capability desconhecida: {capability!r} (registry VEGA Mobile Control)"
        )
    spec.validate_args(args)
    return spec


def list_capabilities() -> list[dict[str, Any]]:
    """Visão declarativa do registry (sanitizada; sem secrets)."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "risk": spec.risk,
            "executable": spec.executable_on_mobile,
            "args": sorted((k, kind, required) for k, (kind, required) in (spec.args or {}).items()),
            "needs_system_permission": spec.needs_system_permission,
        }
        for spec in CAPABILITIES.values()
    ]


def is_executable(capability: str) -> bool:
    spec = get_capability(capability)
    return bool(spec is not None and spec.executable_on_mobile)