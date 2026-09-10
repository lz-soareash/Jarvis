from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


def _default_version() -> str:
    """Fase 22 — versão central: o arquivo `VERSION` na raiz é a ÚNICA fonte.

    O backend não mantém versão própria: lê `VERSION` do repositório (fallback
    conservador para instalações sem o arquivo). Assim backend, Desktop, Android
    e Releases permanecem sincronizados (Fase 22, versionamento central).
    """
    try:
        version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        if version:
            return version
    except OSError:
        pass
    return "0.22.0"


class Settings(BaseSettings):
    """Configuração central do JARVIS Core (lida de variáveis de ambiente / .env)."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "JARVIS"
    version: str = _default_version()
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

    # Perception Layer (Fase 15) — PERCEPÇÃO segura/determinística do computador.
    # `perception_enabled` liga/desliga a camada de percepção; `perception_screenshot_enabled`
    # habilita a captura OPCIONAL e MANUAL de screenshot (nunca contínua, nunca automática;
    # exige bibliotecas opcionais; binário nunca entra em logs/eventos).
    perception_enabled: bool = True
    perception_screenshot_enabled: bool = True
    # Teto de processos incluídos numa observação (evita payloads grandes).
    perception_max_processes: int = 30

    # Filesystem (Fase 7) — raiz-sandbox das ferramentas de arquivos
    files_root: str = "~"

    # Web Research (Fase 14) — busca, coleta e síntese independentes do Gemini.    # A coleta NUNCA depende de LLM/API externa (local-first); o LLM só entra na
    # síntese (Router: local → gemini → determinístico).
    web_search_enabled: bool = True  # gate global da camada de busca
    web_search_provider: str = "duckduckgo"  # provider default (provider registry)
    web_search_timeout: float = 10.0  # s — timeout de uma consulta de busca
    web_search_max_results: int = 8  # resultados por consulta devolvidos (teto)
    web_search_retries: int = 1  # nova tentativa por consulta em falha recuperável
    web_fetch_timeout: float = 10.0  # s — timeout de um fetch
    web_fetch_max_bytes: int = 512 * 1024  # 512 KiB — teto de corpo baixado
    web_fetch_max_redirects: int = 3  # teto de redirects (cada hop é revalidado)
    web_fetch_user_agent: str = "JarvisBot/0.1 (local-first research; contact: localhost)"
    web_research_max_queries: int = 3  # teto de consultas por pesquisa
    web_research_max_pages: int = 4  # teto de páginas coletadas por pesquisa
    web_research_max_total_bytes: int = 2 * 1024 * 1024  # 2 MiB — bytes totais
    web_research_max_duration: float = 40.0  # s — teto de duração da pesquisa
    web_research_min_evidence: int = 2  # evidências p/ considerar suficiente
    web_research_synthesis_tokens: int = 400  # teto de tokens da síntese
    # Nível de risco padrão das tools web (web_search/web_fetch). Via Permission
    # Engine, com override persistente por ferramenta.
    web_tools_status: str = "enabled"

    # Atlas (Fase 10) — camada externa de inteligência (Django separado).
    atlas_enabled: bool = False
    atlas_base_url: str = "http://127.0.0.1:8000"
    atlas_email: str = ""
    atlas_password: str = ""
    atlas_timeout: float = 30.0  # s — timeout de conexão/leitura com o Atlas
    # Fase 14 — política de escrita de conhecimento (AtlasWriteMode).
    # Default conservador: sugerir (nunca escrever sem visibilidade).
    atlas_write_mode: str = "suggest"

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

    # Remote Gateway WAN (Fase 23) — core link outbound para o relé deployável
    # (`backend/app/gateway/`). Gate mestre: quando `remote_gateway_enabled=false`
    # (padrão), NENHUM worker/conexão de WAN é criado. O Core continua sendo a
    # ÚNICA autoridade (identidade/permissões/IA); o relé só transporta.
    remote_gateway_enabled: bool = False
    # Segredo compartilhado com o relé que o identifica como core legítimo no
    # handshake `hello` (mesmo valor de `GATEWAY_PEER_TOKEN` no deploy do relé).
    # Nunca entregue a mobiles; nunca em logs.
    remote_gateway_peer_token: str = ""

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

    # Remote Layer (Fase 16) — fronteira de acesso controlada ao agente local.
    # Sessões: TTL de atividade em segundos (0 = sem expiração). Sessões com
    # `last_seen_at` além do TTL são expiradas (ENDED) pela limpeza automática.
    remote_session_ttl_seconds: int = 3600  # 1h
    # Autorização do transporte HTTP remoto: os mesmos níveis L0-L3 do Permission
    # Engine se aplicam — sessões remotas nunca burlam confirmações/auditoria.
    # Payload máximo (bytes) de requisições HTTP a `/api/remote/*` (413 se maior).
    remote_max_payload_bytes: int = 256 * 1024  # 256 KiB
    # Rate limiting de autenticação (token bucket, compartilhado por processo):
    # capacidade de rajada e refill por segundo, por origem (IP) + um bucket global.
    remote_auth_rate_capacity: float = 10.0
    remote_auth_rate_refill: float = 0.5
    # Rate limiting de mensagens, POR DEVICE autenticado (token bucket).
    remote_message_rate_capacity: float = 20.0
    remote_message_rate_refill: float = 1.0
    # Máximo de mensagens remotas em processamento simultâneo (semáforo process-local).
    remote_max_concurrent_messages: int = 4
    # CORS do acesso remoto: lista separada por vírgula. VAZIO por padrão =
    # restritivo (nenhum origin liberado; browsers cross-origin são bloqueados).
    # Nunca use "*". Ex.: "https://app.meudominio.com,http://127.0.0.1:4200"
    remote_cors_origins: str = ""

    # Device Bridge (Fase 21) — camada de identidade de clientes finos (Desktop/
    # Mobile) sobre o Remote Gateway. `device_enabled` liga/desliga os endpoints
    # de identidade/estado (registro, capabilities, heartbeat, rename, info);
    # a SEGURANÇA e o transporte das mensagens continuam governados por
    # `remote_enabled` (gate mestre: REMOTE_ENABLED=false por padrão). Não afeta
    # permissões (o cliente nunca escolhe nível/autonomia/tools).
    device_enabled: bool = True
    # Teto de devices confiáveis simultâneos (ACTIVE/PAIRED). Ao atingir, o
    # pareamento de novos devices é recusado (impede acumulação desordenada).
    device_max_devices: int = 20
    # Intervalo de heartbeat sugerido aos clientes (s). Também usado como
    # janela de "staleness" em telemetria (device sem batida nesse intervalo).
    device_heartbeat_seconds: int = 30
    # Habilita o fluxo de reconexão dos clientes (cabe ao cliente respeitar;
    # aqui controla a telemetria/estado derivado e metadados de transport).
    device_reconnect_enabled: bool = True
    # Teto de devices PENDING não pareados (registros órfãos não se acumulam).
    device_max_pending: int = 50

    # Proactive Agent (Fase 17) — infraestrutura de proatividade controlada.
    # Tudo começa DESLIGADO (conservador): ligue explicitamente via env/.env.
    # Capacidade geral: é o que permite ao JARVIS agir por iniciativa própria,
    # SEM nunca derrubar o fluxo conversacional nem ignorar limites do usuário.
    proactive_enabled: bool = False
    # Scheduler persistente (one-shot/interval/cron-lite) — liga os triggers
    # agendados no banco. Mesmo desligado, a API de schedules responde.
    proactive_scheduler_enabled: bool = False
    # Tetos de notificações proativas (defaults conservadores).
    proactive_max_per_hour: int = 12
    proactive_max_per_day: int = 48
    # Cooldown mínimo entre notificações do MESMO tipo de evento (s).
    proactive_default_cooldown_seconds: int = 600
    # Janela de silêncio "HH:MM-HH:MM" (cruzando meia-noite é permitido).
    # Em quiet hours, LOW/NORMAL/HIGH são adiados (DEFER); CRITICAL só interrompe
    # se `proactive_interrupt_on_critical` for explicitamente True.
    proactive_quiet_hours: str = "22:00-07:00"
    # Deslocamento de fuso local usado para avaliar quiet hours (minutos, UTC+).
    proactive_timezone_offset_minutes: int = -180  # BRT padrão
    proactive_interrupt_on_critical: bool = False
    # Ciclo do worker proativo (respeita o FastAPI lifespan; sem thread infinita).
    proactive_tick_seconds: int = 30
    # TTL de mensagens proativas pendentes (s) antes de serem expiradas.
    proactive_message_ttl_seconds: int = 86400  # 24h

    # Wake-on-LAN (Fase 12.6) — tool `wake_on_lan` que acorda uma máquina na LAN
    # enviando o magic packet UDP. `wol_enabled` liga/desliga a tool; o resto são
    # defaults (endereço de broadcast e porta) sobrescrevíveis por chamada.
    wol_enabled: bool = True
    wol_default_broadcast: str = "255.255.255.255"
    wol_default_port: int = 9

    # Computer Action Layer (Fase 18) — ações controladas de mouse/teclado.
    # DESLIGADO por padrão: a Fase 18 nunca ativa controle de mouse/teclado
    # silenciosamente em instalações existentes. `dry_run` também funciona
    # desligado NÃO executa física — mas aqui a camada inteira fica inativa.
    computer_actions_enabled: bool = False
    # Tempo máximo (s) de execução física de uma ação antes de TIME-OUT.
    computer_action_timeout_seconds: float = 10.0
    # Limites por tipo (validator): dimensões seguras de ação.
    computer_action_max_type_text_chars: int = 500
    computer_action_max_scroll_amount: int = 10
    computer_action_max_hotkey_keys: int = 3
    # Rate limit: token bucket global para TODAS as ações (rajada/dreno).
    computer_action_rate_capacity: float = 20.0
    computer_action_rate_refill_per_second: float = 2.0
    # Tetos adicionais de segurança (por minuto, extra ao bucket).
    computer_action_max_actions_per_minute: int = 60
    # Combinações de teclas permanentemente bloqueadas (formato: "A+B+C").
    # Defaults mapeados em app/action/safety.py: CTRL+ALT+DEL, CTRL+SHIFT+ESC,
    # ALT+F4, CTRL+ALT+F2. Estes aqui são ADICIONAIS à lista padrão.
    computer_action_blocked_hotkeys: list[str] = []

    # Computer Agent (Fase 19) — Computer Use Agent sobre Perception + Action.
    # DESLIGADO por padrão: o agente de computador NUNCA atua silenciosamente.
    # Enquanto falso, nenhuma tarefa de Computer Use inicia (API retorna 503).
    computer_agent_enabled: bool = False
    # Nível de autonomia do agente (C0 observe-only ... C4 ações críticas com
    # autorização explícita). O LLM/agente NUNCA pode alterar o próprio nível.
    computer_agent_autonomy: str = "C1"
    # Limites rígidos do loop (evita loops indefinidos e rajadas).
    computer_agent_max_steps: int = 12
    computer_agent_max_actions: int = 24
    computer_agent_max_retries: int = 2
    computer_agent_timeout_seconds: float = 180.0
    # Exige confirmação (approval existente) para ações nível 2 durante o loop.
    computer_agent_require_confirmation_l2: bool = True
    # Teto de tentativas de planificação reprovadas pela verificação antes de FAIL.
    computer_agent_max_plan_retries: int = 3

    # Identity / Personality (Fase 19) — identidade do assistente separada do
    # provider de IA. `assistant_name` é o NOME DE EXIBIÇÃO (VEGA); o nome
    # técnico do projeto/repositório permanece JARVIS.
    assistant_name: str = "VEGA"
    assistant_tone: str = "professional"
    assistant_verbosity: str = "adaptive"
    assistant_proactivity: str = "controlled"
    assistant_humor: str = "subtle"
    assistant_formality: str = "adaptive"
    # Fase 19.5 — presença da VEGA (estados reais derivados; badge/frontend).
    # Observabilidade de UI => ligada por padrão; NUNCA afeta permissões/segurança.
    vega_presence_enabled: bool = True

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