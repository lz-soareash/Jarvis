"""Camada determinística de detecção de intenção — Fase 11.2.

Quando o LLM não gera tool_calls mas o texto do usuário contém uma
solicitação claramente operacional, esta camada detecta e sugere a
ferramenta apropriada. Conservadora — nunca executa comandos arbitrários.
"""

import re
from dataclasses import dataclass

from app.schemas.ai import ToolCall


@dataclass
class IntentMatch:
    """Resultado da detecção de intenção."""

    tool_call: ToolCall
    confidence: float  # 0.0 a 1.0


# ---------------------------------------------------------------------------
# Padrões de intenção — (regex, tool_name, argument_factory, confidence)
# ---------------------------------------------------------------------------

# Abertura de aplicativos por alias
_APP_PATTERNS: list[tuple[re.Pattern, str, callable, float]] = [
    # VS Code
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:vs\s*code|visual\s+studio\s+code|code)\b", re.IGNORECASE), "open_application", lambda m: {"target": "vscode"}, 0.95),
    # Chrome
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:chrome|google\s+chrome)\b", re.IGNORECASE), "open_application", lambda m: {"target": "chrome"}, 0.95),
    # Firefox
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:firefox|mozilla\s+firefox)\b", re.IGNORECASE), "open_application", lambda m: {"target": "firefox"}, 0.95),
    # Spotify
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?spotify\b", re.IGNORECASE), "open_application", lambda m: {"target": "spotify"}, 0.95),
    # Notepad
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:bloco\s+de\s+notas|notepad)\b", re.IGNORECASE), "open_application", lambda m: {"target": "notepad"}, 0.95),
    # Edge
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:edge|microsoft\s+edge|ms\s+edge)\b", re.IGNORECASE), "open_application", lambda m: {"target": "edge"}, 0.95),
    # Explorer
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:explorador|file\s+explorer|explorer|meus\s+arquivos)\b", re.IGNORECASE), "open_application", lambda m: {"target": "explorer"}, 0.95),
    # Terminal/CMD
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:terminal|cmd|prompt\s+de\s+comando)\b", re.IGNORECASE), "open_application", lambda m: {"target": "terminal"}, 0.90),
    # PowerShell
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:powershell|ps)\b", re.IGNORECASE), "open_application", lambda m: {"target": "powershell"}, 0.90),
    # Calculator
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:[oa]\s+)?(?:calculadora|calc|calculator)\b", re.IGNORECASE), "open_application", lambda m: {"target": "calc"}, 0.95),
    # Paint
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:paint|mspaint)\b", re.IGNORECASE), "open_application", lambda m: {"target": "paint"}, 0.95),
    # Task Manager
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:gerenciador\s+de\s+tarefas|task\s*manager)\b", re.IGNORECASE), "open_application", lambda m: {"target": "task manager"}, 0.95),
    # Settings
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:as?\s+)?(?:configura[çc][õo]es|painel\s+de\s+controle|settings)\b", re.IGNORECASE), "open_application", lambda m: {"target": "settings"}, 0.95),
]

# Abertura de URLs
_URL_PATTERNS: list[tuple[re.Pattern, str, callable, float]] = [
    # YouTube
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:youtube|yt)\b", re.IGNORECASE), "open_url", lambda m: {"url": "https://youtube.com"}, 0.95),
    # Google
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:google|google\s+com)\b", re.IGNORECASE), "open_url", lambda m: {"url": "https://google.com"}, 0.90),
    # GitHub
    (re.compile(r"\b(?:abra|abrir|abre|abre\s+o)\s+(?:o\s+)?(?:github)\b", re.IGNORECASE), "open_url", lambda m: {"url": "https://github.com"}, 0.95),
    # Genérico: "abra <algo>.com" ou "abra <algo>.com.br"
    (re.compile(r"\b(?:abra|abrir|abre)\s+(?:https?://)?(?:www\.)?([a-zA-Z0-9-]+\.(?:com|com\.br|org|net|io))\b", re.IGNORECASE), "open_url", lambda m: {"url": f"https://{m.group(1)}"}, 0.85),
]

