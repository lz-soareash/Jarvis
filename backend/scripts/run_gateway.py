"""Runner standalone do Gateway WAN (Fase 23).

Uso (da raiz do repositório):

    python backend/scripts/run_gateway.py

Lê variáveis `GATEWAY_*` (env ou `.env` na raiz). Ex.: GATEWAY_PEER_TOKEN,
GATEWAY_HOST, GATEWAY_PORT. Nunca expõe o token em logs.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    from app.core.logging import configure_logging  # mesmo pipeline de logs do Core

    from app.gateway.config import get_settings

    configure_logging()
    settings = get_settings()

    import uvicorn

    uvicorn.run(
        "app.gateway.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()