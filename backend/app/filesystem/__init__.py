"""Fase 7 — Filesystem: controle seguro e local dos arquivos do usuário."""

from .controller import FileSystemController, FileSystemError, get_filesystem_controller

__all__ = ["FileSystemController", "FileSystemError", "get_filesystem_controller"]
