"""Proteção SSRF (Fase 14 — section 12, 14, 18).

Nenhum fetch de URL arbitrária do usuário/web poderá acessar a rede interna.
Aplicada em TRÊS camadas, todas obrigatórias (sem exceções inseguras — nem
nos testes; lá a resolução de DNS é sempre fake):

1. `validate_url` — restrição sintática: só http/https, sem credenciais
   embutidas (`user@host`), portas fora de uma allow-list rejeitadas.
2. `assert_public_target` — resolução DNS: TODOS os IPs de um hostname devem
   ser públicos (bloqueia localhost, RFC1918, link-local, CGNAT, multicast,
   reservado, IPv6-mapped/metadata 169.254.169.254 etc).
3. Re-validação por hop — every redirect é re-checado pelo Fetcher antes do
   próximo GET (mitiga DNS rebinding entre request e follow).
"""

import ipaddress
import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger("jarvis.research.ssrf")

# Única resposta aceita: http/https, portas convencionais (waivable só via param).
ALLOWED_SCHEMES = ("http", "https")
DEFAULT_ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})

_CREDENTIALS_RE = re.compile(r"^[^/@]+@")

_METADATA_HOSTS = (
    "metadata.google.internal",
    "metadata.aws.internal",
    "169.254.169.254",
)


class SsrfError(ValueError):
    """URL bloqueada pela política SSRF (mensagem oferece contexto)."""


class UrlPolicyError(SsrfError):
    """Falha de política sintática (scheme, credenciais, porta)."""


class DnsPolicyError(SsrfError):
    """O hostname resolveu para endereço não-público (bloqueado)."""


# ---------------------------------------------------------------------------
# Imports seguros de IP
# ---------------------------------------------------------------------------
def _ipv4_blocked(addr: ipaddress.IPv4Address) -> bool:
    if addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return True
    if addr.is_private or addr.is_reserved or addr.is_unspecified:
        return True
    # Ranges fora
    if addr in ipaddress.ip_network("0.0.0.0/8"):  # "esta rede" antiga
        return True
    if addr in ipaddress.ip_network("100.64.0.0/10"):  # CGNAT (shared space)
        return True
    if addr in ipaddress.ip_network("198.18.0.0/15"):  # benchmark/reserva
        return True
    return False


def _ipv6_blocked(addr: ipaddress.IPv6Address) -> bool:
    if addr.ipv4_mapped is not None:
        return _ipv4_blocked(addr.ipv4_mapped)
    # NAT64 64:ff9b::/96 — embute IPv4 (prefixo reservado, mas o tráfego
    # alcança o IPv4 embutido: precisa ser re-avaliado antes do `is_reserved`).
    if addr in ipaddress.ip_network("64:ff9b::/96"):
        embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        return _ipv4_blocked(embedded)
    if addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return True
    if addr.is_private or addr.is_reserved or addr.is_unspecified:
        return True
    return False


def is_private_ip(ip_str: str) -> bool:
    """True se um literal de IP é privado/reservado/interno (SSRF)."""
    try:
        addr = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return True  # não-parseável é tratado como suspeito
    return _ipv6_blocked(addr) if addr.version == 6 else _ipv4_blocked(addr)


# ---------------------------------------------------------------------------
# Camada 1 — política sintática
# ---------------------------------------------------------------------------
def validate_url(
    url: str,
    *,
    allowed_schemes: tuple[str, ...] = ALLOWED_SCHEMES,
    allowed_ports: frozenset[int] | set[int] | None = DEFAULT_ALLOWED_PORTS,
    timeout: float | None = None,
) -> str:
    """Valida sintaxe/porta de uma URL e devolve-a normalizada (sem DNS).

    Levanta `UrlPolicyError` em vetores clássicos de SSRF. A checagem DNS é
    separada (`assert_public_target`).
    """
    if not url or not url.strip():
        raise UrlPolicyError("URL vazia.")
    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    if scheme not in allowed_schemes:
        raise UrlPolicyError(f"Scheme '{scheme or '<vazio>'}' não permitido ({'/'.join(allowed_schemes)}).")
    host = parsed.hostname
    if not host:
        raise UrlPolicyError("URL sem hostname.")
    if _CREDENTIALS_RE.match(host or "") or parsed.username is not None or parsed.password is not None:
        raise UrlPolicyError("URL com credenciais embutidas não é permitida.")
    if parsed.port is not None:
        if allowed_ports is not None and parsed.port not in allowed_ports:
            raise UrlPolicyError(f"Porta {parsed.port} não permitida (allow-list SSRF).")
    elif allowed_ports is not None:
        default_port = 443 if scheme == "https" else 80
        if default_port not in allowed_ports:
            raise UrlPolicyError(f"Default '{scheme}' não está na allow-list de portas.")
    return cleaned


# ---------------------------------------------------------------------------
# Camada 2 — resolução DNS contra IPs internos
# ---------------------------------------------------------------------------
async def assert_public_target(
    host: str,
    resolver=None,
    *,
    allowed_ports: frozenset[int] | set[int] | None = DEFAULT_ALLOWED_PORTS,
) -> None:
    """Resolve `host` e garante que todos os IPs são públicos.

    `resolver` é um callable injetável `async (host) -> list[str]` (tests usam
    fakes; o default usa `loop.getaddrinfo`). Levanta `DnsPolicyError`.
    """
    host_lower = (host or "").strip().lower().rstrip(".")
    if not host_lower:
        raise DnsPolicyError("Hostname vazio.")
    if host_lower in _METADATA_HOSTS:
        raise DnsPolicyError(f"Hostname de metadados de nuvem bloqueado ({host}).")
    if allowed_ports is not None and len(allowed_ports) == 0:
        raise DnsPolicyError("Allow-list de portas vazia.")
    # Host é um literal IP? Checa direto.
    try:
        addr = ipaddress.ip_address(host_lower)
        if _ipv6_blocked(addr) if addr.version == 6 else _ipv4_blocked(addr):
            raise DnsPolicyError(f"Endereço interno/reservado bloqueado ({host}).")
        return
    except ValueError as exc:
        if isinstance(exc, DnsPolicyError):
            raise
    # Senão, resolve.
    ips = await _resolve_host(host_lower, resolver)
    if not ips:
        raise DnsPolicyError(f"Não foi possível resolver '{host}'.")
    blocked = [ip for ip in ips if is_private_ip(ip)]
    if blocked:
        raise DnsPolicyError(f"'%s' resolve para endereço interno/reservado (%s) — SSRF." % (host, ", ".join(sorted(blocked))))


async def _resolve_host(host: str, resolver) -> list[str]:
    import asyncio

    if resolver is not None:
        return await resolver(host)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=0)
    except OSError:
        return []
    ips: list[str] = []
    for _, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        if ip not in ips:
            ips.append(ip)
    return ips


# ---------------------------------------------------------------------------
# Utilidades partilhadas com auditoria
# ---------------------------------------------------------------------------
def redact_url(url: str) -> str:
    """Remove credenciais/query sensível de uma URL p/ logs/auditoria."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        netloc = host
        if parsed.port:
            netloc = f"{host}:{parsed.port}"
        return parsed._replace(netloc=netloc).geturl()
    except Exception:  # noqa: BLE001 — nunca quebra o caminho de auditoria
        return "<url-inválida>"


def url_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""