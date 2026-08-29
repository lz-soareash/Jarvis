"""Fase 5 — Computer: controlador do computador com operações nominais e seguras.

O resto do Core depende desta interface — ferramentas e API — nunca do SO.
A implementação real consulta o sistema pelo PowerShell nativo (Windows) ou por
stdlib (fallback), sem dependências externas. Os testes injetam um controlador
fake; o controlador real nunca é tocado pela suíte de testes.
"""

import json
import logging
import os
import shutil
import subprocess
import sys

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


class SystemController:
    """Fachada de operações do computador (stats, processos, apps)."""

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

    # ------------------------------------------------------------------- apps
    def open_app(self, target: str) -> str:
        """Abre um app/arquivo pelo manipulador padrão do Windows (sem shell)."""
        if not self._is_windows:
            raise SystemControllerError("open_app disponível apenas no Windows")
        target = (target or "").strip()
        if not target:
            raise SystemControllerError("Nenhum app informado")

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


_controller: SystemController | None = None


def get_system_controller() -> SystemController:
    global _controller
    if _controller is None:
        _controller = SystemController()
    return _controller