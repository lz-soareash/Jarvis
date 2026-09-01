"""Fase 5 + Fase 11.2 (Computer Control): controlador do computador.

Operações seguras e auditáveis do computador. O resto do Core depende desta
interface — ferramentas e API — nunca do SO. A implementação real consulta o
sistema pelo PowerShell nativo (Windows) ou por stdlib (fallback), sem
dependências externas. Os testes injetam um controlador fake; o controlador real
nunca é tocado pela suíte de testes.

Fase 11.2 adiciona: media, volume, URLs, Windows control, verificação pós-execução,
e application aliases (via app.computer.aliases).
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("jarvis.computer")


class SystemControllerError(RuntimeError):
    """Falha ao interagir com o sistema (SO, o PowerShell, permissão, etc.)."""


_PSCMD = "powershell"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_SYSINFO_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$os   = Get-CimInstance Win32_OperatingSystem | Select-Object -First 1
$cpu  = Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
        Where-Object { $_.Size } | Select-Object -First 1
[pscustomobject]@{
  cpu_percent = [math]::Round($cpu.Average, 1)
  memory_total = if ($os) { [long]$os.TotalVisibleMemorySize * 1KB } else { 0 }
  memory_used  = if ($os) { [long]($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) * 1KB } else { 0 }
  disk_total   = if ($disk) { [long]$disk.Size } else { 0 }
  disk_free    = if ($disk) { [long]$disk.FreeSpace } else { 0 }
  boot_time    = if ($os -and $os.LastBootUpTime) { $os.LastBootUpTime.ToString("yyyy-MM-ddTHH:mm:ssZ") } else { $null }
} | ConvertTo-Json -Compress
"""

_PROCESSES_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Get-CimInstance Win32_Process |
  Select-Object ProcessId, Name,
              @{n='mem_bytes';e={[long]$_.WorkingSetSize}},
              @{n='created_at';e={ if ($_.CreationDate) { $_.CreationDate.ToString("yyyy-MM-ddTHH:mm:ssZ") } else { $null } }} |
  ConvertTo-Json -Compress
"""

_KILL_SCRIPT = "Stop-Process -Id {pid} -Force -ErrorAction Stop"

# --- Media: envia teclas de mídia via Windows API ---
_MEDIA_KEY_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait("{keys}")
"""

