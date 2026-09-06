"""Contracts de Percepção (Fase 15.1).

Define a abstração `PerceptionProvider` e o modelo estruturado
`ComputerObservation` que o Agentic Core consome. Princípios:

- Percepção INDEPENDENTE do LLM: o modelo recebe dados estruturados, nunca
  tenta adivinhar o estado do computador.
- Capability discovery REFLEXIVA: as capacidades reais são descobertas do
  ambiente (OS, bibliotecas opcionais) — nunca inventadas.
- Screenshot OPCIONAL e nunca automático: só se explicitamente solicitado, com
  metadata (timestamp/largura/altura/hash); o binário NUNCA entra em logs ou
  eventos.
"""

import abc
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(data: bytes) -> str:
    """Hash de conteúdo usado em metadata de screenshot (nunca o binário)."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ActiveWindow:
    """Janela ativa do usuário (única informação visível de janela)."""

    title: str | None = None
    process_name: str | None = None
    pid: int | None = None


@dataclass(frozen=True)
class ScreenshotMetadata:
    """Metadata de um screenshot — o binário NUNCA é serializado/logado."""

    timestamp: str
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    format: str = "png"
    mode: str = "manual"

    @classmethod
    def from_pixels(cls, data: bytes, width: int | None, height: int | None) -> "ScreenshotMetadata":
        return cls(
            timestamp=_utcnow_iso(),
            width=width,
            height=height,
            sha256=sha256_hex(data) if data else None,
        )

    def to_meta_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "format": self.format,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class CapabilityCheck:
    """Resultado da verificação de uma capacidade específica."""

    available: bool
    detail: str | None = None


@dataclass(frozen=True)
class ComputerCapabilities:
    """Capability discovery reflexiva, baseada no ambiente real.

    Ex.: {screenshot, active_window, processes, accessibility_tree, ocr,
    vision}. `os`, `os_version` e `platform` descrevem o sistema detectado.
    Os booleanos refletem o que o provider realmente consegue — nunca inventado.
    """

    os: str | None = None
    os_version: str | None = None
    platform: str | None = None
    screenshot: bool = False
    active_window: bool = False
    processes: bool = False
    accessibility_tree: bool = False
    ocr: bool = False
    vision: bool = False
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "os": self.os,
            "os_version": self.os_version,
            "platform": self.platform,
            "screenshot": self.screenshot,
            "active_window": self.active_window,
            "processes": self.processes,
            "accessibility_tree": self.accessibility_tree,
            "ocr": self.ocr,
            "vision": self.vision,
            "detail": self.detail,
        }

    @property
    def summary(self) -> str:
        flags = [k for k, v in self.to_dict().items() if v is True]
        return f"{self.os or 'desconhecido'} | " + (", ".join(flags) if flags else "sem capacidades")


@dataclass
class ComputerObservation:
    """Observação estruturada do computador, consumível pelo Agentic Core.

    NUNCA contém binário de screenshot nem segredos. `screenshot` carrega apenas
    a metadata; binário fica sob responsabilidade do chamador (nunca em eventos).
    """

    timestamp: str = field(default_factory=_utcnow_iso)
    capabilities: ComputerCapabilities = field(default_factory=ComputerCapabilities)
    active_window: ActiveWindow | None = None
    processes: list[dict] = field(default_factory=list)
    system_stats: dict = field(default_factory=dict)
    screenshot: ScreenshotMetadata | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "capabilities": self.capabilities.to_dict(),
            "active_window": (
                {
                    "title": self.active_window.title,
                    "process_name": self.active_window.process_name,
                    "pid": self.active_window.pid,
                }
                if self.active_window
                else None
            ),
            "processes": self.processes,
            "system_stats": self.system_stats,
            "screenshot": (
                {
                    "timestamp": self.screenshot.timestamp,
                    "width": self.screenshot.width,
                    "height": self.screenshot.height,
                    "sha256": self.screenshot.sha256,
                    "format": self.screenshot.format,
                    "mode": self.screenshot.mode,
                }
                if self.screenshot
                else None
            ),
            "errors": self.errors,
        }


class PerceptionError(RuntimeError):
    """Falha ao obter percepção do computador (SO, biblioteca, permissão)."""


class PerceptionResult:
    """Resultado de uma observação: dados + flags de disponibilidade.

    `available=True` apenas quando a observação foi obtida de fato.
    """

    __slots__ = ("observation", "available", "error")

    def __init__(self, observation: ComputerObservation, available: bool, error: str | None = None):
        self.observation = observation
        self.available = available
        self.error = error

    @classmethod
    def ok(cls, observation: ComputerObservation) -> "PerceptionResult":
        return cls(observation, True)

    @classmethod
    def unavailable(cls, error: str) -> "PerceptionResult":
        return cls(ComputerObservation(errors=[error]), False, error)


class PerceptionProvider(abc.ABC):
    """Abstração da camada de percepção do computador (L0 segura/determinística).

    Implementações reais leem o SO via APIs nativas (ex.: PowerShell/ctypes no
    Windows), SEM dependência pesada obrigatória. Nunca executam ações
    destrutivas; nunca capturam screenshot automaticamente.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Identificador estável do provider (ex.: 'windows')."""

    @abc.abstractmethod
    def available(self) -> bool:
        """True se o provider consegue operar neste ambiente."""

    @abc.abstractmethod
    def discover_capabilities(self) -> ComputerCapabilities:
        """Descoberta reflexiva das capacidades reais do ambiente."""

    @abc.abstractmethod
    def observe(self, *, include_processes: bool = False, max_processes: int = 30) -> PerceptionResult:
        """Obtém a observação atual do computador (nunca inventada)."""

    def capture_screenshot(self) -> ScreenshotMetadata | None:
        """(Opcional) Captura screenshot manual e devolve apenas metadata.

        Implementação default: não suportado → None. Providers que suportam
        sobrescrevem. O binário é mantido apenas para hashing/entrega discreta.
        """
        return None
