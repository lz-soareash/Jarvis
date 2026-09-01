"""Ferramentas da Fase 5 + Fase 11.2 — Computer Control.

Fase 5: stats, processos e apps do computador.
Fase 11.2: mídia, volume, URLs, Windows control, verificação pós-execução.

Permissões (PermissionLevel):
  LEVEL_0 — leitura/ação reversível (automática)
  LEVEL_1 — ação simples (automática)
  LEVEL_2 — ação potencialmente destrutiva (aprovação)
  LEVEL_3 — ação crítica (bloqueio + aprovação)
"""

import logging

from app.core.enums import PermissionLevel, RiskLevel
from app.computer import controller as computer_module

from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger("jarvis.tools.computer")


# ======================================================================
# LEVEL 0 — leitura / ações reversíveis (automáticas)
# ======================================================================


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


class GetSystemStatus(Tool):
    """Status completo do computador (stats + processos + hostname)."""

    name = "get_system_status"
    description = "Retorna status completo: CPU, memória, disco, hostname e lista dos principais processos em execução."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            status = computer_module.get_system_controller().system_status()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        cpu = status.get("cpu_percent")
        lines = [
            f"CPU: {cpu}%" if cpu is not None else "CPU: indisponível",
            f"Memória: {_human(status.get('memory_used') or 0)} em {_human(status.get('memory_total') or 0)}",
            f"Disco: {_human(status.get('disk_free') or 0)} livres de {_human(status.get('disk_total') or 0)}",
            f"Processos: {status.get('process_count', 0)}",
            f"Hostname: {status.get('hostname', '?')}",
        ]
        apps = status.get("apps_running", [])
        if apps:
            lines.append(f"Apps principais: {', '.join(apps[:15])}")
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


class PlayMedia(Tool):
    """Inicia/reproduz mídia (play/pause)."""

    name = "play_media"
    description = "Inicia ou retoma a reprodução de mídia (música, vídeo)."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().play_media()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class PauseMedia(Tool):
    """Pausa a reprodução de mídia."""

    name = "pause_media"
    description = "Pausa a reprodução de mídia atual."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().pause_media()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class NextTrack(Tool):
    """Próxima faixa de mídia."""

    name = "next_track"
    description = "Avança para a próxima faixa de música ou vídeo."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().next_track()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class PreviousTrack(Tool):
    """Faixa anterior de mídia."""

    name = "previous_track"
    description = "Retorna à faixa anterior de música ou vídeo."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().previous_track()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class VolumeUp(Tool):
    """Aumenta o volume do sistema."""

    name = "volume_up"
    description = "Aumenta o volume do sistema."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().volume_up()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class VolumeDown(Tool):
    """Diminui o volume do sistema."""

    name = "volume_down"
    description = "Diminui o volume do sistema."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().volume_down()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class Mute(Tool):
    """Alterna o estado mudo do sistema."""

    name = "mute"
    description = "Alterna o mudo do sistema (liga/desliga)."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().mute()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


# ======================================================================
# LEVEL 1 — ações simples (automáticas)
# ======================================================================


class OpenApplication(Tool):
    """Abre um aplicativo pelo nome ou alias."""

    name = "open_application"
    description = (
        "Abre um aplicativo no computador do usuário. "
        "Aceita aliases conhecidos (vscode, chrome, spotify, notepad, etc.) ou caminhos. "
        "Exemplos: open_application(target='vscode'), open_application(target='chrome')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Nome, alias ou caminho do aplicativo a abrir.",
            },
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


class OpenUrl(Tool):
    """Abre uma URL no navegador padrão."""

    name = "open_url"
    description = (
        "Abre uma URL no navegador padrão do sistema. "
        "Aceita URLs completas (https://...) ou domínios (youtube.com). "
        "Exemplos: open_url(url='https://youtube.com')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL a abrir no navegador.",
            },
        },
        "required": ["url"],
    }
    permission_level = PermissionLevel.LEVEL_1
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        url = (arguments.get("url") or "").strip()
        if not url:
            return ToolResult.failure("url é obrigatória")
        try:
            result = computer_module.get_system_controller().open_url(url)
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class OpenFile(Tool):
    """Abre um arquivo pelo manipulador padrão do SO."""

    name = "open_file"
    description = (
        "Abre um arquivo no computador do usuário usando o programa padrão associado. "
        "Exemplo: open_file(file_path='C:\\Users\\usuario\\documento.pdf')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Caminho completo do arquivo a abrir.",
            },
        },
        "required": ["file_path"],
    }
    permission_level = PermissionLevel.LEVEL_1
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        file_path = (arguments.get("file_path") or "").strip()
        if not file_path:
            return ToolResult.failure("file_path é obrigatório")
        try:
            result = computer_module.get_system_controller().open_file(file_path)
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class OpenFolder(Tool):
    """Abre uma pasta no Explorador de Arquivos."""

    name = "open_folder"
    description = (
        "Abre uma pasta no Explorador de Arquivos do Windows. "
        "Exemplo: open_folder(folder_path='D:\\Projects\\Atlas')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "folder_path": {
                "type": "string",
                "description": "Caminho da pasta a abrir.",
            },
        },
        "required": ["folder_path"],
    }
    permission_level = PermissionLevel.LEVEL_1
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        folder_path = (arguments.get("folder_path") or "").strip()
        if not folder_path:
            return ToolResult.failure("folder_path é obrigatório")
        try:
            result = computer_module.get_system_controller().open_folder(folder_path)
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


