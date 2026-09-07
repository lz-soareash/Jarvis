"""Computer Action Layer (Fase 18) — camada de serviço (orquestração).

O serviço é o ponto de entrada de alto nível para quem NÃO usa o Tool
Registry (ex.: remote executor). NÃO contém lógica de negócio de agente —
apenas orquestra o ActionExecutor de forma segura pelo mesmo pipeline.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .executor import ActionExecutor, ActionExecutorOptions, get_executor
from .models import ActionRequest, ActionResult
from .observer import action_stats


def _blocking_execute(delegate: ActionExecutor, request: ActionRequest, opts: ActionExecutorOptions) -> ActionResult:
    """Executa o executor de forma bloqueante sem corromper o event loop corrente.

    Não usa asyncio.run (que em 3.13 redefine o loop corrente para None e
    quebra chamadas subsequentes a asyncio.get_event_loop() no mesmo thread).
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(delegate.execute(request, opts))
    finally:
        loop.close()


def execute_action(
    *,
    action_type: str,
    params: dict[str, Any] | None = None,
    requested_by: str = "remote",
    session_id: str | None = None,
    device_id: str | None = None,
    dry_run: bool = False,
    confirmed: bool = False,
    db=None,
    executor: ActionExecutor | None = None,
) -> ActionResult:
    """Executa uma ação via o executor (única porta), de forma bloqueante.

    `confirmed=True` sinaliza que o usuário confirmou ações que requerem
    confirmação (política de segurança OU nível de permissão efetiva).
    Uso em contexto sem event loop corrente (ex.: executor remoto).
    """
    request = ActionRequest(
        action_type=action_type,
        params=params or {},
        requested_by=requested_by,
        session_id=session_id,
        device_id=device_id,
        dry_run=dry_run,
        metadata={"confirmed": True} if confirmed else {},
    )
    opts = ActionExecutorOptions(
        requested_by=requested_by,
        session_id=session_id,
        device_id=device_id,
        db=db,
    )
    return _blocking_execute(executor or get_executor(), request, opts)


def get_action_overview(db) -> dict[str, Any]:
    """Overview da camada de ações (sanitizado) para a Central de Operações."""
    return action_stats(db)