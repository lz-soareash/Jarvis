"""Fase 5 + Fase 11.2 — Computer: controle seguro e local do computador do usuário."""

from .aliases import AppAliasRegistry, get_alias_registry
from .controller import SystemController, SystemControllerError, get_system_controller

__all__ = [
    "SystemController",
    "SystemControllerError",
    "get_system_controller",
    "AppAliasRegistry",
    "get_alias_registry",
]