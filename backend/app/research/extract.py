"""Extração de conteúdo textual de HTML (Fase 14) — stdlib apenas.

`extract_html` produz `ExtractResult` com título, headings (`h1..h6`),
texto corrido (sem scripts/styles/nav/footer/comentários), e links absolutos
http(s). A saída é DADO NÃO CONFIÁVEL para qualquer LLM — usa-se o separador
de conteúdo externo na síntese (ver `app.research.synthesis`).
"""

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("jarvis.research.extract")

_SKIP_BLOCK_ELEMENTS = {
    "script": "script",
    "style": "style",
    "noscript": "noscript",
    "nav": "block",
    "footer": "block",
    "aside": "block",
    "form": "block",
}
_DISCARD_TAGS = {"svg", "img", "canvas", "iframe", "audio", "video", "button", "input"}


class _ExtractParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title: str = ""
        self.heading_levels: list[tuple[int, str]] = []  # (nível, texto) em ordem
        self.text_chunks: list[str] = []
        self._links: list[tuple[str, str]] = []  # (href absoluto, texto)
        self._skip_stack: list[str] = []
        self._in_title_tag = False
        self._cur_link_href: str | None = None
        self._cur_link_parts: list[str] = []
        self._pending_text: list[str] = []
        self._last_was_block = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self._skip_stack:
            self._skip_stack.append(tag)
            return
        if tag in _SKIP_BLOCK_ELEMENTS:
            self._skip_stack.append(tag)
            self._flush_inline()
            return
        if tag in _DISCARD_TAGS:
            self._skip_stack.append(tag)
            return
        if tag == "title":
            self._in_title_tag = True
        if tag == "a":
            self._cur_link_href = self._abs(attrs.get("href", ""))
            self._cur_link_parts = []
        elif _is_heading(tag):
            self._flush_inline()
        elif _is_blocky(tag):
            self._flush_inline()

    def handle_endtag(self, tag):
        if self._skip_stack:
            if tag == self._skip_stack[-1]:
                self._skip_stack.pop()
            return
        if tag == "title":
            self._in_title_tag = False
        elif tag == "a" and self._cur_link_href:
            text = _join(self._cur_link_parts)
            if text:
                self._links.append((self._cur_link_href, text))
            self._cur_link_href = None
            self._cur_link_parts = []
        elif _is_heading(tag):
            self._flush_inline(is_heading=True, tag=tag)

    def handle_data(self, data):
        if self._skip_stack:
            return
        text = re.sub(r"\s+", " ", data).strip(" ")
        if not text:
            return
        if self._in_title_tag:
            self.title = _join([self.title, text])
            return
        if self._cur_link_href is not None:
            self._cur_link_parts.append(text)
        self._pending_text.append(text)

    def handle_decl(self, decl):
        pass

    def handle_entityref(self, name):
        pass

    def handle_charref(self, name):
        pass

    def handle_comment(self, data):
        pass

    def _flush_inline(self, *, is_heading: bool = False, tag: str = "") -> None:
        text = _join(self._pending_text)
        self._pending_text = []
        if not text:
            return
        self.text_chunks.append(text)
        if is_heading and _is_heading(tag):
            level = int(tag[1])
            self.heading_levels.append((level, text))

    def _abs(self, href: str) -> str:
        href = href.strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return ""
        url = urljoin(self.base_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ""
        return url


def _is_heading(tag: str) -> bool:
    return len(tag) == 2 and tag[0] == "h" and tag[1] in "123456"


def _is_blocky(tag: str) -> bool:
    return tag in ("p", "div", "br", "li", "ul", "ol", "table", "tr", "section", "article", "header", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre")


def _join(parts: list[str]) -> str:
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return text[:2000]


@dataclass
class ExtractResult:
    """Conteúdo textual extraído de uma página."""

    title: str = ""
    headings: list[tuple[int, str]] = field(default_factory=list)
    text: str = ""  # texto contínuo (blocos separados por parágrafo)
    main_text: str = ""  # primeiros N caracteres de `text`
    links: list[tuple[str, str]] = field(default_factory=list)
    word_count: int = 0


def extract_html(html_body: str, *, base_url: str, max_chars: int = 100_000) -> ExtractResult:
    """Converte HTML em texto estruturado, ignorando roteiro/estilo/navegação."""
    parser = _ExtractParser(base_url or "")
    try:
        parser.feed(html_body or "")
        parser.close()
    except Exception:  # noqa: BLE001 — HTML hostil/malformado nunca derruba extração
        logger.debug("HTML malformado ignorado (base_url=%s)", base_url)
    parser._flush_inline()
    text = "\n\n".join(parser.text_chunks)
    result = ExtractResult(
        title=parser.title,
        headings=parser.heading_levels,
        text=text,
        main_text=text[:max_chars],
        links=[(url, t) for url, t in parser._links if url],
    )
    result.word_count = len(text.split())
    return result