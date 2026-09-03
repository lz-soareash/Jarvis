"""Criptografia de identidade remota (Fase 12.2).

Princípios:
- Tokens de credencial são gerados com alta entropia (`secrets`) e NUNCA
  armazenados em texto claro — apenas a derivação (`sha256:<hex>`) é gravada.
- O código curto de pairing também é armazenado apenas na forma derivada.
- A comparação usa `hmac.compare_digest` (imune a timing) sobre a derivação
  normalizada, nunca diretamente sobre o segredo.

Modelo de ameaça documentado: um vazamento do banco de dados não revela
tokens/códigos (apenas hashes de valores de alta entropia, sem KDF necessário —
não são senhas de usuário escolhidas). SHA-256 de 256 bits de entropia não é
brute-forçável; MD5/SHA-1 são proibidos.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.config import settings

# Precisão da derivação: "sha256:" + 64 hex.
PREFIX = "sha256:"


def derive_secret(secret: str) -> str:
    """Deriva um segredo para persistência (nunca armazene o valor bruto)."""
    return f"{PREFIX}{hashlib.sha256(secret.encode('utf-8')).hexdigest()}"


def verify_secret(secret: str, stored: str) -> bool:
    """Comparação em tempo constante entre segredo bruto e valor derivado."""
    if not secret or not stored:
        return False
    derived = derive_secret(secret)
    if not derived.startswith(PREFIX):
        return False
    return hmac.compare_digest(derived, stored)


def generate_token() -> str:
    """Token de credencial criptograficamente aleatório (URL-safe)."""
    return secrets.token_urlsafe(settings.remote_token_entropy_bytes)


def generate_pairing_code(length: int | None = None) -> str:
    """Código curto numérico uniforme — cada dígito via `secrets` (uniforme)."""
    size = length or settings.remote_pairing_code_length
    if size < 1:
        raise ValueError("comprimento de pairing code deve ser >= 1")
    return "".join(str(secrets.randbelow(10)) for _ in range(size))