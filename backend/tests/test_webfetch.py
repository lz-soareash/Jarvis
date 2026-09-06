"""Testes do WebFetcher (Fase 14) — SSRF aplicada + limites + content-type.

Tudo com `httpx.MockTransport` e resolver fake — zero rede em teste.
"""

import httpx
import pytest

from app.research import fetcher
from app.research.fetcher import (
    FetchContentTypeError,
    FetchTooLargeError,
    WebFetcher,
)


def _page(text: str = "<h1>OI</h1>", content_type: str = "text/html; charset=utf-8", status: int = 200):
    return httpx.Response(status, text=text, headers={"content-type": content_type})


async def _resolve(host: str) -> list[str]:
    return {"example.com": ["93.184.216.34"], "secret.local": ["127.0.0.1"]}.get(host, [])


def _fetcher(**kwargs) -> WebFetcher:
    params: dict = {"resolver": _resolve, "max_bytes": 200, "max_redirects": 3}
    params.update(kwargs)
    return WebFetcher(**params)


@pytest.mark.asyncio
async def test_fetch_success():
    async def handler(request):
        return _page("<html><head><title>T</title></head><body>Olá mundo</body></html>")

    f = _fetcher(transport=httpx.MockTransport(handler))
    page = await f.fetch("http://example.com/x")
    assert page.status == 200
    assert page.final_url == "http://example.com/x"
    assert "Olá mundo" in page.text
    assert page.truncated is False
    await f.close()


@pytest.mark.asyncio
async def test_fetch_rejects_private_target_dns():
    async def handler(request):
        return _page()

    f = _fetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(fetcher.FetchSsrError):
        await f.fetch("http://secret.local/heartbeat")
    await f.close()


@pytest.mark.asyncio
async def test_fetch_rejects_scheme_and_credentials_before_network():
    f = _fetcher()
    for bad in ("file:///etc/passwd", "http://admin:root@example.com/"):
        with pytest.raises(fetcher.FetchSsrError):
            await f.fetch(bad)
    await f.close()


@pytest.mark.asyncio
async def test_fetch_follows_redirects_revalidate_each_hop():
    calls: list[str] = []

    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/mid"})
        if request.url.path == "/mid":
            return httpx.Response(302, headers={"location": "http://secret.local/x"})
        return _page("final", status=200)

    f = _fetcher(transport=httpx.MockTransport(handler))
    # /mid redireciona para host privado → SSRF bloqueia mesmo após hop.
    with pytest.raises(fetcher.FetchSsrError):
        await f.fetch("http://example.com/start")
    assert calls == ["/start", "/mid"]
    assert "secret.local" not in calls
    await f.close()


@pytest.mark.asyncio
async def test_fetch_limit_redirects():
    async def handler(request):
        return httpx.Response(302, headers={"location": "/mais"})

    f = _fetcher(transport=httpx.MockTransport(handler), max_redirects=2)
    with pytest.raises(fetcher.FetchHttpError):
        await f.fetch("http://example.com/a")
    await f.close()


@pytest.mark.asyncio
async def test_fetch_rejects_binary_content_type():
    async def handler(request):
        return httpx.Response(200, content=b"\x89PNGhostile", headers={"content-type": "image/png"})

    f = _fetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchContentTypeError):
        await f.fetch("http://example.com/logo.png")
    await f.close()


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_body():
    async def handler(request):
        return _page("x" * 5000)  # > max_bytes=200

    f = _fetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchTooLargeError):
        await f.fetch("http://example.com/big")
    await f.close()


@pytest.mark.asyncio
async def test_fetch_http_error_status():
    async def handler(request):
        return httpx.Response(500, text="erro")

    f = _fetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(fetcher.FetchHttpError):
        await f.fetch("http://example.com/erro")
    await f.close()


@pytest.mark.asyncio
async def test_fetch_redirect_without_location():
    async def handler(request):
        return httpx.Response(301, headers={})

    f = _fetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(fetcher.FetchHttpError):
        await f.fetch("http://example.com/no-loc")
    await f.close()


@pytest.mark.asyncio
async def test_fetch_never_calls_network_for_public_literal_ok():
    """Garante que o caminho feliz passa pelo resolver e não toca rede real."""
    transport = httpx.MockTransport(lambda r: _page("<p>ok</p>"))

    async def resolve(host):
        return ["8.8.8.8"]

    f = WebFetcher(resolver=resolve, transport=transport)
    page = await f.fetch("http://example.com/")
    assert page.status == 200
    await f.close()