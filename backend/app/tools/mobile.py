"""Fase 26 — VEGA Mobile Agent Orchestration: ferramentas `mobile_*` do Core.

Requisitadas pelo modelo (Gemini/determinístico) com structured tool calls, estas
ferramentas levam o agente até as capabilities de dispositivo da Fase 25:
validação no registry, seleção segura de dispositivo e transporte WAN/LAN via
`app.remote.mobile_agent`.

Segurança e política:
- O LLM nunca recebe `device_id`/tokens/internals — apenas o `device` (nome
  amigável) opcional para desambiguação quando há mais de um dispositivo.
- Leituras (DEVICE_INFO/BATTERY/NETWORK/MEDIA) são LEVEL_0; ajustes
  (OPEN_URL/VIBRATE/SET_VOLUME/OPEN_APP/SET_BRIGHTNESS) são LEVEL_1 — risco
  LOW. O Permission Engine existente do Core decide a execução (sem approval).
- `ACCESSIBILITY_CONTROL` permanece DECLARADO mas não executável (Fase 29) —
  nunca chega aqui.
- Cada resultado é `ok=True` apenas em status `success`; `denied`, `timeout`,
  `unsupported` e `failed` viram `ok=False` sem fingir sucesso. O `output` é um
  JSON estruturado e sanitizado que o SSE `tool_done` entrega à UI mobile.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.enums import PermissionLevel
from app.remote import mobile_agent
from app.tools.base import Tool, ToolContext, ToolResult


def _fail(message: str) -> ToolResult:
    return ToolResult(
        output=json.dumps(
            {"type": "mobile_command_result", "status": "failed", "error": message},
            ensure_ascii=False,
        ),
        ok=False,
    )


def _guard_db(context: ToolContext) -> str | None:
    if context.db is None:
        return "Core sem acesso ao banco de dispositivos."
    return None


class _MobileTool(Tool):
    """Base comum das tools de dispositivo (valida + despacha + estrutura o resultado)."""

    capability: str = ""
    # Nomes dos parâmetros declarados que são repassados como args da capability.
    forward: tuple[str, ...] = ()

    async def run(self, context: ToolContext, **arguments: Any) -> ToolResult:
        guarded = _guard_db(context)
        if guarded is not None:
            return _fail(guarded)

        hint = arguments.pop("device", None)
        if not isinstance(hint, str) or not hint.strip():
            hint = None

        args: dict[str, Any] = {}
        for key in self.forward:
            if key in arguments:
                args[key] = arguments[key]

        try:
            outcome = await mobile_agent.dispatch_mobile(
                context.db,
                capability=self.capability,
                args=args,
                session_id=context.session_id,
                hint=hint,
            )
        except Exception as exc:  # noqa: BLE001 — bug interno vira resultado p/ o modelo
            return _fail(f"{type(exc).__name__}: {exc}")

        # Fase 27 — propaga o resultado REAL ao contexto multi-turn da sessão,
        # permitindo continuidade determinística ("Agora pesquisa FIAP"). O id
        # fica no contexto p/ auditoria; só o nome vai ao prompt. Best-effort:
        # registrar o contexto nunca quebra a execução da tool.
        try:
            from app.ai import turn_context as turn_ctx

            if context.session_id and outcome.device_name:
                turn_ctx.record_device_outcome(
                    context.db,
                    context.session_id,
                    device_id=outcome.device_id or "",
                    device_name=outcome.device_name,
                    capability=outcome.capability,
                    status=outcome.status,
                    transport=outcome.transport,
                    summary=outcome.summary,
                )
        except Exception:  # noqa: BLE001
            pass

        payload: dict[str, Any] = {
            "type": "mobile_command_result",
            "capability": outcome.capability,
            "status": outcome.status,
            "command_id": outcome.command_id,
            "device": outcome.device_name,
            "transport": outcome.transport,
            "result": outcome.result,
            "error": outcome.error,
            "latency_ms": outcome.latency_ms,
            "summary": outcome.summary,
        }
        if outcome.devices:
            payload["devices"] = outcome.devices
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, default=str),
            ok=outcome.ok,
        )


class MobileDeviceInfo(_MobileTool):
    name = "mobile_device_info"
    description = (
        "Consulta informações gerais do celular pareado (VEGA Mobile): modelo, "
        "fabricante e versão do Android. Nunca expõe IDs de hardware."
    )
    capability = "DEVICE_INFO"
    parameters = {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Nome amigável do celular, usado quando há mais de um dispositivo pareado.",
            }
        },
        "required": [],
    }
    permission_level = PermissionLevel.LEVEL_0


class MobileBatteryStatus(_MobileTool):
    name = "mobile_battery_status"
    description = (
        "Consulta o nível e o estado da bateria do celular pareado (VEGA Mobile), "
        "incluindo se está carregando."
    )
    capability = "BATTERY_STATUS"
    parameters = {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Nome amigável do celular, usado quando há mais de um dispositivo pareado.",
            }
        },
        "required": [],
    }
    permission_level = PermissionLevel.LEVEL_0


class MobileNetworkStatus(_MobileTool):
    name = "mobile_network_status"
    description = (
        "Consulta o estado de rede do celular pareado (VEGA Mobile): tipo "
        "(wi-fi/móvel/offline) e conectividade atual."
    )
    capability = "NETWORK_STATUS"
    parameters = {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Nome amigável do celular, usado quando há mais de um dispositivo pareado.",
            }
        },
        "required": [],
    }
    permission_level = PermissionLevel.LEVEL_0


class MobileMediaStatus(_MobileTool):
    name = "mobile_media_status"
    description = (
        "Consulta o que está tocando de mídia no celular pareado (VEGA Mobile), "
        "de forma sanitizada (sem dados de conta)."
    )
    capability = "MEDIA_STATUS"
    parameters = {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Nome amigável do celular, usado quando há mais de um dispositivo pareado.",
            }
        },
        "required": [],
    }
    permission_level = PermissionLevel.LEVEL_0


class MobileOpenUrl(_MobileTool):
    name = "mobile_open_url"
    description = (
        "Abre uma URL no navegador do celular pareado (VEGA Mobile). "
        "Nunca abrir URLs com segredos, credenciais ou tokens."
    )
    capability = "OPEN_URL"
    forward = ("url",)
    parameters = {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Nome amigável do celular, usado quando há mais de um dispositivo pareado.",
            },
            "url": {"type": "string", "description": "URL completa (https://...) a abrir."},
        },
        "required": ["url"],
    }
    permission_level = PermissionLevel.LEVEL_1


class MobileVibrate(_MobileTool):
    name = "mobile_vibrate"
    description = "Faz o celular pareado (VEGA Mobile) vibrar por um intervalo curto."
    capability = "VIBRATE"
    forward = ("duration_ms",)
    parameters = {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Nome amigável do celular, usado quando há mais de um dispositivo pareado.",
            },
            "duration_ms": {
                "type": "integer",
                "description": "Duração da vibração em milissegundos (ex.: 500).",
                "minimum": 200,
                "maximum": 3000,
            },
        },
        "required": ["duration_ms"],
    }
    permission_level = PermissionLevel.LEVEL_1


class MobileSetVolume(_MobileTool):
    name = "mobile_set_volume"
    description = (
        "Ajusta o volume do celular pareado (VEGA Mobile) (stream de mídia por "
        "padrão; nível 0-100)."
    )
    capability = "SET_VOLUME"
    forward = ("stream", "level")
    parameters = {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Nome amigável do celular, usado quando há mais de um dispositivo pareado.",
            },
            "stream": {
                "type": "string",
                "enum": ["music", "ring", "alarm", "notification"],
                "description": "Stream de áudio (default: music).",
            },
            "level": {
                "type": "integer",
                "description": "Nível de volume (0-100).",
                "minimum": 0,
                "maximum": 100,
            },
        },
        "required": ["level"],
    }
    permission_level = PermissionLevel.LEVEL_1


class MobileOpenApp(_MobileTool):
    name = "mobile_open_app"
    description = (
        "Abre um aplicativo no celular pareado (VEGA Mobile). Apenas pacotes da "
        "allowlist configurada no Core são executáveis."
    )
    capability = "OPEN_APP"
    forward = ("package_name",)
    parameters = {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Nome amigável do celular, usado quando há mais de um dispositivo pareado.",
            },
            "package_name": {
                "type": "string",
                "description": "Pacote Android do app (ex.: com.android.settings, com.android.chrome).",
            },
        },
        "required": ["package_name"],
    }
    permission_level = PermissionLevel.LEVEL_1


class MobileSetBrightness(_MobileTool):
    name = "mobile_set_brightness"
    description = (
        "Ajusta o brilho da tela do celular pareado (VEGA Mobile) (0-100). "
        "Exige permissão de sistema WRITE_SETTINGS no dispositivo."
    )
    capability = "SET_BRIGHTNESS"
    forward = ("level",)
    parameters = {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Nome amigável do celular, usado quando há mais de um dispositivo pareado.",
            },
            "level": {
                "type": "integer",
                "description": "Nível de brilho (0-100).",
                "minimum": 0,
                "maximum": 100,
            },
        },
        "required": ["level"],
    }
    permission_level = PermissionLevel.LEVEL_1


_ALL = (
    MobileDeviceInfo,
    MobileBatteryStatus,
    MobileNetworkStatus,
    MobileMediaStatus,
    MobileOpenUrl,
    MobileVibrate,
    MobileSetVolume,
    MobileOpenApp,
    MobileSetBrightness,
)


def mobile_tools() -> list[Tool]:
    return [tool_cls() for tool_cls in _ALL]