"""Registry de aliases de aplicativos — Fase 11.2 (Computer Control).

Mapeia nomes/aliases conhecidos para identificadores resolúveis pelo SO.
O LLM pode chamar qualquer alias; o registry resolve para o executável correto.
Aliases são case-insensitive e normalizados (removidos acentos, espaços extras).

Adicione novos aliases aqui — não no controller.
"""

import os
import shutil
from dataclasses import dataclass


@dataclass
class AppEntry:
    """Definição de um aplicativo conhecido."""

    aliases: set[str]  # nomes que o LLM ou usuário pode usar
    display_name: str  # nome para exibição
    executable: str | None = None  # caminho ou nome do executável (None = resolver via which)
    args: list[str] | None = None  # argumentos extras (ex.: code --new-window)
    process_names: list[str] | None = None  # nomes de processo para verificação pós-execução


# ---------------------------------------------------------------------------
# Aplicativos conhecidos — adicione novos aqui
# ---------------------------------------------------------------------------

_KNOWN_APPS: list[AppEntry] = [
    AppEntry(
        aliases={"vscode", "vs code", "visual studio code", "code"},
        display_name="Visual Studio Code",
        executable="code",
        args=["--new-window"],
        process_names=["Code", "Code.exe"],
    ),
    AppEntry(
        aliases={"chrome", "google chrome", "google-chrome"},
        display_name="Google Chrome",
        executable="chrome",
        process_names=["chrome", "chrome.exe"],
    ),
    AppEntry(
        aliases={"firefox", "mozilla firefox", "mozilla-firefox"},
        display_name="Mozilla Firefox",
        executable="firefox",
        process_names=["firefox", "firefox.exe"],
    ),
    AppEntry(
        aliases={"spotify"},
        display_name="Spotify",
        executable="spotify",
        process_names=["Spotify", "Spotify.exe"],
    ),
    AppEntry(
        aliases={"notepad", "bloco de notas"},
        display_name="Bloco de Notas",
        executable="notepad",
        process_names=["notepad", "notepad.exe"],
    ),
    AppEntry(
        aliases={"explorer", "file explorer", "explorer de arquivos", "meu computador", "meus arquivos"},
        display_name="Explorador de Arquivos",
        executable="explorer",
        process_names=["explorer", "explorer.exe"],
    ),
    AppEntry(
        aliases={"terminal", "cmd", "prompt de comando"},
        display_name="Prompt de Comando",
        executable="cmd",
        process_names=["cmd", "cmd.exe"],
    ),
    AppEntry(
        aliases={"powershell", "ps", "terminal avançado"},
        display_name="PowerShell",
        executable="powershell",
        process_names=["powershell", "pwsh", "pwsh.exe", "powershell.exe"],
    ),
    AppEntry(
        aliases={"edge", "microsoft edge", "ms edge"},
        display_name="Microsoft Edge",
        executable="msedge",
        process_names=["msedge", "msedge.exe"],
    ),
    AppEntry(
        aliases={"calc", "calculadora", "calculator"},
        display_name="Calculadora",
        executable="calc",
        process_names=["Calculator", "Calculator.exe"],
    ),
    AppEntry(
        aliases={"paint", "mspaint"},
        display_name="Paint",
        executable="mspaint",
        process_names=["mspaint", "mspaint.exe"],
    ),
    AppEntry(
        aliases={"wordpad"},
        display_name="WordPad",
        executable="write",
        process_names=["write", "write.exe", "wordpad.exe"],
    ),
    AppEntry(
        aliases={"snipping tool", "recorte", "captura de tela"},
        display_name="Ferramenta de Captura",
        executable="SnippingTool",
        process_names=["SnippingTool", "SnippingTool.exe"],
    ),
    AppEntry(
        aliases={"settings", "configurações", "configuracoes", "painel de controle", "control panel"},
        display_name="Configurações",
        executable="ms-settings:",
        process_names=[],
    ),
    AppEntry(
        aliases={"task manager", "gerenciador de tarefas"},
        display_name="Gerenciador de Tarefas",
        executable="taskmgr",
        process_names=["Taskmgr", "Taskmgr.exe"],
    ),
]


class AppAliasRegistry:
    """Resolve aliases de aplicativos para execução segura."""

    def __init__(self) -> None:
        self._apps: dict[str, AppEntry] = {}
        for entry in _KNOWN_APPS:
            for alias in entry.aliases:
                self._apps[alias.lower().strip()] = entry

    def resolve(self, target: str) -> AppEntry | None:
        """Resolve um alias/apelido para o AppEntry correspondente.

        Returns None se não encontrar.
        """
        normalized = target.lower().strip()
        return self._apps.get(normalized)

    def find_by_process(self, process_name: str) -> AppEntry | None:
        """Busca um AppEntry pelo nome do processo (para verificação pós-execução)."""
        process_lower = process_name.lower().strip()
        for entry in _KNOWN_APPS:
            if entry.process_names:
                for pn in entry.process_names:
                    if pn.lower() == process_lower:
                        return entry
        return None

    def is_known(self, target: str) -> bool:
        """Verifica se o target é um alias conhecido."""
        return self.resolve(target) is not None

    def list_apps(self) -> list[dict]:
        """Lista todos os aplicativos conhecidos."""
        seen: set[str] = set()
        result: list[dict] = []
        for entry in _KNOWN_APPS:
            key = entry.display_name
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "display_name": entry.display_name,
                    "aliases": sorted(entry.aliases),
                    "executable": entry.executable,
                }
            )
        return result


_registry: AppAliasRegistry | None = None


def get_alias_registry() -> AppAliasRegistry:
    global _registry
    if _registry is None:
        _registry = AppAliasRegistry()
    return _registry
