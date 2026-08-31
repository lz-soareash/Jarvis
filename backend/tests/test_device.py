"""Fase 9 — Mobile/Devices/PWA: detecção de dispositivo e endpoints."""

from app.core.enums import DeviceType
from app.services.device import detect_device, detect_touch

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Mobile Safari/537.36"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 "
    "Safari/604.1"
)
IPAD_UA = (
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
CURL_UA = "curl/8.5.0"


def test_detect_device_desktop(client):
    assert detect_device(DESKTOP_UA) is DeviceType.DESKTOP
    assert detect_device(MAC_UA) is DeviceType.DESKTOP


def test_detect_device_mobile(client):
    assert detect_device(MOBILE_UA) is DeviceType.MOBILE
    assert detect_device(IPHONE_UA) is DeviceType.MOBILE


def test_detect_device_tablet_is_mobile(client):
    assert detect_device(IPAD_UA) is DeviceType.MOBILE


def test_detect_device_web_for_bots_and_cli(client):
    assert detect_device(BOT_UA) is DeviceType.WEB
    assert detect_device(CURL_UA) is DeviceType.WEB


def test_detect_device_empty_is_web(client):
    assert detect_device(None) is DeviceType.WEB
    assert detect_device("") is DeviceType.WEB


def test_detect_touch(client):
    assert detect_touch(MOBILE_UA) is True
    assert detect_touch(IPAD_UA) is True
    assert detect_touch(DESKTOP_UA) is False


def test_device_info_endpoint_mobile(client):
    response = client.get("/api/device/info", headers={"User-Agent": MOBILE_UA})
    assert response.status_code == 200
    data = response.json()
    assert data["device"] == "mobile"
    assert data["touch"] is True


def test_device_info_endpoint_desktop(client):
    response = client.get("/api/device/info", headers={"User-Agent": DESKTOP_UA})
    assert response.status_code == 200
    data = response.json()
    assert data["device"] == "desktop"
    assert data["touch"] is False


def test_health_reports_detected_device(client):
    mobile = client.get("/health", headers={"User-Agent": MOBILE_UA}).json()
    assert mobile["device"] == "mobile"
    desktop = client.get("/health", headers={"User-Agent": DESKTOP_UA}).json()
    assert desktop["device"] == "desktop"


def test_serves_pwa_icon_pngs(client):
    assert client.get("/icons/icon-192.png").status_code == 200
    assert client.get("/icons/icon-512.png").status_code == 200
    assert client.get("/icons/icon-maskable-512.png").status_code == 200
    assert client.get("/icons/apple-touch-icon.png").status_code == 200