# ======================================================================
# LEVEL 2 — ações potencialmente destrutivas (aprovação)
# ======================================================================


class CloseApplication(Tool):
    """Fecha um aplicativo pelo nome do processo."""

    name = "close_application"
    description = (
        "Fecha/encerra um aplicativo pelo nome do processo. "
        "Exemplo: close_application(process_name='Code')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "process_name": {
                "type": "string",
                "description": "Nome do processo a encerrar (ex.: Code, chrome, Spotify).",
            },
        },
        "required": ["process_name"],
    }
    permission_level = PermissionLevel.LEVEL_2
    risk = RiskLevel.MEDIUM

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        process_name = (arguments.get("process_name") or "").strip()
        if not process_name:
            return ToolResult.failure("process_name é obrigatório")
        try:
            result = computer_module.get_system_controller().close_app(process_name)
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class StopProcess(Tool):
    """Para um processo pelo PID."""

    name = "stop_process"
    description = (
        "Para/encerra um processo específico informando o PID. "
        "Use quando o processo travou ou quando o usuário pediu explicitamente."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pid": {
                "type": "integer",
                "description": "O PID do processo a parar.",
            },
        },
        "required": ["pid"],
    }
    permission_level = PermissionLevel.LEVEL_2
    risk = RiskLevel.MEDIUM

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        pid = arguments.get("pid")
        if pid is None:
            return ToolResult.failure("pid é obrigatório")
        try:
            result = computer_module.get_system_controller().stop_process(pid)
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class RunAllowedCommand(Tool):
    """Executa um comando da allowlist de comandos seguros."""

    name = "run_allowed_command"
    description = (
        "Executa um comando do sistema que está na allowlist de comandos seguros. "
        "Comandos permitidos: dir, ls, pwd, echo, date, time, whoami, hostname, "
        "ipconfig, ping, systeminfo, tasklist, netstat."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Comando a executar (deve estar na allowlist).",
            },
        },
        "required": ["command"],
    }
    permission_level = PermissionLevel.LEVEL_2
    risk = RiskLevel.MEDIUM

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        command = (arguments.get("command") or "").strip()
        if not command:
            return ToolResult.failure("command é obrigatório")
        try:
            result = computer_module.get_system_controller().run_allowed_command(command)
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


# ======================================================================
# LEVEL 3 — ações críticas (bloqueio + aprovação)
# ======================================================================


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


class LockComputer(Tool):
    """Bloqueia a tela do computador."""

    name = "lock_computer"
    description = "Bloqueia a tela do computador (Windows + L)."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_3
    risk = RiskLevel.MEDIUM

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().lock_computer()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class SleepComputer(Tool):
    """Suspém o computador."""

    name = "sleep_computer"
    description = "Coloca o computador em modo de suspensão (sleep)."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_3
    risk = RiskLevel.HIGH

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().sleep_computer()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class RestartComputer(Tool):
    """Reinicia o computador."""

    name = "restart_computer"
    description = "Reinicia o computador imediatamente. Todas as aplicações serão fechadas."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_3
    risk = RiskLevel.HIGH

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().restart_computer()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


class ShutdownComputer(Tool):
    """Desliga o computador."""

    name = "shutdown_computer"
    description = "Desliga o computador imediatamente. Todas as aplicações serão fechadas."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_3
    risk = RiskLevel.HIGH

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        try:
            result = computer_module.get_system_controller().shutdown_computer()
        except computer_module.SystemControllerError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(result)


# ======================================================================
# Helpers
# ======================================================================


def _human(bytes_: int) -> str:
    value = float(bytes_)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"
