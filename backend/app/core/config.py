from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


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
    ai_max_retries: int = 3  # tentativas extra em 429/5xx (respeita o retryDelay da API)

    # Persistência — Local-first (SQLite); Postgres é troca futura de URL.
    database_url: str = "sqlite:///./data/jarvis.db"

    # Chat — janela de contexto enviada ao modelo por resposta
    max_context_messages: int = 20

    # AI Core / AI Router (Fase 11) — seleção de provedores e fallback.
    # Ordem de prioridade dos provedores (primeiro = preferido), separada por
    # vírgula. Nomes conhecidos: gemini, deterministic. Desconhecidos são
    # ignorados com aviso.
    ai_provider_order: str = "gemini,deterministic"
    # Fallback de segurança determinístico (sem API externa). Habilitado, o
    # JARVIS responde tarefas simples mesmo sem GEMINI_API_KEY (nunca 503 sozinho).
    ai_deterministic_enabled: bool = True

    # Memória / Contexto (Fase 2)
    gemini_embed_model: str = "text-embedding-004"
    memory_context_limit: int = 5  # memórias relevantes injetadas no contexto
    summarize_after_messages: int = 40  # a partir de quantas mensagens resumir
    summary_chunk: int = 20  # mensagens absorvidas por resumo rolante

    # Tool Engine (Fase 3)
    max_tool_rounds: int = 3  # rodadas máx. de chamadas de ferramenta por turno

    # Permissions (Fase 4)
    approval_ttl_seconds: int = 600  # validade de um pedido de aprovação pendente

    # Filesystem (Fase 7) — raiz-sandbox das ferramentas de arquivos
    files_root: str = "~"

    # Atlas (Fase 10) — camada externa de inteligência (Django separado).
    atlas_enabled: bool = False
    atlas_base_url: str = "http://127.0.0.1:8000"
    atlas_email: str = ""
    atlas_password: str = ""
    atlas_timeout: float = 30.0  # s — timeout de conexão/leitura com o Atlas

    # Voz & Fala (Fase 6b) — configuração centralizada do TTS
    # Provedor ativo (edge por padrão; outros via tts_providers).
    tts_provider: str = "edge"
    # Voz equivalente ao perfil "Antonio" (pt-BR masculino, natural).
    tts_voice: str = "pt-BR-AntonioNeural"
    tts_language: str = "pt-BR"
    # Velocidade (taxa) em %; leve redução para tom calmo (padrão -5%).
    tts_rate: str = "-5%"
    # Tom (pitch) em Hz; levemente grave.
    tts_pitch: str = "-2Hz"
    # Volume em % (0 a +100). 100% = sem alteração relativa.
    tts_volume: str = "+0%"
    # Tempo limite (s) de conexão/leitura de um provedor externo.
    tts_timeout: int = 20
    # Cache de áudio: tamanho máximo de entradas em memória (0 desativa).
    tts_cache_size: int = 128
    # Fallback: se o provider principal falhar, tenta novamente N vezes.
    tts_fallback_attempts: int = 1

    log_level: str = "INFO"


settings = Settings()