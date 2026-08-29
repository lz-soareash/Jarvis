from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Configuração central do JARVIS Core (lida de variáveis de ambiente / .env)."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "JARVIS"
    version: str = "0.1.0"
    env: str = "development"

    # Servidor
    host: str = "127.0.0.1"
    port: int = 8100
    ws_port: int = 8101

    # IA (Google Gemini) — segredo mantido fora de logs/repr.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"

    # Persistência — Local-first (SQLite); Postgres é troca futura de URL.
    database_url: str = "sqlite:///./data/jarvis.db"

    log_level: str = "INFO"


settings = Settings()