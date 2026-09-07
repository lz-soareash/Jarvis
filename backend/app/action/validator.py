"""Computer Action Layer (Fase 18) — validador de ações.

Validador puro (sem side effects). Verifica: existência do tipo, params,
limits configuráveis, normalização. Chamado ANTES de qualquer efeito colateral.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings

from .errors import ActionValidationError
from .models import ActionRequest, ActionType
from .registry import get_action_registry


def validate_action_request(request: ActionRequest) -> ActionRequest:
    """Valida um ActionRequest de forma determinística.

    - Verifica se o tipo de ação é conhecido no registry.
    - Valida e normaliza params via registry validator.
    - Aplica limits de config (max_type_text_chars, max_scroll_amount, etc).
    - Retorna request com params normalizados ou levanta ActionValidationError.
    - Nenhum efeito colateral: não movimenta mouse, não digita, não consulta adapter.
    """
    registry = get_action_registry()

    if not registry.is_known(request.action_type):
        raise ActionValidationError(
            f"Tipo de ação desconhecido: {request.action_type.value}",
            detail=f"Tipos disponíveis: {', '.join(t.value for t in registry.all_types())}",
        )

    try:
        normalized = registry.validate_params(request.action_type, request.params)
    except ValueError as e:
        raise ActionValidationError(str(e), detail="Parâmetros inválidos para o tipo de ação")

    if request.action_type == ActionType.TYPE_TEXT and "text" in normalized:
        max_chars = settings.computer_action_max_type_text_chars
        if len(normalized["text"]) > max_chars:
            raise ActionValidationError(
                f"text excede limite máximo de {max_chars} caracteres",
                detail=f"recebido: {len(normalized['text'])} caracteres",
            )

    if request.action_type == ActionType.SCROLL and "amount" in normalized:
        max_scroll = settings.computer_action_max_scroll_amount
        if abs(normalized["amount"]) > max_scroll:
            raise ActionValidationError(
                f"amount={normalized['amount']} excede limite ±{max_scroll}",
            )

    if request.action_type == ActionType.HOTKEY and "keys" in normalized:
        max_keys = settings.computer_action_max_hotkey_keys
        if len(normalized["keys"]) > max_keys:
            raise ActionValidationError(
                f"hotkey máximo {max_keys} teclas, recebeu {len(normalized['keys'])}",
            )

    # Coordenadas: bounds checks (se adapter disponível usa screen size real)
    for coord_key in ("x", "y"):
        if coord_key in normalized:
            val = normalized[coord_key]
            if val < 0:
                raise ActionValidationError(f"{coord_key}={val} não pode ser negativo")

    import copy
    return ActionRequest(
        action_type=request.action_type,
        params=copy.deepcopy(normalized),
        action_id=request.action_id,
        requested_by=request.requested_by,
        session_id=request.session_id,
        device_id=request.device_id,
        dry_run=request.dry_run,
        timeout_seconds=request.timeout_seconds,
        metadata=copy.deepcopy(request.metadata),
        created_at=request.created_at,
    )
