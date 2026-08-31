"""Detecção de dispositivo (Fase 9 — Mobile/Devices/PWA).

Normaliza o header User-Agent em um DeviceType (desktop/mobile/tablet/web) mais
metadados leves (browser, OS, é tela sensível a toque), sem fingerprint nem
persistência — suficiente para a UI ajustar a experiência por classe de device.
"""

import logging
import re

from app.core.enums import DeviceType

logger = logging.getLogger("jarvis.device")

# Ordem importa: mobile/tablet antes de desktop.
_MOBILE_TOKEN = re.compile(
    r"Mobile|Android|iPhone|iPod|BlackBerry|Windows Phone|webOS|"
    r"Opera Mini|IEMobile",  # trapézio para não pegar iPad-desktop do iOS 13+
    re.IGNORECASE,
)
_TABLET_TOKEN = re.compile(
    r"iPad|Tablet|Kindle|Silk|PlayBook|Nexus (7|9|10)(?!.*Mobile)|"
    r"Android(?!.*Mobile)",  # Android sem Mobile => tablet
    re.IGNORECASE,
)
_BOTS = re.compile(
    r"bot|spider|crawl|curl|wget|python-requests|http-client|postman|"
    r"googlebot|bingbot|duckduckbot|telegrambot|slackbot",  # rastreadores e clientes CLI
    re.IGNORECASE,
)


def detect_device(user_agent: str | None) -> DeviceType:
    """Classifica um User-Agent em um DeviceType.

    Regras, nesta ordem:
      1. UA ausente/vazio -> WEB (indefinido/cliente genérico).
      2. Rastreador/CLI -> WEB (não é tela humana, sem UX mobile).
      3. Tablet -> MOBILE (mesma UX responsiva que celular).
      4. Mobile -> MOBILE.
      5. Caso contrário -> DESKTOP.
    """
    ua = (user_agent or "").strip()
    if not ua:
        return DeviceType.WEB
    if _BOTS.search(ua):
        return DeviceType.WEB
    if _TABLET_TOKEN.search(ua) or _MOBILE_TOKEN.search(ua):
        return DeviceType.MOBILE
    return DeviceType.DESKTOP


def detect_touch(user_agent: str | None) -> bool:
    """Estima se a UI deveria assumir toque como entrada primária."""
    ua = (user_agent or "").lower()
    return any(
        k in ua
        for k in (
            "mobile",
            "android",
            "iphone",
            "ipad",
            "ipod",
            "touch",
            "tablet",
            "windows phone",
        )
    )
