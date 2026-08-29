"""Ferramentas da Fase 5 — Computer: stats, processos e apps do computador.

Leitura (nível 0) é automática; abrir app (nível 1) também; encerrar processo é
nível 3 (potencialmente destrutivo) e exige aprovação explícita do usuário.
"""

import logging

from app.core.enums import PermissionLevel, RiskLevel
from app.computer import controller as computer_module

from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger("jarvis.tools.computer")


class GetSystemStats(Tool):
    """Métricas atuais do computador (CPU, memória, disco e boot)."""

    name = "get_system_stats"
    description = "Retorna uso atual de CPU, memória RAM e disco, além da hora de boot do computador."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            stats = computer_module.get_system_controller().system_stats()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        cpu = stats.get("cpu_percent")
        lines = [
            f"CPU: {cpu}%" if cpu is not None else "CPU: indisponível",
            f"Memória: {_human(stats.get('memory_used') or 0)} em {_human(stats.get('memory_total') or 0)}",
            f"Disco: {_human(stats.get('disk_free') or 0)} livres de {_human(stats.get('disk_total') or 0)}",
        ]
        if stats.get("boot_time"):
            lines.append(f"Boot: {stats['boot_time']}")
        return ToolResult.success("\n".join(lines))


class ListProcesses(Tool):
    """Lista os processos em execução no computador."""

    name = "list_processes"
    description = "Lista os processos em execução no computador (PID, nome e uso de memória)."
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Máximo de processos (padrão 50)."},
        },
    }
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        limit = min(int(arguments.get("limit") or 50), 200)
        try:
            rows = computer_module.get_system_controller().list_processes(limit=limit)
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        if not rows:
            return ToolResult.success("Nenhum processo encontrado.")
        lines = [
            f"{row['pid']:>7}  {row['name']:<28} {_human(row.get('mem_bytes') or 0)}"
            for row in rows
        ]
        header = f"{'PID':>7}  {'NOME':<28} MEMÓRIA"
        return ToolResult.success(f"{header}\n" + "\n".join(lines))


class OpenApp(Tool):
    """Abre um app ou arquivo pelo manipulador padrão do sistema."""

    name = "open_app"
    description = (
        "Abre um aplicativo ou arquivo no computador do usuário (nome ou caminho). "
        "Ex.: open_app com 'notepad', 'C:\\caminho\\app.exe' ou um documento."
    )
    parameters = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Nome ou caminho do app/arquivo a abrir."},
        },
        "required": ["target"],
    }
    permission_level = PermissionLevel.LEVEL_1
    risk = RiskLevel.MEDIUM

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        target = (arguments.get("target") or "").strip()
        if not target:
            return ToolResult.failure("target é obrigatório")
        try:
            message = computer_module.get_system_controller().open_app(target)
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(message)


class KillProcess(Tool):
    """Encerra um processo pelo PID — potencialmente destrutivo."""

    name = "kill_process"
    description = (
        "Encerra (força a saída de) um processo do computador informando o PID. "
        "Use apenas quando o processo travou ou o usuário pediu explicitamente."
    )
    parameters = {
        "type": "object",
        "properties": {"pid": {"type": "integer", "description": "O PID do processo a encerrar."}},
        "required": ["pid"],
    }
    permission_level = PermissionLevel.LEVEL_3
    risk = RiskLevel.HIGH

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        pid = arguments.get("pid")
        try:
            message = computer_module.get_system_controller().kill_process(pid)
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(message)


def _human(bytes_: int) -> str:
    value = float(bytes_)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"