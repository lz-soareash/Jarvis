"""Persistência best-effort de preferências do Core Link WAN (Fase 23).

Apenas o override de URL de runtime é persistido (em `data/gateway.runtime.json`).
Identidade (`remote_device_id`) e `remote_gateway_peer_token` NUNCA são
gravados aqui — vêm do `.env` e podem ser revogados pela UI.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.core.config import REPO_ROOT

_PREFS_PATH = REPO_ROOT / "data" / "gateway.runtime.json"
_LOCK = threading.Lock()


class GatewayPrefs:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _PREFS_PATH

    def url(self) -> str | None:
        data = self._load()
        raw = data.get("url") if isinstance(data, dict) else None
        return raw if isinstance(raw, str) and raw.strip() else None

    def save(self, url: str) -> None:
        with _LOCK:
            try:
                self._path.write_text(
                    json.dumps({"url": url}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass  # persistência é best-effort (lançar nunca)

    def clear(self) -> None:
        with _LOCK:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass

    def _load(self) -> dict:
        with _LOCK:
            try:
                if not self._path.exists():
                    return {}
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                return {}