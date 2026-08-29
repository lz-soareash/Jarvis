import json
import logging
import logging.config
from datetime import datetime, timezone

from app.core.config import settings

# Campos internos do logging que não devem virar campos separados no JSON.
_SKIPPED_KEYS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message",
}


class JsonFormatter(logging.Formatter):
    """Formata cada linha de log como JSON estruturado (UTC)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            key = str(key)
            if key not in _SKIPPED_KEYS:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def configure_logging(level: str | None = None) -> None:
    """Configura logging estruturado. Idempotente (evita handlers duplicados)."""
    global _configured
    if _configured:
        return

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": "app.core.logging.JsonFormatter"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"}},
        "root": {"level": "INFO", "handlers": ["console"]},
        "loggers": {
            "uvicorn": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "uvicorn.access": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "uvicorn.error": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "sqlalchemy.engine": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        },
    }
    logging.config.dictConfig(config)
    _configured = True

    logger = logging.getLogger("jarvis")
    logger.debug("Logging estruturado configurado (level=%s)", level or settings.log_level)