"""Web Fetch (Fase 14) — download de página validado por SSRF e limites.

`WebFetcher`:
- valida SSRF sintática (`validate_url`) e DNS (`assert_public_target`) com
  resolver injetável;
- segue redirects manualmente, REVALIDANDO cada hop (DNS rebinding);
- limita bytes, redirecionamentos e timeout; rejeita MIME binários;
- devolve `PageContent` com texto decodificado (charset do header/declaração).

Nenhum endpoint da rede interna é alcançável — mesmo que o conteúdo web seja
hostil (prompt injection), a superfície de rede é a fetch URL validada.
"""

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.core.config import settings

from .ssrf import DnsPolicyError, UrlPolicyError, assert_public_target, redact_url, validate_url

logger = logging.getLogger("jarvis.research.fetcher")

# MIME aceitos para análise textual (o resto é "binário" → rejeitado).
ALLOWED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
    "application/json",
    "application/ld+json",
)

_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_CHARSET_RE = re.compile(r"charset=([\w\-]+)", re.I)
_MAX_CHARSET_LEN = 40


class WebFetchError(RuntimeError):
    """Erro de coleta de página (timeout/HTTP/MIME/limite) — com categoria."""

    def __init__(self, message: str, category: str = "fetch") -> None:
        super().__init__(message)
        self.category = category


class FetchTimeoutError(WebFetchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "timeout")


class FetchHttpError(WebFetchError):
    pass


class FetchContentTypeError(WebFetchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "content_type")


class FetchTooLargeError(WebFetchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "too_large")


class FetchSsrError(WebFetchError):
    def __init__(self, message: str, inner: Exception) -> None:
        super().__init__(message, "ssrf")
        self.inner = inner


@dataclass
class PageContent:
    """Conteúdo de uma página coletada (texto já decodificado)."""

    url: str
    final_url: str
    status: int
    content_type: str = ""
    text: str = ""
    text_length: int = 0
    truncated: bool = False
    headers: dict = field(default_factory=dict)


DEFAULT_RESOLVER = None  # sentinela: usa getaddrinfo


