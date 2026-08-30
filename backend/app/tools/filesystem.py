"""Ferramentas da Fase 7 — Filesystem: ler, escrever e gerenciar arquivos locais.

Confinadas a uma raiz-sandbox (`~` por padrão). Leitura (nível 0) e escrita
(nível 2) geram confirmação; destruição (nível 3) exige aprovação explícita.
"""

import logging

from app.core.enums import PermissionLevel, RiskLevel
from app.filesystem import controller as fs_module

from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger("jarvis.tools.filesystem")


class ListDir(Tool):
    """Lista arquivos e pastas dentro da área permitida do JARVIS."""

    name = "list_dir"
    description = (
        "Lista os arquivos e pastas de uma pasta relativa à área do JARVIS "
        "(a pasta inicial do usuário). Ex.: list_dir com '.' lista a raiz."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Pasta relativa a listar (padrão: raiz '.').",
            },
            "limit": {"type": "integer", "description": "Máximo de entradas (padrão 50)."},
        },
    }
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        path = arguments.get("path") or ""
        limit = min(int(arguments.get("limit") or 50), 200)
        try:
            data = fs_module.get_filesystem_controller().list_dir(path=path, limit=limit)
        except fs_module.FileSystemError as exc:
            return ToolResult.failure(str(exc))
        if not data["entries"]:
            return ToolResult.success(f"Pasta vazia: {path or '.'}")
        rows = [f"[PASTA] {e['name']}/" if e["type"] == "dir" else f"{e['name']}  ({_human(e['size'] or 0)}, {e['modified']})" for e in data["entries"]]
        header = f"Conteúdo de {data['path']} ({data['total']} itens):"
        body = "\n".join(rows)
        if data["truncated"]:
            body += f"\n(mostrando {len(data['entries'])} de {data['total']})"
        return ToolResult.success(f"{header}\n{body}")


class ReadFile(Tool):
    """Lê o conteúdo textual de um arquivo (nível 0 - leitura automática)."""

    name = "read_file"
    description = (
        "Lê o conteúdo de um arquivo de texto dentro da área do JARVIS "
        "(caminho relativo). Ex.: read_file com 'notas.txt'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Caminho relativo do arquivo."},
            "limit": {"type": "integer", "description": "Máximo de linhas (padrão 2000)."},
        },
        "required": ["path"],
    }
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        path = (arguments.get("path") or "").strip()
        if not path:
            return ToolResult.failure("path é obrigatório")
        limit = min(int(arguments.get("limit") or 2000), 10000)
        try:
            data = fs_module.get_filesystem_controller().read_file(path=path, limit=limit)
        except fs_module.FileSystemError as exc:
            return ToolResult.failure(str(exc))
        suffix = "\n(conteúdo truncado)" if data["truncated"] else ""
        return ToolResult.success(f"--- {data['path']} ---\n{data['content']}{suffix}")


class WriteFile(Tool):
    """Escreve (cria ou substitui) um arquivo de texto — nível 2 (confirmação)."""

    name = "write_file"
    description = (
        "Escreve ou substitui o conteúdo de um arquivo de texto dentro da área "
        "do JARVIS. Ex.: salvar uma lista, criar um .txt/.md/.json de notas."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Caminho relativo do arquivo."},
            "content": {"type": "string", "description": "Conteúdo a gravar no arquivo."},
        },
        "required": ["path", "content"],
    }
    permission_level = PermissionLevel.LEVEL_2
    risk = RiskLevel.MEDIUM

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        path = (arguments.get("path") or "").strip()
        content = arguments.get("content") or ""
        if not path:
            return ToolResult.failure("path é obrigatório")
        if content is None:
            content = ""
        try:
            message = fs_module.get_filesystem_controller().write_file(path=path, content=content)
        except fs_module.FileSystemError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(message)


class MakeDir(Tool):
    """Cria uma pasta — nível 2 (confirmação)."""

    name = "make_dir"
    description = (
        "Cria uma pasta dentro da área do JARVIS (caminho relativo), "
        "incluindo subpastas intermediárias se necessário."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Caminho relativo da pasta a criar."},
        },
        "required": ["path"],
    }
    permission_level = PermissionLevel.LEVEL_2
    risk = RiskLevel.MEDIUM

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        path = (arguments.get("path") or "").strip()
        if not path:
            return ToolResult.failure("path é obrigatório")
        try:
            message = fs_module.get_filesystem_controller().make_dir(path=path)
        except fs_module.FileSystemError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(message)


class DeletePath(Tool):
    """Apaga um arquivo ou pasta — potencialmente destrutivo (nível 3)."""

    name = "delete_path"
    description = (
        "Apaga permanentemente um arquivo ou pasta dentro da área do JARVIS "
        "(caminho relativo). Para pastas com conteúdo, use recursive=true. "
        "NUNCA apagar a raiz. Use apenas quando o usuário pedir explicitamente."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Caminho relativo a apagar."},
            "recursive": {
                "type": "boolean",
                "description": "Apagar pasta com todo o conteúdo (padrão: false).",
            },
        },
        "required": ["path"],
    }
    permission_level = PermissionLevel.LEVEL_3
    risk = RiskLevel.HIGH

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        path = (arguments.get("path") or "").strip()
        if not path:
            return ToolResult.failure("path é obrigatório")
        recursive = bool(arguments.get("recursive"))
        try:
            message = fs_module.get_filesystem_controller().delete_path(path=path, recursive=recursive)
        except fs_module.FileSystemError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(message)


def _human(bytes_: int) -> str:
    value = float(bytes_)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"
