"""Testes de SSRF (Fase 14) — a camada mais crítica da Web Research.

Sem exceções inseguras: TODOS os casos (inclusive "host válido") usam o módulo
real com resolver fake — nada de rede real em teste.
"""

import pytest

from app.research import ssrf
from app.research.ssrf import (
    DnsPolicyError,
    UrlPolicyError,
    assert_public_target,
    is_private_ip,
    redact_url,
    validate_url,
)


# ---------------------------------------------------------------------------
# Camada 1 — sintática
# ---------------------------------------------------------------------------
def test_validate_url_ok_http():
    assert validate_url("http://example.com/page") == "http://example.com/page"


def test_validate_url_ok_many_schemes_ports():
    assert validate_url("https://sub.example.org:8443/a?b=1") == "https://sub.example.org:8443/a?b=1"
    assert validate_url("http://example.com:8080/x") == "http://example.com:8080/x"


def test_validate_url_rejects_disallowed_schemes():
    for bad in ("ftp://x", "file:///etc/passwd", "gopher://x", "data:text/html,hi", "javascript:alert(1)"):
        with pytest.raises(UrlPolicyError):
            validate_url(bad)


def test_validate_url_rejects_credentials():
    with pytest.raises(UrlPolicyError):
        validate_url("https://user:senha@example.com/")
    with pytest.raises(UrlPolicyError):
        validate_url("https://token@example.com/")


def test_validate_url_rejects_non_standard_ports():
    with pytest.raises(UrlPolicyError):
        validate_url("http://example.com:22/")
    with pytest.raises(UrlPolicyError):
        validate_url("https://example.com:5000/")
    with pytest.raises(UrlPolicyError):
        validate_url("http://10.0.0.1:7000/")


def test_validate_url_allows_override_ports():
    assert validate_url("http://example.com:7000/", allowed_ports={80, 443, 7000})


def test_validate_url_empty_or_hostless():
    with pytest.raises(UrlPolicyError):
        validate_url("")
    with pytest.raises(UrlPolicyError):
        validate_url("https:///sem-host")


# ---------------------------------------------------------------------------
# IPs reservados/privados — is_private_ip
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.0.0.2",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "fe80::1",
        "fc00::1",
        "ff00::1",
        "64:ff9b::a00:1",
    ],
)
def test_is_private_ip_true(ip):
    assert is_private_ip(ip) is True


@pytest.mark.parametrize(
    "ip",
    ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111", "64:ff9b::8.8.8.8"],
)
def test_is_private_ip_false(ip):
    assert is_private_ip(ip) is False


# ---------------------------------------------------------------------------
# Camada 2 — DNS (resolver fake; nunca rede real)
# ---------------------------------------------------------------------------
async def _resolve(host: str) -> list[str]:
    table = {
        "example.com": ["93.184.216.34"],
        "localhost": ["127.0.0.1"],
        "interno.local": ["10.0.0.5"],
        "metadata.google.internal": ["169.254.169.254"],
        "multihost.com": ["8.8.8.8", "192.168.1.1"],
        "ipv6host.com": ["::1"],
        "unresolve.able": [],
    }
    return table.get(host, [])


@pytest.mark.asyncio
async def test_public_target_ok():
    await assert_public_target("example.com", _resolve)


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["localhost", "interno.local", "metadata.google.internal"])
async def test_public_target_private(host):
    with pytest.raises(DnsPolicyError):
        await assert_public_target(host, _resolve)


@pytest.mark.asyncio
async def test_public_target_mixed_multi():
    with pytest.raises(DnsPolicyError):
        await assert_public_target("multihost.com", _resolve)


@pytest.mark.asyncio
async def test_public_target_ipv6_loopback():
    with pytest.raises(DnsPolicyError):
        await assert_public_target("ipv6host.com", _resolve)


@pytest.mark.asyncio
async def test_public_target_unresolvable():
    with pytest.raises(DnsPolicyError):
        await assert_public_target("unresolve.able", _resolve)


@pytest.mark.asyncio
async def test_public_target_literal_ip():
    await assert_public_target("8.8.8.8", _resolve)
    with pytest.raises(DnsPolicyError):
        await assert_public_target("10.9.9.9", _resolve)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def test_redact_url():
    out = redact_url("https://user:pass@example.com:8443/p?token=abc")
    assert "user" not in out and "pass" not in out
    assert out.startswith("https://")
    assert "token=abc" in out  # query fica p/ auditoria ver o quê (sanitizada abaixo)


def test_url_host():
    assert ssrf.url_host("https://Example.COM/p") == "example.com"