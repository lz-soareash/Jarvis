"""Camada determinística de detecção de intenção — Fase 11.2.

Quando o LLM não gera tool_calls mas o texto do usuário contém uma
solicitação claramente operacional, esta camada detecta e sugere a
ferramenta apropriada. Conservadora — nunca executa comandos arbitrários.
"""

import re
from dataclasses import dataclass
from urllib.parse import quote

from app.schemas.ai import ToolCall


@dataclass
class IntentMatch:
    """Resultado da detecção de intenção."""

    tool_call: ToolCall
    confidence: float  # 0.0 a 1.0


# ---------------------------------------------------------------------------
# Padrões de intenção — (regex, tool_name, argument_factory, confidence)
# ---------------------------------------------------------------------------

# Fase 26 — mobile_*: CAMADA ANTES das de computador. Sempre exigem a menção
# explícita ao dispositivo ("celular/telefone/aparelho"), para nunca roubar
# intenções de PC. Confidence 0.96 > PC (0.90-0.95) para desempate ("abra o
# chrome no meu celular" → mobile_open_app, e não open_application).
_MOBILE_PATTERNS: list[tuple[re.Pattern, str, callable, float]] = [
    (re.compile(r"\b(?:bateria\s+do\s+(?:meu\s+)?(?:celular|telefone|aparelho)|quanto\s+est[áa]\s+(?:a\s+)?bateria\s+do\s+(?:meu\s+)?(?:celular|telefone))\b", re.IGNORECASE), "mobile_battery_status", lambda m: {}, 0.96),
    (re.compile(r"\b(?:informa[çc][õo]es\s+do\s+(?:meu\s+)?(?:celular|telefone|aparelho)|status\s+do\s+(?:meu\s+)?(?:celular|telefone|dispositivo)|como\s+est[áa]\s+o\s+(?:meu\s+)?celular)\b", re.IGNORECASE), "mobile_device_info", lambda m: {}, 0.96),
    (re.compile(r"\b(?:(?:como\s+est[áa]\s+(?:a\s+)?)?rede\s+do\s+(?:meu\s+)?(?:celular|telefone)|wifi\s+do\s+(?:meu\s+)?(?:celular|telefone))\b", re.IGNORECASE), "mobile_network_status", lambda m: {}, 0.96),
    (re.compile(r"\b(?:o\s+que\s+est[áa]\s+tocando\s+no\s+(?:meu\s+)?(?:celular|telefone|aparelho)|m[ií]dia\s+no\s+(?:meu\s+)?(?:celular|telefone))\b", re.IGNORECASE), "mobile_media_status", lambda m: {}, 0.96),
    (re.compile(r"\b(?:abra|abrir|abre)\s+(?:o\s+)?(youtube|google|github|gmail)\s+no\s+(?:meu\s+)?(?:celular|telefone|aparelho)\b", re.IGNORECASE), "mobile_open_url", lambda m: {"url": f"https://{m.group(1)}.com"}, 0.96),
    (re.compile(r"\b(?:abra|abrir|abre)\s+(?:o\s+)?(chrome|firefox)\s+no\s+(?:meu\s+)?(?:celular|telefone|aparelho)\b", re.IGNORECASE), "mobile_open_app", lambda m: {"package_name": "com.android.chrome" if m.group(1) == "chrome" else "org.mozilla.firefox"}, 0.96),
    (re.compile(r"\b(?:fa[çc]a\s+(?:o\s+)?(?:meu\s+)?(?:celular|telefone|aparelho)\s+vibrar|vibre?\s+o\s+(?:meu\s+)?(?:celular|telefone|aparelho))\b", re.IGNORECASE), "mobile_vibrate", lambda m: {"duration_ms": 500}, 0.95),
    (re.compile(r"\b(?:aumente?|diminua?|abaixe?|baixe?)\s+(?:o\s+)?volume\s+do\s+(?:meu\s+)?(?:celular|telefone|aparelho)\b", re.IGNORECASE), "mobile_set_volume", lambda m: {"stream": "music", "level": 65 if "aument" in m.group(0) else 35}, 0.92),
    (re.compile(r"\b(?:aumente?|diminua?|abaixe?)\s+(?:a\s+)?(?:luz|brilho)\s+do\s+(?:meu\s+)?(?:celular|telefone|aparelho)\b", re.IGNORECASE), "mobile_set_brightness", lambda m: {"level": 80 if "aument" in m.group(0) else 40}, 0.9),
]

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

    # Fase 28 — multi-dispositivo (0.97 > mobile 0.96 > pc 0.95).
    multi = _detect_multi_device(text)
    if multi is not None:
        return multi

    all_patterns = (
        _MOBILE_PATTERNS
        + _APP_PATTERNS
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


# ---------------------------------------------------------------------------
# Fase 28 — Remote Operations: multi-dispositivo determinístico (remote_operation)
# ---------------------------------------------------------------------------

# Abertura de URL e app por alvo: dispositivo PC vs. móvel mudam os param args.
_PC_DEVICE_TOKENS = frozenset({"computador", "pc", "notebook", "desktop"})
_MOBILE_DEVICE_TOKENS = frozenset({"celular", "telefone", "aparelho", "mobile", "smartphone"})
_OP_SITES = r"youtube|google|github|gmail|chrome|firefox"
_OP_DEVICES = r"celular|telefone|aparelho|computador|pc|notebook|desktop"
_VERB_OPT2 = r"(?:(?:abra|abre|abrir)\s+)?(?:o\s+)?"

# Padrão A — duas cláusulas completas: "abra o X no A e depois o Y no B"
# (verbo opcional em ambas: "abra o X no A e depois o chrome no B").
_VERB_OPT = r"(?:(?:abra|abre|abrir)\s+)?(?:o\s+)?"
_MULTI_A_RE = re.compile(
    rf"\b{_VERB_OPT}(?P<s1>{_OP_SITES})\s+(?:no|na)\s+(?:meu\s+)?(?P<d1>{_OP_DEVICES})"
    rf"\s+(?:e\s+(?:depois|ent[ãa]o)\s+|,?\s*(?:e\s+)?depois\s+|e\s+em\s+seguida\s+|e\s+)"
    rf"{_VERB_OPT2}(?P<s2>{_OP_SITES})\s+(?:no|na)\s+(?:meu\s+)?(?P<d2>{_OP_DEVICES})\b",
    re.IGNORECASE,
)
# Padrão B — site só na 1ª cláusula: "abra o X no A e depois no B".
_MULTI_B_RE = re.compile(
    rf"\b{_VERB_OPT}(?P<s1>{_OP_SITES})\s+(?:no|na)\s+(?:meu\s+)?(?P<d1>{_OP_DEVICES})"
    rf"\s+(?:e\s+(?:depois|ent[ãa]o)\s+|,?\s*(?:e\s+)?depois\s+|e\s+em\s+seguida\s+|e\s+)"
    rf"(?:no|na)\s+(?:meu\s+)?(?P<d2>{_OP_DEVICES})\b",
    re.IGNORECASE,
)


def _operation_step(site: str, device: str) -> dict:
    """Constrói um passo para a Remote Operation (por site e alvo amigável)."""
    site = (site or "").strip().lower()
    device = (device or "").strip().lower()
    if site in ("youtube", "google", "github", "gmail"):
        return {"action": "OPEN_URL", "params": {"url": f"https://{site}.com"}, "target": device}
    if device in _PC_DEVICE_TOKENS:
        return {"action": "OPEN_APP", "params": {"target": site}, "target": device}
    package = {"chrome": "com.android.chrome", "firefox": "org.mozilla.firefox"}.get(site)
    return {"action": "OPEN_APP", "params": {"package_name": package}, "target": device} if package else None


def _multi_device_intent(m) -> IntentMatch | None:
    g = m.groupdict()
    s1, d1 = g.get("s1"), g.get("d1")
    s2, d2 = (g.get("s2") or s1), (g.get("d2") or d1)
    step1 = _operation_step(s1, d1)
    step2 = _operation_step(s2, d2)
    if step1 is None or step2 is None:
        return None
    if step1 == step2:
        return None  # mesmo alvo → intenção single-device (padrões existentes)
    steps = [step1, step2]
    return IntentMatch(
        tool_call=ToolCall(
            name="remote_operation",
            arguments={
                "operation": f"abrir {s1} e {s2 or s1} em dispositivos",
                "steps": steps,
            },
        ),
        confidence=0.97,
    )


def _sanitize_continuation_params(params) -> dict:
    """Params sanitizados da operação anterior (reuso seguro na continuidade)."""
    if not isinstance(params, dict):
        return {}
    return {k: (str(v)[:400] if isinstance(v, str) else v) for k, v in params.items()}


def _detect_multi_device(text: str) -> IntentMatch | None:
    if not text:
        return None
    for pattern in (_MULTI_A_RE, _MULTI_B_RE):
        m = pattern.search(text)
        if m:
            match = _multi_device_intent(m)
            if match is not None:
                return match
    return None


# ---------------------------------------------------------------------------
# Fase 27 — Continuidade multi-turn determinística ("Agora pesquisa FIAP")
# ---------------------------------------------------------------------------

# Marcadores de continuidade explícita (só ativados com contexto de dispositivo).
_CONTINUATION_SEARCH_RE = re.compile(
    r"\b(?P<now>agora\s+)?"
    r"(?:pesquis(?:a|e|ar)|busc(?:a|e|ar)|procur(?:a|e|ar))"
    r"(?:\s+(?:por|o|a))?\s+(?P<query>.+)",
    re.IGNORECASE,
)
# "Agora abre o youtube" (pós-ação de navegação no mesmo dispositivo).
_CONTINUATION_SITE_RE = re.compile(
    r"\b(?P<now>agora\s+)?abre\s+(?:o\s+)?(?P<site>youtube|google|github|gmail)\b",
    re.IGNORECASE,
)
# Sufixo opcional de dispositivo na consulta ("no meu celular") a remover.
_DEVICE_TAIL_RE = re.compile(
    r"\s+(?:no|na)\s+(?:meu\s+)?(?:celular|telefone|aparelho)\s*$", re.IGNORECASE
)
# Ações de navegação/web antecessoras que tornam "pesquisa X" continuidade segura.
_BROWSER_CAPABILITIES = frozenset({"OPEN_APP", "OPEN_URL"})

# Confidence abaixo dos padrões explícitos (0.90-0.96) — a continuidade nunca
# compete com uma menção explícita ao dispositivo.
_CONTINUATION_CONFIDENCE = 0.8


def _strip_device_tail(query: str) -> str:
    return _DEVICE_TAIL_RE.sub("", query).strip(" ,;:-")


# Fase 28 — troca determinística de dispositivo (continuidade): "agora no
# celular" / "continue no computador" / "faz isso no pc" → repete a última ação
# de uma operação anterior no novo dispositivo. Exige marcador explícito.
_CONTINUATION_DEVICE_SWITCH_RE = re.compile(
    rf"\b(?:\bagora\b|\bcontinue\b(?:\s+isso)?|\bcont[íi]nua\b|\bvolta\b|\brepete\b|\brepetir\b|\bfa[çc]a\s+isso\b|\bfaz\s+isso\b|\btamb[ée]m\b)"
    rf"\s+(?:no|na|para|pra|pro)\s+(?:meu\s+|minha\s+|o\s+|a\s+)?(?P<dev>{_OP_DEVICES})\b",
    re.IGNORECASE,
)


def _latest_operation_for_continuation(db, session_id: str | None) -> dict | None:
    """Último passo com status 'success' de uma operação recente da sessão."""
    if db is None or not session_id:
        return None
    from sqlalchemy import select

    from app.models.operations import RemoteOperation

    try:
        ops = db.scalars(
            select(RemoteOperation)
            .where(RemoteOperation.session_id == session_id)
            .order_by(RemoteOperation.created_at.desc())
            .limit(3)
        ).all()
    except Exception:  # noqa: BLE001 — continuidade nunca quebra o turno
        return None
    for op in ops:
        try:
            import json

            steps = json.loads(op.steps_json or "[]")
        except (TypeError, ValueError):
            continue
        for step in reversed(steps):
            if step.get("status") == "success" and step.get("action"):
                return step
    return None


def detect_continuation(
    user_text: str, ctx=None, db=None, session_id: str | None = None
) -> IntentMatch | None:
    """Resolve continuidade SÓ quando há contexto de dispositivo fresco.

    Regras conservadoras (Fase 27):
    - Nunca dispara sem `DeviceContext` fresco (≤ TTL) ou nome de dispositivo;
    - "agora pesquisa/busca/procura X" → `mobile_open_url` (Google Search) no
      mesmo dispositivo da sessão;
    - "pesquisa X" sem "agora" só continua quando a thread anterior era de
      navegação (OPEN_APP/OPEN_URL) no dispositivo ativo;
    - "agora abre o <youtube|google|github|gmail>" → `mobile_open_url` no
      dispositivo ativo;

    Fase 28 — troca de dispositivo determinística: com marcador explícito
    ("agora no celular"/"continue no computador"), reaplica a ÚLTIMA ação bem
    sucedida de uma operação anterior da sessão no novo alvo (via
    `remote_operation`), sem depender do LLM.
    """
    text = (user_text or "").strip()
    if not text:
        return None

    # Fase 28 — troca de dispositivo (exige operação anterior na sessão).
    switched = _CONTINUATION_DEVICE_SWITCH_RE.search(text)
    if switched is not None and db is not None:
        last = _latest_operation_for_continuation(db, session_id)
        if last is not None:
            dev = switched.group("dev").lower()
            steps = [
                {
                    "action": last.get("action"),
                    "params": _sanitize_continuation_params(last.get("params")),
                    "target": dev,
                }
            ]
            return IntentMatch(
                tool_call=ToolCall(
                    name="remote_operation",
                    arguments={
                        "operation": f"repetir no {dev}",
                        "steps": steps,
                    },
                ),
                confidence=_CONTINUATION_CONFIDENCE,
            )

    device = getattr(ctx, "device", None)
    if device is None or not getattr(device, "fresh", False) or not getattr(device, "name", ""):
        return None

    text = (user_text or "").strip()
    if not text:
        return None
    hint = device.name

    m = _CONTINUATION_SEARCH_RE.search(text)
    if m and m.group("query"):
        is_agora = bool(m.group("now"))
        browser_thread = getattr(device, "capability", "") in _BROWSER_CAPABILITIES
        if is_agora or browser_thread:
            query = _strip_device_tail(m.group("query"))
            if query:
                url = "https://www.google.com/search?q=" + quote(query)
                return IntentMatch(
                    tool_call=ToolCall(
                        name="mobile_open_url", arguments={"url": url, "device": hint}
                    ),
                    confidence=_CONTINUATION_CONFIDENCE,
                )

    site = _CONTINUATION_SITE_RE.search(text)
    if site and site.group("now"):
        s = site.group("site").lower()
        url = f"https://{s}.com"
        return IntentMatch(
            tool_call=ToolCall(
                name="mobile_open_url", arguments={"url": url, "device": hint}
            ),
            confidence=_CONTINUATION_CONFIDENCE,
        )

    return None
