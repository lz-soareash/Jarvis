"""Perception Layer (Fase 15).

Fundação da camada de PERCEBER do futuro Computer Use (PERCEBER→INTERPRETAR→
PLANEJAR→AGIR→OBSERVAR→VERIFICAR→RECUPERAR). Esta fase entrega apenas a base
segura e determinística de percepção do computador — SEM agente autônomo de
controle de UI, sem OCR, sem loops de Computer Use.

A percepção é independente do LLM: o modelo recebe `ComputerObservation`
estruturado, nunca "adivinha" o estado da máquina. Tudo passa por
PerceptionProvider → capability discovery reflexiva → observation store bounded
→ integração no Agentic Core (Tool Registry → Permission → Audit).
"""

from app.perception.base import (
    ActiveWindow,
    CapabilityCheck,
    ComputerCapabilities,
    ComputerObservation,
    PerceptionError,
    PerceptionProvider,
    PerceptionResult,
    ScreenshotMetadata,
)

__all__ = [
    "ActiveWindow",
    "CapabilityCheck",
    "ComputerCapabilities",
    "ComputerObservation",
    "PerceptionError",
    "PerceptionProvider",
    "PerceptionResult",
    "ScreenshotMetadata",
]