# Controle de mídia
_MEDIA_PATTERNS: list[tuple[re.Pattern, str, callable, float]] = [
    (re.compile(r"\b(?:toque|reproduza|play|d[áa]\s+play|iniciar\s+m[ií]dia|continue\s+tocando)\b", re.IGNORECASE), "play_media", lambda m: {}, 0.90),
    (re.compile(r"\b(?:pause|pause\b|pausar|pausa|d[áa]\s+pause|pare\s+a\s+m[ií]dia)\b", re.IGNORECASE), "pause_media", lambda m: {}, 0.90),
    (re.compile(r"\b(?:pr[óo]xima|m[úu]sica\s+seguinte|next\s*track|pr[óo]xima\s+faixa)\b", re.IGNORECASE), "next_track", lambda m: {}, 0.90),
    (re.compile(r"\b(?:anterior|faixa\s+anterior|m[úu]sica\s+anterior|previous\s*track)\b", re.IGNORECASE), "previous_track", lambda m: {}, 0.90),
]

# Volume
_VOLUME_PATTERNS: list[tuple[re.Pattern, str, callable, float]] = [
    (re.compile(r"\b(?:aument[eo]\s+(?:o\s+)?volume|volume\s+up|mais\s+volume|mais\s+alto)\b", re.IGNORECASE), "volume_up", lambda m: {}, 0.90),
    (re.compile(r"\b(?:diminu[aá]\s+(?:o\s+)?volume|volume\s+down|menos\s+volume|menos\s+alto)\b", re.IGNORECASE), "volume_down", lambda m: {}, 0.90),
    (re.compile(r"\b(?:mudo|mute|silenciar|silencio)\b", re.IGNORECASE), "mute", lambda m: {}, 0.85),
]

# Windows control
_WINDOWS_PATTERNS: list[tuple[re.Pattern, str, callable, float]] = [
    (re.compile(r"\b(?:bloquei?e?\s+(?:o\s+)?computador|lock\s+screen|trava\s+tela)\b", re.IGNORECASE), "lock_computer", lambda m: {}, 0.90),
    (re.compile(r"\b(?:suspenda?\s+(?:o\s+)?computador|dormir|sleep|coloque\s+para\s+dormir)\b", re.IGNORECASE), "sleep_computer", lambda m: {}, 0.85),
    (re.compile(r"\b(?:reini?ci?e?\s+(?:o\s+)?computador|reboot|restart)\b", re.IGNORECASE), "restart_computer", lambda m: {}, 0.90),
    (re.compile(r"\b(?:desligue?\s+(?:o\s+)?computador|desligar|shut\s*down|turn\s*off)\b", re.IGNORECASE), "shutdown_computer", lambda m: {}, 0.95),
]

# Status do computador
_STATUS_PATTERNS: list[tuple[re.Pattern, str, callable, float]] = [
    (re.compile(r"\b(?:como\s+(?:est[áa]|vai)\s+(?:o\s+)?(?:meu\s+)?computador|status\s+(?:do\s+)?computador|estado\s+(?:do\s+)?sistema)\b", re.IGNORECASE), "get_system_status", lambda m: {}, 0.90),
    (re.compile(r"\b(?:quanto\s+(?:de\s+)?(?:ram|mem[óo]ria|CPU|espa[çc]o|disco))\b", re.IGNORECASE), "get_system_stats", lambda m: {}, 0.85),
]

# Fechar aplicativo
_CLOSE_PATTERNS: list[tuple[re.Pattern, str, callable, float]] = [
    (re.compile(r"\b(?:fech(?:e|a|ar)|clos(?:e|ing))\s+(?:o\s+)?(?:vs\s*code|chrome|firefox|spotify|notepad|edge)\b", re.IGNORECASE), "close_application", lambda m: {"process_name": m.group(0).split()[-1]}, 0.85),
]


def detect_intent(user_text: str) -> IntentMatch | None:
    """Detecta intenção operacional no texto do usuário.

    Retorna IntentMatch se encontrar uma intenção clara, None caso contrário.
    Conservadora: só retorna match com confidence >= 0.85.
    """
    if not user_text or not user_text.strip():
        return None

    text = user_text.strip()
    all_patterns = (
        _APP_PATTERNS
        + _URL_PATTERNS
        + _MEDIA_PATTERNS
        + _VOLUME_PATTERNS
        + _WINDOWS_PATTERNS
        + _STATUS_PATTERNS
        + _CLOSE_PATTERNS
    )

    best_match: IntentMatch | None = None
    for pattern, tool_name, arg_factory, confidence in all_patterns:
        m = pattern.search(text)
        if m and confidence >= 0.85:
            if best_match is None or confidence > best_match.confidence:
                try:
                    args = arg_factory(m)
                except Exception:  # noqa: BLE001
                    continue
                best_match = IntentMatch(
                    tool_call=ToolCall(name=tool_name, arguments=args),
                    confidence=confidence,
                )

    return best_match
