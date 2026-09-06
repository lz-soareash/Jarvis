"""Tool `observe_computer` (Fase 15.5) — PERCEPÇÃO do computador (LEVEL_0).

Operação de leitura registrada no Tool Registry. Durante a execução segue o
pipeline obrigatório Tool Registry → Permission Engine → Audit (via
`perception_service.run_perception`). Devolve ao modelo uma observação
ESTRUTURADA (nunca adivinha o estado): capacidades, janela ativa, processos,
stats — e metadata de screenshot quando solicitado explicitamente.

Segurança: screenshot é OPCIONAL e MANUAL (nunca automático/contínuo); o binário
nunca entra no payload/log/evento — apenas metadata.
"""

import json
import logging

from app.core.config import settings
from app.core.enums import PermissionLevel, RiskLevel
from app.tools.base import Tool, ToolContext, ToolResult

from .base import PerceptionResult

logger = logging.getLogger("jarvis.tools.perception")


class ObserveComputer(Tool):
    """Observa o estado atual do computador (PERCEPÇÃO, leitura segura)."""

    name = "observe_computer"
    description = (
        "Observa o estado atual do computador: capacidade detectada, janela ativa, "
        "uso de CPU/memória/disco e (opcionalmente) processos em execução e metadata "
        "de screenshot. Somente leitura — não executa ações no sistema."
    )
    parameters = {
        "type": "object",
        "properties": {
            "include_processes": {
                "type": "boolean",
                "description": "Incluir a lista de processos em execução (padrão false).",
            },
            "capture_screenshot": {
                "type": "boolean",
                "description": (
                    "Capturar screenshot MANUAL e devolver apenas metadata (timestamp/dimensões/hash). "
                    "Nunca automática. Padrão false."
                ),
            },
            "max_processes": {
                "type": "integer",
                "description": "Teto de processos incluídos (padrão configurado, máx. 200).",
            },
        },
    }
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        if not settings.perception_enabled:
            return ToolResult.failure("Percepção desabilitada (perception_enabled=false).")

        include_processes = bool(arguments.get("include_processes"))
        capture_screenshot = bool(arguments.get("capture_screenshot"))
        if capture_screenshot and not settings.perception_screenshot_enabled:
            return ToolResult.failure("Screenshot desabilitado (perception_screenshot_enabled=false).")
        max_processes = min(int(arguments.get("max_processes") or settings.perception_max_processes), 200)

        from .service import run_perception

        async def noop_sink(_payload):
            pass

        try:
            result: PerceptionResult = await run_perception(
                context.db,
                context.session_id,
                sink=noop_sink,
                include_processes=include_processes,
                max_processes=max_processes,
                capture_screenshot=capture_screenshot,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Falha ao obter percepção: {exc}")

        if not result.available:
            return ToolResult.failure(result.error or "Observação indisponível.")

        obs = result.observation
        lines = [
            f"Capacidades: {obs.capabilities.summary}",
            f"Timestamp: {obs.timestamp}",
        ]
        active = obs.active_window
        if active and (active.title or active.process_name):
            lines.append(
                f"Janela ativa: {active.title or '?'}"
                + (f" ({active.process_name})" if active.process_name else "")
            )
        if obs.system_stats:
            stats = obs.system_stats
            cpu = stats.get("cpu_percent")
            lines.append(
                f"CPU: {cpu}%" if cpu is not None else "CPU: indisponível"
            )
        if obs.processes:
            lines.append(f"Processos: {len(obs.processes)}")
        if obs.screenshot:
            lines.append(
                f"Screenshot: ok ({obs.screenshot.width or '?'}x{obs.screenshot.height or '?'}, "
                f"hash {obs.screenshot.sha256 or '?'})"
            )
        if obs.errors:
            lines.append("Avisos: " + "; ".join(obs.errors))
        return ToolResult.success("\n".join(lines))
