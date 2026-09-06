"""Proactive Agent (Fase 17) — decisão IGNORE/DEFER/NOTIFY/ASK/EXECUTE.

A decisão é determinística por intenção do evento. A ÚNICA porta de EXECUTE é
`gate_execute`, que exige: tool conhecida no registro, nível efetivo LEVEL_1
(leitura segura), device ativo/confiável e auditoria. LEVEL_2/3 (ou tool
desconhecida/forbidden/device ausente ou revogado) NUNCA executam — viram ASK
(mensagem acionável, sem auto-run) ou IGNORE. Não existe `proactive_execute()`
livre neste pacote (ver Fase 17, seção de segurança).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("jarvis.proactive.decision")

DECISION_IGNORE = "ignore"
DECISION_DEFER = "defer"
DECISION_NOTIFY = "notify"
DECISION_ASK = "ask"
DECISION_EXECUTE = "execute"

# Intenção de notificação por tipo de evento — conservador: somente tipos
# explicitamente listados geram NOTIFY. Tipos de ação em `_ACTION_TYPES`.
_NOTIFY_INTENT = frozenset(
    {
        "system.ready",
        "system.shutdown",
        "system.error",
        "task.completed",
        "task.failed",
        "tool.failed",
        "project.test_failed",
        "project.test_passed",
        "remote.device_connected",
        "remote.device_disconnected",
        "remote.command_completed",
        "remote.command_failed",
        "research.completed",
        "knowledge.recorded",
        "proactive.scheduled",
    }
)

# Tipos que pedem uma ação (EXECUTE se a segurança permitir).
_ACTION_TYPES = frozenset({"action.requested", "proactive.action"})


def intended_decision(event) -> str:
    """Decisão *pretendida* por tipo/prioridade (antes dos gates de entrega)."""
    priority = getattr(event, "priority", "normal") or "normal"
    event_type = getattr(event, "event_type", "")
    if priority == "low":
        # low é informativo; nunca notifica por padrão (anti-spam).
        return DECISION_IGNORE
    if event_type in _NOTIFY_INTENT:
        return DECISION_NOTIFY
    if event_type in _ACTION_TYPES:
        return DECISION_EXECUTE
    return DECISION_IGNORE  # DO NOTHING quando não houver confiança


def _device_trusted(db, device_id: str | None) -> bool:
    """Confiança de origem: device ausente/não-ativo/revogado → não confiável."""
    if not device_id:
        return True  # origem local (JARVIS/workspace/system) é confiável
    from app.core.enums import DeviceStatus
    from app.models import Device

    device = db.get(Device, device_id)
    return device is not None and device.status == DeviceStatus.ACTIVE.value


def gate_execute(db, event) -> str:
    """Porta de EXECUTE: Tool Registry → Permission Engine → Auditoria.

    Retorna DECISION_EXECUTE apenas para tools conhecidas com nível efetivo
    LEVEL_1 (leitura segura). LEVEL_2+ → ASK (confirmação, nunca auto-run);
    tool desconhecida ou origin não confiável → IGNORE. Nunca executa por aqui;
    registro de auditoria é gravado em `log_action` para cada avaliação.
    """
    from app.services.permissions import effective_level

    payload = getattr(event, "payload", None)
    if callable(payload):
        payload = payload()
    payload = payload or {}
    tool_name = payload.get("tool")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return DECISION_IGNORE

    from app.core.enums import PermissionLevel
    from app.tools import registry as tool_registry

    if not _device_trusted(db, getattr(event, "device_id", None)):
        return DECISION_IGNORE

    known = tool_registry.get_tool_registry().get(tool_name)
    if known is None:
        return DECISION_IGNORE  # tool desconhecida → nada

    level = effective_level(db, tool_name)
    if level <= PermissionLevel.LEVEL_1:
        return DECISION_EXECUTE
    return DECISION_ASK  # LEVEL_2/3: exige confirmação/approval, nunca auto