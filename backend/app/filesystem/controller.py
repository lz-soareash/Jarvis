"""Fase 7 — Filesystem: controlador de arquivos local restrito a uma raiz-sandbox.

O JARVIS NUNCA acessa o sistema de arquivos livremente: todas as operações são
resolvidas contra uma raiz-sandbox (padrão: diretório `~` do usuário) e qualquer
caminho que escape dela é recusado. Leitura (nível 0) e escrita (nível 2) são
expostas como ferramentas; destruição (nível 3) exige aprovação explícita.

Os testes injetam um controlador fake; o controlador real é exercitado com raiz
em um diretório temporário, nunca no sistema de arquivos do usuário.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger("jarvis.filesystem")


class FileSystemError(RuntimeError):
    """Falha ao interagir com o sistema de arquivos (caminho, permissão, etc.)."""


class FileSystemController:
    """Fachada segura de operações de arquivo confinada a `root` (sandbox)."""

    def __init__(self, root: str | Path | None = None) -> None:
        from app.core.config import settings

        base = Path(root or settings.files_root or "~").expanduser()
        self.root = base.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, *parts) -> Path:
        """Resolve um caminho relativo à raiz, recusando qualquer fuga do sandbox.

        Apenas caminhos relativos (a partir da raiz) são aceitos. Caminhos
        absolutos ou com `..` que escapem da raiz são rejeitados com exceção,
        garantindo que a IA nunca alcance arquivos fora do sandbox.
        """
        if not parts or all(p in (None, "") for p in parts):
            raise FileSystemError("Nenhum caminho informado")
        cleaned = []
        for p in parts:
            if p is None:
                continue
            cleaned.append(os.fspath(p).strip().strip('"').strip("'"))
        candidate = Path(*[Path(c) for c in cleaned])
        if candidate.is_absolute():
            raise FileSystemError("Caminho absoluto não permitido — use caminho relativo à raiz do JARVIS")
        resolved = (self.root / candidate).resolve()
        root_str = self.root.as_posix()
        if resolved.as_posix() != root_str and not resolved.as_posix().startswith(root_str.rstrip("/") + "/"):
            raise FileSystemError("Caminho fora da área permitida do JARVIS")
        return resolved

    # ---------------------------------------------------------------- leitura
    def list_dir(self, path: str = "", limit: int = 50) -> dict:
        """Lista arquivos/pastas dentro da raiz (nível 0 - leitura automática)."""
        p = self._resolve(path)
        if not p.exists():
            raise FileSystemError(f"Caminho não encontrado: {path or '.'}")
        if not p.is_dir():
            raise FileSystemError(f"Não é uma pasta: {path or '.'}")
        entries = []
        for child in sorted(p.iterdir(), key=lambda c: (c.name.lower(),)):
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size if child.is_file() else None,
                    "modified": _fmt_time(stat.st_mtime),
                }
            )
        total = len(entries)
        if limit:
            entries = entries[: max(1, int(limit))]
        return {"path": p.as_posix(), "total": total, "entries": entries, "truncated": total > len(entries)}

    def read_file(self, path: str, limit: int = 2000) -> dict:
        """Lê o conteúdo textual de um arquivo (nível 0 - leitura automática)."""
        p = self._resolve(path)
        if not p.exists():
            raise FileSystemError(f"Arquivo não encontrado: {path}")
        if not p.is_file():
            raise FileSystemError(f"Não é um arquivo: {path}")
        try:
            raw = p.read_bytes()
        except OSError as exc:
            raise FileSystemError(f"Não foi possível ler o arquivo: {exc}") from exc
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — conteúdo binário
            text = raw.decode("utf-8", errors="replace")
        total = len(text.splitlines())
        truncated = False
        if limit and total > int(limit):
            text = "\n".join(text.splitlines()[: int(limit)])
            truncated = True
            total = int(limit)
        return {
            "path": p.as_posix(),
            "lines": total if not truncated else f"primeiras {int(limit)} de {len(raw.decode('utf-8', errors='replace').splitlines())}",
            "truncated": truncated,
            "content": text,
        }

    # ---------------------------------------------------------------- escrita
    def write_file(self, path: str, content: str, create_dirs: bool = True) -> str:
        """Escreve (cria/substitui) um arquivo de texto (nível 2 - confirmação)."""
        p = self._resolve(path)
        if create_dirs:
            p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise FileSystemError(f"Não foi possível escrever o arquivo: {exc}") from exc
        size = p.stat().st_size
        logger.info("Arquivo escrito pelo agente: %s (%d bytes)", p.as_posix(), size)
        return f"Arquivo gravado: {path} ({_human(size)})"

    def make_dir(self, path: str) -> str:
        """Cria uma pasta (nível 2 - confirmação)."""
        p = self._resolve(path)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileSystemError(f"Não foi possível criar a pasta: {exc}") from exc
        logger.info("Pasta criada pelo agente: %s", p.as_posix())
        return f"Pasta criada: {path}"

    # --------------------------------------------------------------- destruição
    def delete_path(self, path: str, recursive: bool = False) -> str:
        """Apaga um arquivo ou pasta (nível 3 - exige aprovação explícita)."""
        p = self._resolve(path)
        if not p.exists():
            raise FileSystemError(f"Caminho não encontrado: {path}")
        if p.as_posix() == self.root.as_posix():
            raise FileSystemError("Não é permitido apagar a raiz do JARVIS")
        if p.is_dir():
            if not recursive:
                raise FileSystemError(
                    "A pasta não está vazia — informe recursive=true para apagá-la por completo"
                )
            import shutil

            shutil.rmtree(p)
            logger.warning("Pasta apagada pelo agente (após aprovação): %s", p.as_posix())
        else:
            p.unlink()
            logger.warning("Arquivo apagado pelo agente (após aprovação): %s", p.as_posix())
        return f"Apagado: {path}"


def _human(bytes_: int) -> str:
    value = float(bytes_)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _fmt_time(epoch: float) -> str:
    from datetime import datetime

    try:
        return datetime.fromtimestamp(epoch).strftime("%d/%m/%Y %H:%M")
    except (OSError, ValueError):
        return "—"


_controller: FileSystemController | None = None


def get_filesystem_controller() -> FileSystemController:
    global _controller
    if _controller is None:
        _controller = FileSystemController()
    return _controller
