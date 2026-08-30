"""Ferramentas internas de desenvolvimento e inspeção (Fase 8 — Developer Tools).

Somente operações **internas e predefinidas** do Tool Engine: listar/inspecionar
ferramentas e schemas, consultar configuração (com segredos mascarados) e ler
informações de diagnóstico permitidas.

**NUNCA** executa comandos, shell, terminal, subprocessos ou código arbitrário.
A execução no sistema operacional continua sendo papel do futuro Local Agent,
isolado do Core, com suas próprias políticas de segurança. Esta fase preserva
essa separação arquitetural: o Core apenas inspeciona o próprio ambiente.
"""

from __future__ import annotations

import json
import logging
import platform

from app.core.config import settings
from app.core.enums import AgentState, PermissionLevel, RiskLevel
from app.models import ToolPolicy
from app.services import permissions as permissions_service
from app.tools import registry as tool_registry
from app.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("jarvis.tools.developer")

# Campos de configuração considerados SENSÍVEIS (valores sempre mascarados).
_SENSITIVE_FIELD = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
)


def _is_sensitive(field: str) -> bool:
    low = field.lower()
    return any(part in low for part in _SENSITIVE_FIELD)


def _mask(value: object) -> object:
    """Mascara um valor sensível mantendo a ideia de comprimento (segurança por omissão)."""
    if value is None:
        return None
    s = str(value)
    if not s:
        return ""
    return f"{s[0]}{'•' * min(len(s) - 1, 8)}"


def _tool_catalog_entry(db, tool: Tool) -> dict:
    level = permissions_service.effective_level(db, tool.name)
    source = "override" if db.get(ToolPolicy, tool.name) is not None else "default"
    return {
        "name": tool.name,
        "description": tool.description,
        "level": level.value,
        "level_label": level.name,
        "risk": tool.risk.value,
        "requires_confirmation": level.requires_confirmation,
        "blocked_by_default": level.blocked_by_default,
        "source": source,
    }


class DevListTools(Tool):
    """Lista as ferramentas registradas no Tool Engine (catálogo e níveis)."""

    name = "dev_list_tools"
    description = (
        "Dev: lista todas as ferramentas registradas no Tool Engine, com nome, "
        "descrição, nível de permissão efetivo, risco e flags de confirmação/bloqueio."
    )
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        db = context.db
        if db is None:
            return ToolResult.failure("Contexto sem sessão de banco (necessária p/ níveis efetivos).")
        tools = tool_registry.get_tool_registry().all()
        if not tools:
            return ToolResult.success("Nenhuma ferramenta registrada.")
        lines = ["Ferramentas registradas no Tool Engine:", ""]
        for t in tools:
            entry = _tool_catalog_entry(db, t)
            flags = []
            if entry["requires_confirmation"]:
                flags.append("exige confirmação")
            if entry["blocked_by_default"]:
                flags.append("bloqueada por padrão")
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(
                f"- {entry['name']} [L{entry['level']} · {entry['risk']}]{suffix} — {entry['description']}"
            )
        lines.append("")
        lines.append(f"Total: {len(tools)} ferramentas registradas.")
        return ToolResult.success("\n".join(lines))


class DevGetToolSchema(Tool):
    """Inspeciona o schema (parâmetros) de uma ferramenta registrada."""

    name = "dev_get_tool_schema"
    description = (
        "Dev: retorna o schema de parâmetros, descrição e política efetiva de uma "
        "ferramenta registrada, dado o nome dela."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "Nome da ferramenta a inspecionar (ex.: dev_list_tools).",
            },
        },
        "required": ["tool"],
    }
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        name = (arguments.get("tool") or "").strip()
        if not name:
            return ToolResult.failure("tool é obrigatório")
        registry = tool_registry.get_tool_registry()
        tool = registry.get(name)
        if tool is None:
            names = ", ".join(t.name for t in registry.all())
            return ToolResult.success(f"Ferramenta não encontrada: {name}. Registradas: {names}")
        decl = tool.declaration()
        db = context.db
        level = permissions_service.effective_level(db, name) if db else tool.permission_level
        return ToolResult.success(
            f"Ferramenta: {decl.name}\n"
            f"Descrição: {decl.description}\n"
            f"Nível efetivo: L{level.value} ({level.name})\n"
            f"Risco: {tool.risk.value}\n"
            f"Schema de parâmetros:\n{json.dumps(decl.parameters, ensure_ascii=False, indent=2)}"
        )


class DevGetConfig(Tool):
    """Consulta a configuração do Core com todos os segredos mascarados."""

    name = "dev_get_config"
    description = (
        "Dev: retorna a configuração de runtime do Core (versão, ambiente, limites), "
        "com qualquer valor sensível (chaves/tokens/senhas) sempre mascarado."
    )
    parameters = {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "Filtra por seção/prefixo (ex.: tts_, ai_). Vazio = tudo.",
            },
        },
        "required": [],
    }
    permission_level = PermissionLevel.LEVEL_1
    risk = RiskLevel.MEDIUM

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        section = (arguments.get("section") or "").strip()
        safe: dict[str, object] = {}
        for field, value in settings.model_dump().items():
            if section and not field.startswith(section):
                continue
            safe[field] = _mask(value) if _is_sensitive(field) else value
        lines = [f"{k}: {v}" for k, v in sorted(safe.items())]
        if section and not lines:
            return ToolResult.success(f"Nenhum campo de configuração para a seção '{section}'.")
        return ToolResult.success("Configuração do Core (segredos mascarados):\n" + "\n".join(lines))


class DevDiagnostics(Tool):
    """Diagnósticos de leitura permitidos: versões, contagens e saúde interna."""

    name = "dev_diagnostics"
    description = (
        "Dev: retorna diagnóstico de leitura do Core — versões (Python/sistema/app), "
        "número de ferramentas registradas, estado do banco/permissões, cache de TTS e "
        "estados do agente. Somente informações permitidas; nunca segredos."
    )
    parameters = {}
    permission_level = PermissionLevel.LEVEL_0
    risk = RiskLevel.LOW

    async def run(self, context: ToolContext, **arguments) -> ToolResult:
        from app.services import tts as tts_service

        registry = tool_registry.get_tool_registry()
        diag = {
            "app": settings.app_name,
            "version": settings.version,
            "environment": settings.env,
            "python": platform.python_version(),
            "python_impl": platform.python_implementation(),
            "os": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(),
            "ai_provider": settings.gemini_model,
            "ai_configured": bool(settings.gemini_api_key),
            "ai_configured_mask": _mask(settings.gemini_api_key) if settings.gemini_api_key else "",
            "tools_registered": len(registry.all()),
            "tts_provider": settings.tts_provider,
            "tts_voice": settings.tts_voice,
            "tts_cache": tts_service.cache_info(),
            "max_tool_rounds": settings.max_tool_rounds,
            "max_context_messages": settings.max_context_messages,
            "files_root": settings.files_root,
            "agent_states": [s.value for s in AgentState],
        }
        lines = [f"{k}: {v}" for k, v in diag.items()]
        return ToolResult.success("Diagnóstico do Core (somente leitura permitida):\n" + "\n".join(lines))
