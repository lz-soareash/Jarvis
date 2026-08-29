"""Fase 5 — Computer: controle seguro e local do computador do usuário."""

from .controller import SystemController, SystemControllerError, get_system_controller

__all__ = ["SystemController", "SystemControllerError", "get_system_controller"]