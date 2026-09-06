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
    # vírgula. Nomes conhecidos: local, gemini, deterministic. Desconhecidos
    # são ignorados com aviso.
    ai_provider_order: str = "local,gemini,deterministic"
    # Fallback de segurança determinístico (sem API externa). Habilitado, o
    # JARVIS responde tarefas simples mesmo sem GEMINI_API_KEY (nunca 503 sozinho).
    ai_deterministic_enabled: bool = True

    # Local LLM (Fase 11 — extensão) — motor linguístico local generativo.
    # O provider local é o principal; Gemini é opcional; deterministic é
    # safety fallback.
    ai_local_llm_enabled: bool = True
    ai_local_llm_model_path: str = ""  # vazio → resolve em runtime
    ai_local_llm_embed_path: str = ""  # vazio → resolve em runtime
    ai_local_llm_n_ctx: int = 4096  # Fase 11.3: seguro p/ 16GB CPU-only; gerido via context budget
    ai_local_llm_n_threads: int = 6  # 5600G: 6c/12t, threads físicas
    ai_local_llm_temperature: float = 0.7

    # Fase 11.3 (#3): política LOCAL_FIRST — operações de computador reconhecidas
    # pela Intent Detection são executadas deterministicamente sem depender de
    # geração de tool call por um LLM remoto. O modelo local/remote continua
    # usado para conversação, raciocínio e operações ambíguas. Não reintroduz
    # dependência obrigatória do Gemini.
    local_first: bool = True

    # Memória / Contexto (Fase 2)
    gemini_embed_model: str = "text-embedding-004"
    memory_context_limit: int = 5  # memórias relevantes injetadas no contexto
    summarize_after_messages: int = 40  # a partir de quantas mensagens resumir
    summary_chunk: int = 20  # mensagens absorvidas por resumo rolante

    # Tool Engine (Fase 3)
    max_tool_rounds: int = 3  # rodadas máx. de chamadas de ferramenta por turno

    # Agentic Core (Fase 13) — camada de tarefas compostas (plano multi-passos).
    agent_core_enabled: bool = True
    agent_core_max_steps: int = 12  # teto de passos por plano (evita loops)
    agent_core_max_retries: int = 1  # nova tentativa por passo em falha recuperável

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

    # Remote (Fase 12.1) — transporte com a Gateway (PC é cliente WebSocket;
    # nenhuma porta de entrada é aberta no Windows). Desabilitado por padrão;
    # sem conexão em andamento quando `remote_enabled=false`.
    remote_enabled: bool = False
    remote_gateway_url: str = ""  # ex.: wss://gateway.example.com:443/ws
    remote_device_id: str = ""  # identidade do device junto à Gateway
    remote_device_token: str = ""  # segredo de autenticação (nunca em logs)
    remote_heartbeat_interval: int = 30  # s — intervalo de heartbeat
    remote_connect_timeout: float = 10.0  # s — timeout de estabelecimento
    remote_max_reconnect_delay: float = 60.0  # s — teto do backoff de reconexão

    # Remote Identity (Fase 12.2) — parâmetros de segurança do pairing/credenciais.
    # Código curto de pairing: 10 dígitos, TTL de 10 min, single-use.
    remote_pairing_code_length: int = 10
    remote_pairing_ttl_seconds: int = 600
    # Tentativas por pedido de pairing (após o limite o pedido é bloqueado).
    remote_pairing_max_attempts: int = 5
    # Teto de pairings ativos simultâneos (anti-desperdício de recursos).
    remote_pairing_max_active: int = 10
    # Entropia dos tokens de credencial (bytes passados ao secrets.token_urlsafe).
    remote_token_entropy_bytes: int = 32
    # Máximo de credenciais ativas por device (rotacionar em vez de acumular).
    remote_max_active_credentials: int = 10
    # Gate global de submissões de código ("token bucket", por processo):
    # capacidade de rajada e refill por segundo. Não depende de IP.
    remote_pairing_gate_capacity: float = 5.0
    remote_pairing_gate_refill: float = 0.5

    # Remote Agent (Fase 12.3) — conexão persistente, comandos e idempotência.
    # Idempotência: TTL (s) dos registros de comandos e payload máximo (bytes).
    remote_command_ttl_seconds: int = 3600  # 1h — comandos/approvals velhos expiram
    remote_command_max_payload: int = 16 * 1024  # 16 KiB — teto do payload
    # Reconexão: backoff inicial (s), fator multiplicador e jitter máximo (fração).
    remote_reconnect_min_delay: float = 0.5  # s — primeiro backoff
    remote_reconnect_max_delay: float = 60.0  # s — teto do backoff (também configurável acima)
    remote_reconnect_jitter: float = 0.1  # fração do delay usada como jitter

    # Wake-on-LAN (Fase 12.6) — tool `wake_on_lan` que acorda uma máquina na LAN
    # enviando o magic packet UDP. `wol_enabled` liga/desliga a tool; o resto são
    # defaults (endereço de broadcast e porta) sobrescrevíveis por chamada.
    wol_enabled: bool = True
    wol_default_broadcast: str = "255.255.255.255"
    wol_default_port: int = 9

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