"""Ferramentas embutidas do JARVIS (Fase 3 — Tool Engine).

Todas são leves e seguras: leitura de ambiente e acesso estruturado à
memória de longo prazo (Fase 2). Ações com efeito (gravar memória) são
nível 1 (reversível) e executam automaticamente; destrutivas são nível ≥2.
"""

import logging
import os
import platform
from datetime import datetime, timezone

from app.core.config import settings
from app.core.enums import PermissionLevel
from app.services import memory as memory_service

from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger("jarvis.tools")

_WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


class GetTime(Tool):
    """Data e hora atuais do computador (fuso local e UTC)."""

    name = "get_current_time"
    description = "Retorna a data e a hora atuais do computador do usuário."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        now_local = datetime.now().astimezone()
        now_utc = datetime.now(timezone.utc)
        text = (
            f"{now_local.strftime('%d/%m/%Y %H:%M:%S')} "
            f"({_WEEKDAYS[now_local.weekday()]}, fuso {now_local.tzinfo.tzname(now_local) or 'local'}) | "
            f"UTC: {now_utc.strftime('%d/%m/%Y %H:%M:%S')}"
        )
        return ToolResult.success(text)


class GetSystemInfo(Tool):
    """Informações básicas do sistema operacional e hardware (somente leitura)."""

    name = "get_system_info"
    description = "Retorna sistema operacional, arquitetura, versão do Python e CPUs disponíveis."
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        info = {
            "os": platform.system() or "desconhecido",
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "env": settings.env,
        }
        lines = "\n".join(f"- {key}: {value}" for key, value in info.items())
        return ToolResult.success(f"Informações do sistema:\n{lines}")


class StoreMemory(Tool):
    """Grava uma memória de longo prazo (fato, preferência, decisão, etc.)."""

    name = "store_memory"
    description = (
        "Grava um fato, preferência, decisão ou contexto de projeto do usuário "
        "na memória de longo prazo para uso futuro. Categorias: fact | preference "
        "| decision | project | knowledge | task | note | ephemeral | summary. "
        "Se `kind` for omitido, a categoria é inferida do conteúdo. Nunca grave "
        "senhas/tokens/chaves contidos em `content` — o sistema os mascara."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "O conteúdo da memória a gravar."},
            "kind": {
                "type": "string",
                "enum": [
                    "ephemeral",
                    "preference",
                    "fact",
                    "project",
                    "task",
                    "knowledge",
                    "decision",
                    "note",
                    "summary",
                ],
                "description": "Categoria da memória (padrão: inferida do conteúdo).",
            },
            "project": {
                "type": "string",
                "description": "Projeto associado (opcional).",
            },
        },
        "required": ["content"],
    }
    permission_level = PermissionLevel.LEVEL_1

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        content = (arguments.get("content") or "").strip()
        if not content:
            return ToolResult.failure("content é obrigatório")
        kind = arguments.get("kind") or None
        valid = {k.value for k in memory_service.MemoryKind}
        if kind is not None and kind not in valid:
            return ToolResult.failure(f"kind inválido: {kind}")

        memory = await memory_service.create_memory(
            context.db,
            content=content,
            kind=kind,
            session_id=context.session_id,
            provider=context.provider,
            project=arguments.get("project"),
        )
        logger.info("Memória gravada pela ferramenta %s (%s)", memory.id, memory.kind)
        return ToolResult.success(f"Memória gravada (id={memory.id}, kind={memory.kind}).")


class RecallMemory(Tool):
    """Busca memórias relevantes para um tema (semântica ou lexical)."""

    name = "recall_memory"
    description = (
        "Busca na memória de longo prazo os fatos e preferências relevantes "
        "para o assunto informado."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Assunto a buscar na memória."},
            "limit": {"type": "integer", "description": "Máximo de resultados (padrão 5)."},
        },
        "required": ["query"],
    }
    permission_level = PermissionLevel.LEVEL_0

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        query = (arguments.get("query") or "").strip()
        if not query:
            return ToolResult.failure("query é obrigatória")
        limit = min(int(arguments.get("limit") or 5), 10)

        results = await memory_service.search_memories(
            context.db,
            query=query,
            session_id=context.session_id,
            include_global=True,
            limit=limit,
            provider=context.provider,
        )
        if not results:
            return ToolResult.success("Nenhuma memória encontrada para esse assunto.")

        lines = "\n".join(f"- ({m.kind}) {m.content} (score {round(score, 3)})" for m, score in results)
        return ToolResult.success(f"Memórias relevantes:\n{lines}")