# --- Volume: envia teclas de volume ---
_VOLUME_KEY_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait("{keys}")
"""

# --- Windows Control ---
_SHUTDOWN_SCRIPT = "shutdown /s /t 0"
_RESTART_SCRIPT = "shutdown /r /t 0"

# --- Allowed commands allowlist (expanda conforme necessário) ---
_ALLOWED_COMMANDS: set[str] = {
    "dir",
    "ls",
    "pwd",
    "echo",
    "date",
    "time",
    "whoami",
    "hostname",
    "ipconfig",
    "ping",
    "systeminfo",
    "tasklist",
    "netstat",
}


def _run_ps(script: str, timeout: int = 30) -> str:
    """Executa um script no PowerShell e devolve a saída em UTF-8."""
    cmd = [_PSCMD, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise SystemControllerError("PowerShell não está disponível neste sistema") from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemControllerError("PowerShell excedeu o tempo limite") from exc
    if proc.returncode != 0:
        raise SystemControllerError(
            f"PowerShell falhou ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return proc.stdout.strip()


def _parse_json(text: str):
    if not text:
        return []
    try:
        return json.loads(text)
    except ValueError:
        return []


def _human(bytes_: int) -> str:
    value = float(bytes_)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


class SystemController:
    """Fachada de operações do computador (stats, processos, apps, mídia, Windows).

    Fase 11.2: expandido com media, volume, URLs, Windows control, verificação
    de processos e application aliases.
    """

    def __init__(self) -> None:
        self._is_windows = sys.platform == "win32"

    @property
    def available(self) -> bool:
        return True

    # ------------------------------------------------------------------ stats
    def system_stats(self) -> dict:
        if not self._is_windows:
            return self._fallback_stats()
        data = _parse_json(_run_ps(_SYSINFO_SCRIPT))
        if isinstance(data, dict):
            return data
        return self._fallback_stats()

    def _fallback_stats(self) -> dict:
        usage = shutil.disk_usage(os.path.abspath(os.sep))
        try:
            import resource  # macOS/Linux

            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        except Exception:  # noqa: BLE001 — fallback simples
            rss = 0
        return {
            "cpu_percent": None,
            "memory_total": rss,
            "memory_used": 0,
            "disk_total": usage.total,
            "disk_free": usage.free,
            "boot_time": None,
        }

    def system_status(self) -> dict:
        """Status completo do computador (stats + apps abertos + hostname)."""
        stats = self.system_stats()
        processes = self.list_processes(limit=200)
        apps_running: list[str] = []
        seen_names: set[str] = set()
        for p in processes:
            name = p.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                apps_running.append(name)
        hostname = ""
        try:
            hostname = os.environ.get("COMPUTERNAME", "")
        except Exception:  # noqa: BLE001
            pass
        return {
            **stats,
            "hostname": hostname,
            "process_count": len(processes),
            "apps_running": apps_running[:50],
        }

    # ------------------------------------------------------------ processes
    def list_processes(self, limit: int = 50) -> list[dict]:
        if not self._is_windows:
            raise SystemControllerError("list_processes disponível apenas no Windows")
        rows = _parse_json(_run_ps(_PROCESSES_SCRIPT))
        if not isinstance(rows, list):
            raise SystemControllerError("Resposta inesperada do PowerShell")
        processes = []
        for row in rows:
            processes.append(
                {
                    "pid": int(row.get("ProcessId") or 0),
                    "name": row.get("Name") or "?",
                    "mem_bytes": int(row.get("mem_bytes") or 0),
                    "created_at": row.get("created_at"),
                }
            )
        processes.sort(key=lambda p: (p["name"].lower(), p["pid"]))
        return processes[: max(1, int(limit))]

    def find_process_by_name(self, name: str) -> list[dict]:
        """Busca processos por nome (para verificação pós-execução)."""
        all_procs = self.list_processes(limit=500)
        name_lower = name.lower().strip()
        return [p for p in all_procs if name_lower in p.get("name", "").lower()]

    # ------------------------------------------------------------------- apps
    def open_app(self, target: str) -> str:
        """Abre um app/arquivo pelo manipulador padrão do Windows (sem shell).

        Fase 11.2: primeiro tenta resolver via aliases conhecidos; se não
        encontrar, usa shutil.which como fallback.
        """
        if not self._is_windows:
            raise SystemControllerError("open_app disponível apenas no Windows")
        target = (target or "").strip()
        if not target:
            raise SystemControllerError("Nenhum app informado")

        # Tenta resolver via aliases conhecidos
        from app.computer.aliases import get_alias_registry

        registry = get_alias_registry()
        entry = registry.resolve(target)
        if entry is not None:
            executable = entry.executable or target
            args = entry.args or []
            resolved = executable
            found = shutil.which(executable)
            if found:
                resolved = found
            elif not os.path.exists(executable):
                raise SystemControllerError(
                    f"Aplicativo conhecido '{entry.display_name}' não encontrado no sistema"
                )
            try:
                import subprocess as _sub

                cmd = [resolved, *args] if args else [resolved]
                _sub.Popen(
                    cmd,
                    creationflags=_CREATE_NO_WINDOW,
                )
            except OSError as exc:
                raise SystemControllerError(
                    f"Não foi possível abrir {entry.display_name}: {exc}"
                ) from exc
            logger.info("App aberto via alias: %s -> %s", target, entry.display_name)
            return f"App iniciado: {entry.display_name}"

        # Fallback: tenta abrir diretamente
        resolved = target
        extension_known = target.lower().endswith((".exe", ".lnk", ".bat", ".cmd", ".scr"))
        if not (extension_known and os.path.exists(target)):
            found = shutil.which(target)
            if found:
                resolved = found
            elif not os.path.exists(target):
                raise SystemControllerError(f"App não encontrado: {target}")

        try:
            os.startfile(resolved)
        except OSError as exc:
            raise SystemControllerError(f"Não foi possível abrir {target}: {exc}") from exc
        logger.info("App aberto pelo agente: %s", target)
        return f"App iniciado: {target}"

    def close_app(self, process_name: str) -> str:
        """Fecha um aplicativo pelo nome do processo."""
        if not self._is_windows:
            raise SystemControllerError("close_app disponível apenas no Windows")
        process_name = (process_name or "").strip()
        if not process_name:
            raise SystemControllerError("Nome do processo é obrigatório")
        script = f'Stop-Process -Name "{process_name}" -Force -ErrorAction SilentlyContinue'
        try:
            _run_ps(script, timeout=10)
        except SystemControllerError:
            pass
        found = self.find_process_by_name(process_name)
        if found:
            return f"Processo '{process_name}' pode ainda estar em execução"
        return f"Aplicativo '{process_name}' encerrado"

    # ------------------------------------------------------------------- URLs
    def open_url(self, url: str) -> str:
        """Abre uma URL no navegador padrão. Valida URL antes de abrir."""
        url = (url or "").strip()
        if not url:
            raise SystemControllerError("URL é obrigatória")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise SystemControllerError(f"URL inválida: {url}")
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001
            raise SystemControllerError(f"Não foi possível abrir a URL: {exc}") from exc
        logger.info("URL aberta pelo agente: %s", url)
        return f"URL aberta: {url}"

    # ------------------------------------------------------------------ files
    def open_file(self, file_path: str) -> str:
        """Abre um arquivo pelo manipulador padrão do SO."""
        file_path = (file_path or "").strip()
        if not file_path:
            raise SystemControllerError("Caminho do arquivo é obrigatório")
        if not os.path.exists(file_path):
            raise SystemControllerError(f"Arquivo não encontrado: {file_path}")
        try:
            os.startfile(file_path)
        except OSError as exc:
            raise SystemControllerError(f"Não foi possível abrir o arquivo: {exc}") from exc
        logger.info("Arquivo aberto pelo agente: %s", file_path)
        return f"Arquivo aberto: {file_path}"

    def open_folder(self, folder_path: str) -> str:
        """Abre uma pasta no Explorador de Arquivos."""
        folder_path = (folder_path or "").strip()
        if not folder_path:
            raise SystemControllerError("Caminho da pasta é obrigatório")
        if not os.path.exists(folder_path):
            raise SystemControllerError(f"Pasta não encontrada: {folder_path}")
        if not os.path.isdir(folder_path):
            raise SystemControllerError(f"O caminho não é uma pasta: {folder_path}")
        try:
            os.startfile(folder_path)
        except OSError as exc:
            raise SystemControllerError(f"Não foi possível abrir a pasta: {exc}") from exc
        logger.info("Pasta aberta pelo agente: %s", folder_path)
        return f"Pasta aberta: {folder_path}"

    # -------------------------------------------------------------- media
    def play_media(self) -> str:
        """Inicia/reproduz mídia (play/pause toggle)."""
        if not self._is_windows:
            raise SystemControllerError("play_media disponível apenas no Windows")
        _run_ps(_MEDIA_KEY_SCRIPT.format(keys="{MEDIA_PLAY_PAUSE}"))
        return "Mídia: play/pause"

    def pause_media(self) -> str:
        """Pausa mídia (play/pause toggle)."""
        if not self._is_windows:
            raise SystemControllerError("pause_media disponível apenas no Windows")
        _run_ps(_MEDIA_KEY_SCRIPT.format(keys="{MEDIA_PLAY_PAUSE}"))
        return "Mídia: pausada"

    def next_track(self) -> str:
        """Próxima faixa de mídia."""
        if not self._is_windows:
            raise SystemControllerError("next_track disponível apenas no Windows")
        _run_ps(_MEDIA_KEY_SCRIPT.format(keys="{MEDIA_NEXT}"))
        return "Mídia: próxima faixa"

    def previous_track(self) -> str:
        """Faixa anterior de mídia."""
        if not self._is_windows:
            raise SystemControllerError("previous_track disponível apenas no Windows")
        _run_ps(_MEDIA_KEY_SCRIPT.format(keys="{MEDIA_PREV}"))
        return "Mídia: faixa anterior"

    # -------------------------------------------------------------- volume
    def volume_up(self) -> str:
        """Aumenta o volume."""
        if not self._is_windows:
            raise SystemControllerError("volume_up disponível apenas no Windows")
        _run_ps(_VOLUME_KEY_SCRIPT.format(keys="{VOLUME_UP}"))
        return "Volume aumentado"

    def volume_down(self) -> str:
        """Diminui o volume."""
        if not self._is_windows:
            raise SystemControllerError("volume_down disponível apenas no Windows")
        _run_ps(_VOLUME_KEY_SCRIPT.format(keys="{VOLUME_DOWN}"))
        return "Volume diminuído"

    def mute(self) -> str:
        """Alterna mudo."""
        if not self._is_windows:
            raise SystemControllerError("mute disponível apenas no Windows")
        _run_ps(_VOLUME_KEY_SCRIPT.format(keys="{VOLUME_MUTE}"))
        return "Volume: mudo alternado"

    # -------------------------------------------------------- windows control
    def lock_computer(self) -> str:
        """Bloqueia a tela do computador."""
        if not self._is_windows:
            raise SystemControllerError("lock_computer disponível apenas no Windows")
        try:
            import ctypes

            ctypes.windll.user32.LockWorkStation()
        except Exception as exc:  # noqa: BLE001
            raise SystemControllerError(f"Não foi possível bloquear: {exc}") from exc
        logger.info("Computador bloqueado pelo agente")
        return "Computador bloqueado"

    def sleep_computer(self) -> str:
        """Suspém o computador."""
        if not self._is_windows:
            raise SystemControllerError("sleep_computer disponível apenas no Windows")
        _run_ps("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        logger.info("Computador suspenso pelo agente")
        return "Computador suspenso"

    def restart_computer(self) -> str:
        """Reinicia o computador (sem timeout — executar imediatamente)."""
        if not self._is_windows:
            raise SystemControllerError("restart_computer disponível apenas no Windows")
        _run_ps(_RESTART_SCRIPT)
        logger.info("Computador reiniciado pelo agente")
        return "Computador reiniciando"

    def shutdown_computer(self) -> str:
        """Desliga o computador (sem timeout — executar imediatamente)."""
        if not self._is_windows:
            raise SystemControllerError("shutdown_computer disponível apenas no Windows")
        _run_ps(_SHUTDOWN_SCRIPT)
        logger.info("Computador desligado pelo agente")
        return "Computador desligando"

    # ---------------------------------------------------------- process control
    def stop_process(self, pid: int) -> str:
        """Para um processo pelo PID (com force)."""
        return self.kill_process(pid)

    def run_allowed_command(self, command: str) -> str:
        """Executa um comando da allowlist de comandos seguros."""
        command = (command or "").strip()
        if not command:
            raise SystemControllerError("Comando é obrigatório")
        base_cmd = command.split()[0].lower() if command.split() else ""
        if base_cmd not in _ALLOWED_COMMANDS:
            raise SystemControllerError(
                f"Comando não autorizado: '{base_cmd}'. "
                f"Permitidos: {', '.join(sorted(_ALLOWED_COMMANDS))}"
            )
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=_CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemControllerError("Comando excedeu o tempo limite") from exc
        output = (proc.stdout or proc.stderr or "").strip()[:2000]
        if proc.returncode != 0:
            raise SystemControllerError(
                f"Comando falhou (exit {proc.returncode}): {output}"
            )
        return output or "Comando executado com sucesso"

    # ---------------------------------------------------- kill process (original)
    def kill_process(self, pid: int) -> str:
        """Encerra um processo específico (nível 3 — exige aprovação explícita)."""
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            raise SystemControllerError("PID inválido") from None
        if pid <= 0 or pid == os.getpid():
            raise SystemControllerError("PID inválido ou refere-se ao próprio JARVIS")

        if self._is_windows:
            _run_ps(_KILL_SCRIPT.format(pid=pid))
        else:
            try:
                os.kill(pid, 9)
            except ProcessLookupError as exc:
                raise SystemControllerError(f"Processo {pid} não encontrado") from exc
        logger.info("Processo encerrado pelo agente: %s", pid)
        return f"Processo {pid} encerrado"

    # ---------------------------------------------------- verification helpers
    def verify_app_running(self, process_names: list[str], timeout: float = 3.0) -> bool:
        """Verifica se algum dos processos está rodando (pós-execução)."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for name in process_names:
                found = self.find_process_by_name(name)
                if found:
                    return True
            time.sleep(0.5)
        return False


_controller: SystemController | None = None


def get_system_controller() -> SystemController:
    global _controller
    if _controller is None:
        _controller = SystemController()
    return _controller