class WebFetcher:
    """Baixa e valida conteúdo web (SSRF + limites) para análise."""

    def __init__(
        self,
        *,
        timeout: float | None = None,
        max_bytes: int | None = None,
        max_redirects: int | None = None,
        user_agent: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver=None,
    ) -> None:
        self._timeout = timeout if timeout is not None else settings.web_fetch_timeout
        self._max_bytes = max_bytes if max_bytes is not None else settings.web_fetch_max_bytes
        self._max_redirects = max(max_redirects if max_redirects is not None else settings.web_fetch_max_redirects, 0)
        self._user_agent = user_agent or settings.web_fetch_user_agent
        self._transport = transport
        self._resolver = resolver
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=False,
                headers={"User-Agent": self._user_agent, "Accept": "*/*"},
            )
        return self._client

    # ------------------------------------------------------------------
    # Público
    # ------------------------------------------------------------------
    async def fetch(self, url: str) -> PageContent:
        """Baixa `url` com proteção SSRF completa e limites. Nunca rede interna."""
        return await self._fetch_loop(url, hops=0)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Interno
    # ------------------------------------------------------------------
    async def _fetch_loop(self, url: str, *, hops: int) -> PageContent:
        try:
            url = validate_url(url)
        except UrlPolicyError as exc:
            raise FetchSsrError(f"Política de URL rejeitou {redact_url(url)}: {exc}", exc) from exc

        try:
            await assert_public_target(urlparse(url).hostname, self._resolver)
        except DnsPolicyError as exc:
            raise FetchSsrError(f"SSRF bloqueou {redact_url(url)}: {exc}", exc) from exc

        client = self._get_client()
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code in _REDIRECT_STATUS:
                    location = resp.headers.get("location")
                    await resp.aclose()
                    if not location:
                        raise FetchHttpError(f"Redirect sem Location (HTTP {resp.status_code}).")
                    target = str(httpx.URL(resp.request.url).join(location))
                    if hops + 1 > self._max_redirects:
                        raise FetchHttpError(f"Limite de redirects excedido ({self._max_redirects}).")
                    logger.info("Fetch redirect (%s/%s) → %s", hops + 1, self._max_redirects, redact_url(target))
                    return await self._fetch_loop(target, hops=hops + 1)

                content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if content_type and not self._is_allowed(content_type):
                    raise FetchContentTypeError(f"Tipo de conteúdo '{content_type}' não é analisável (binário).")
                if resp.status_code >= 400:
                    raise FetchHttpError(f"HTTP {resp.status_code} ao buscar {redact_url(str(resp.url))}.")

                body = await self._read_body(resp)
                final_url = str(resp.url)
        except FetchSsrError:
            raise
        except FetchContentTypeError:
            raise
        except httpx.TimeoutException as exc:
            raise FetchTimeoutError(f"Timeout ao buscar {redact_url(url)}.") from exc
        except httpx.HTTPError as exc:
            raise FetchHttpError(f"Erro HTTP ao buscar {redact_url(url)}: {exc}") from exc

        charset = self._charset_from(resp.headers.get("content-type") or "", body)
        text = self._decode(body, charset or _charset_from_meta(body))
        return PageContent(
            url=url,
            final_url=final_url,
            status=resp.status_code,
            content_type=content_type,
            text=text,
            text_length=len(body),
            truncated=len(body) >= self._max_bytes,
            headers={k.lower(): v for k, v in _safe_headers(resp.headers).items()},
        )

    async def _read_body(self, resp) -> bytes:
        cap = self._max_bytes
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > cap:
                    await resp.aclose()
                    raise FetchTooLargeError(f"Corpo excede {cap} bytes ({redact_url(str(resp.url))}).")
                chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise FetchTimeoutError(f"Timeout lendo corpo de {redact_url(str(resp.url))}.") from exc
        except httpx.HTTPError as exc:
            raise FetchHttpError(f"Erro lendo corpo de {redact_url(str(resp.url))}: {exc}") from exc
        body = b"".join(chunks)
        if total >= cap:
            body = body[:cap]
        return body

    def _is_allowed(self, content_type: str) -> bool:
        if not content_type:
            return True  # sem header, tenta analisar (limite de bytes protege)
        if content_type in ALLOWED_CONTENT_TYPES:
            return True
        if content_type.startswith("text/"):
            return True
        if content_type.endswith("+xml") or content_type.endswith("+json"):
            return True
        return False

    @staticmethod
    def _charset_from(content_type: str, body: bytes) -> str | None:
        m = _CHARSET_RE.search(content_type)
        if m:
            return m.group(1)[:_MAX_CHARSET_LEN]
        return _charset_from_meta(body)

    @staticmethod
    def _decode(body: bytes, charset: str | None) -> str:
        for enc in (charset, "utf-8", "latin-1"):
            if not enc:
                continue
            try:
                return body.decode(enc, errors="strict")
            except (LookupError, UnicodeDecodeError):
                continue
        return body.decode("utf-8", errors="replace")


def _charset_from_meta(body: bytes) -> str | None:
    head = body[:4096].decode("latin-1", errors="ignore").lower()
    m = _CHARSET_RE.search(head)
    return m.group(1)[:_MAX_CHARSET_LEN] if m else None


def _safe_headers(headers) -> dict:
    out: dict = {}
    for key, value in headers.items():
        low = key.lower()
        if low in ("content-type", "content-length", "server", "date"):
            out[low] = str(value)
    return out


async def aclose_quietly(fetcher: WebFetcher | None) -> None:
    if fetcher is not None:
        try:
            await fetcher.close()
        except Exception:  # noqa: BLE001 — fechamento não deve quebrar fluxo
            pass