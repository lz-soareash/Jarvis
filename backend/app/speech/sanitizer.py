"""Speech Sanitizer — limpa o texto antes do TTS (Fase 6b).

Remove/transforma tudo que não faz sentido em voz: Markdown, crases, blocos de
código, links, URLs, HTML, JSON bruto, emojis, símbolos decorativos e estruturas
técnicas. O texto DISPLAY (interface) nunca é alterado — esta camada atua apenas
sobre uma cópia preparada para fala.

Cada regra é uma função pura e registrada de forma explícita, facilitando a
adição de novas regras sem tocar no fluxo principal.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("jarvis.speech.sanitizer")

# Blocos de código Markdown ```...``` ou ~~~...~~~ (com ou sem linguagem)
_CODE_BLOCK = re.compile(r"```[^\n]*\n.*?```|~~~.*?~~~", re.DOTALL)
# Código inline `...`
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# Links Markdown [texto](url) -> mantém só o texto
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# URLs (http/https/www) e domínios, evitando pegar pontuação final
_URL = re.compile(
    r"(?i)\bhttps?://[^\s<>'\"]+|www\.[^\s<>'\"]+|"
    r"\b[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?::\d+)?(?:/[^\s<>'\"]*)?"
)
# Tags HTML <...>
_HTML_TAG = re.compile(r"<[^>]+>")
# Cabeçalhos Markdown #, ##, ###
_HEADINGS = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
# Markdown de formatação: ** **, __ __, * *, _ _, e caracteres decorativos
_MARKDOWN_EM = re.compile(r"(\*\*|__|\*|_|~~|~|>|`)\s*")
# Listas Markdown: - , * , + no início de linha, seguidos de espaço
_LIST_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
# Numeração de lista "1. ", "1) "
_LIST_NUMBER = re.compile(r"^\s*\d{1,3}[.)]\s+", re.MULTILINE)
# Símbolos técnicos repetitivos
_DECORATIVE = re.compile(r"[|/\\]{2,}|-{3,}|={3,}|_{3,}|#+\s|~{2,}|•+")
# Emojis e símbolos não-latinos (blocos Unicode de pictogramas e chaves decorativas)
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\U0000FE0F\U0001F3FB-\U0001F3FF"
    "\U00002600-\U000026FF\U00002700-\U000027BF\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF\U00002300-\U000023FF\U000025A0-\U000025FF]"
)
# JSON bruto: objetos { ... } são inequívocos; arrays [...] só quando contêm objetos
# (evita colidir com links Markdown "[texto](url)").
_JSON_BLOCK = re.compile(r"\{[^{}]*\}|\[\s*\{[^\]]*\]")
# Heurística: parece JSON se houver chave aspas ("k":) ou estrutura de objeto.
_JSON_LOOKS = re.compile(r'"\w+"\s*:')

# Strings que representam código/elementos técnicos que não devem ser lidos
_CODE_HINT = re.compile(
    r"\b(function|def|class|import|from|return|=>|const|let|var|async|await|"
    r"print|console\.|lambda|self|nil|None|True|False|null|true|false)\b"
)


def remove_code_blocks(text: str) -> str:
    """Substitui blocos de código por um aviso resumido (não lê o código)."""
    if _CODE_BLOCK.search(text):
        return _CODE_BLOCK.sub(" Encontrei um trecho de código, que está disponível na tela. ", text)
    return _CODE_BLOCK.sub(" ", text)


def summarize_json(text: str) -> str:
    """Transforma JSON bruto em um resumo amigável (dados disponíveis na tela)."""
    if not _JSON_BLOCK.search(text) or not _JSON_LOOKS.search(text):
        return text
    # Substitui blocos JSON simples (sem aninhamento profundo) por aviso
    simplified = _JSON_BLOCK.sub(" os dados estruturados estão disponíveis na tela. ", text)
    return simplified


def remove_inline_code(text: str) -> str:
    """Remove código inline entre crases."""
    return _INLINE_CODE.sub(" ", text)


def remove_markdown_links(text: str) -> str:
    """Converte [texto](url) mantendo apenas o texto do link."""
    return _MD_LINK.sub(r"\1", text)


def remove_urls(text: str) -> str:
    """Substitui URLs por menção de que há um link na tela."""
    return _URL.sub(" há um link disponível na tela. ", text)


def remove_html(text: str) -> str:
    """Remove tags HTML, mantendo o texto interno."""
    return _HTML_TAG.sub(" ", text)


def remove_headings(text: str) -> str:
    """Remove símbolos de cabeçalho Markdown (#, ##, ...), preservando o texto."""
    return _HEADINGS.sub("", text)


def remove_markdown_emphasis(text: str) -> str:
    """Remove ** **, __ __, * *, _ _ e outros marcadores de ênfase."""
    return _MARKDOWN_EM.sub(" ", text)


def remove_list_bullets(text: str) -> str:
    """Remove marcadores de lista (-, *, +) no início de linhas."""
    return _LIST_BULLET.sub(" ", text)


def remove_list_numbers(text: str) -> str:
    """Remove numeração de listas (1., 2), mantendo o conteúdo."""
    return _LIST_NUMBER.sub(" ", text)


def remove_decoratives(text: str) -> str:
    """Remove símbolos decorativos repetitivos."""
    return _DECORATIVE.sub(" ", text)


def remove_emojis(text: str) -> str:
    """Remove emojis e pictogramas inadequados para leitura."""
    return _EMOJI.sub(" ", text)


def summarize_json(text: str) -> str:
    """Transforma JSON bruto em um resumo amigável (dados disponíveis na tela)."""
    if not _JSON_BLOCK.search(text):
        return text
    # Substitui blocos JSON simples (sem aninhamento profundo) por aviso
    simplified = _JSON_BLOCK.sub(" os dados estruturados estão disponíveis na tela. ", text)
    return simplified


class SpeechSanitizer:
    """Pipeline de limpeza de texto para fala, com regras registradas.

    A ordem das regras importa (blocos de código e JSON antes de markdown,
    para não fragmentar). Cada etapa é independente e testável.
    """

    def __init__(self) -> None:
        self._rules: list[tuple[str, callable]] = []
        self.register("code_blocks", remove_code_blocks)
        self.register("json", summarize_json)
        self.register("inline_code", remove_inline_code)
        self.register("html", remove_html)
        self.register("links", remove_markdown_links)
        self.register("urls", remove_urls)
        self.register("headings", remove_headings)
        self.register("emphasis", remove_markdown_emphasis)
        self.register("list_bullets", remove_list_bullets)
        self.register("list_numbers", remove_list_numbers)
        self.register("decoratives", remove_decoratives)
        self.register("emojis", remove_emojis)

    def register(self, name: str, fn: callable) -> None:
        """Registra uma nova regra de limpeza (adicione novas sem tocar no fluxo)."""
        self._rules.append((name, fn))

    def sanitize(self, text: str) -> str:
        """Aplica todas as regras registradas e normaliza espaços finais."""
        if not text:
            return text
        out = text
        for name, fn in self._rules:
            before = out
            try:
                out = fn(out)
            except Exception:  # noqa: BLE001 — uma regra nunca pode derrubar a fala
                logger.exception("Falha na regra de sanitização %s", name)
                out = before
            if out != before:
                logger.debug("speech_sanitized::%s", name)
        out = _MULTI_SPACE.sub(" ", out)
        out = re.sub(r"\s+([.,;:!?])", r"\1", out)
        out = out.strip()
        if out and out[-1] not in ".!?":
            out += "."
        return out


_MULTI_SPACE = re.compile(r"\s{2,}")

# Instância compartilhada (a ordem de regras é determinística)
sanitizer = SpeechSanitizer()
