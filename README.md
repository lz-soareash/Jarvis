# JARVIS — Personal AI Agent

Assistente pessoal inteligente, **local-first**, modular, seguro, escalável e preparado
para múltiplos dispositivos e um futuro modo remoto.

> **JARVIS = "O que posso fazer?"** (agente/execução)
>
> **VEGA = quem ele é** (nome de exibição da identidade — técnico: JARVIS)
>
> **ATLAS = "O que eu sei?"** (conhecimento — integração futura, **opcional**, não é dependência da VEGA)

## Regras fundamentais

- IA **padrão**: LLM local generativo (Qwen2.5 via llama.cpp); **Gemini (Google) é
  opcional** e entra como fallback se configurado; **fallback determinístico local** por
  último (Fase 11 — independência total de internet / chave de API).
- `GEMINI_API_KEY` nunca sai do backend (nunca no frontend/mobile/agente).
- A IA **não executa** nada: ela apenas *sugere* `tool + params`. A execução passa
  obrigatoriamente pelo **Tool Engine** e pelo **Permission System**.
- O **Core** executa as ferramentas **pelo pipeline interno** (Tool Engine → Permission
  System → Auditoria), sempre com operações determinísticas e controladas. O **Agentic Core**
  (Fase 13) executa planos de tarefa reutilizando exatamente esse pipeline, respeitando os
  níveis de permissão e a aprovação do usuário.
- O JARVIS funciona sem o Atlas; Atlas é integração opcional (via API real, sem tocar no banco).

## Arquitetura (Fase 0)

```
                    USUÁRIO (Desktop / Mobile / PWA)
                                │  REST + WebSocket (futuro)
                                ▼
                         JARVIS CORE (FastAPI)          porta 8100 / WS 8101
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
  AI Orchestrator        Context Engine          Memory (SQLite)
  (Gemini via AIProvider)   (fases futuras)           (fases futuras)
                                │
                           ONION: IA → Tool Engine → Permission System → User Confirmation
                                 │
                       Perception Layer (Fase 15) ← ComputerState (bounded, em memória)
                                 │
                           LOCAL AGENT (processo separado)
```

O `AIProvider` é uma interface abstrata (`generate`, `stream`, `analyze`, `health_check`).
Implementações: `GeminiProvider`, `LocalLLMProvider` (local, via llama.cpp) e
`DeterministicProvider` (safety). O resto do sistema nunca depende do SDK.

## Stack

| Camada | Tecnologia |
|---|---|
| Core | Python 3.13, FastAPI, Uvicorn |
| Schemas | Pydantic v2 + pydantic-settings |
| Persistência | SQLAlchemy 2.x + SQLite (migrável para PostgreSQL trocando a URL) |
| IA | google-genai (Gemini) |
| Frontend | HTML + CSS + JS Vanilla (PWA: manifest + service worker) + Web Speech API (voz local) |
| Desktop (Fase 21) | Electron 31 (Node 24) — cliente fino `VEGA.exe` (distribuições Windows Fase 22) |
| Mobile (Fase 21/25) | Kotlin 1.9 + Jetpack Compose + AGP 8.4 — cliente fino com chat em voz/texto (Fase 25) `app-release.apk` assinado (Fase 22) |
| Testes | pytest (+ pytest-asyncio, TestClient), node:test (bridge desktop) |

Sem Docker/Redis/Celery nesta fase (não há necessidade real ainda).

## Estrutura

```
frontend/                  # interface concha: HTML + CSS + JS Vanilla + PWA
└── (index.html, sw.js, manifest.webmanifest, css/, js/, icons/)
    # js/ops.js + css/ops.css: Central de Operações (aba do SPA) — Fase 11
desktop/                   # VEGA Desktop — cliente fino Electron (bridge.js testável) → VEGA.exe
android/                   # VEGA Mobile — chat em voz/texto (MainActivity + ChatViewModel + ui/) → APK
backend/
├── app/
│   ├── ai/
│   │   ├── core.py                # AI Core (Fase 11) — orquestra provedor + caminho + observabilidade
│   │   ├── registry.py            # AI Router (Fase 11) — seleção/fallback de provedores
│   │   └── providers/             # base.py (AIProvider) + gemini.py + local_llm.py + deterministic.py (safety)
│   ├── api/                       # rotas (health, chat, memory, approvals, permissions, audit, system, ops, workspace)
│   ├── computer/                  # SystemController (stats, processos, abrir/encerrar apps) — Fase 5
│   ├── core/                      # config, logging estruturado, enums (estados/permissões)
│   ├── db/                        # SQLAlchemy (Base, engine, sessão)
│   ├── models/                    # Session / Message / Memory / ExecutionEvent / governança (SQLAlchemy 2.x)
│   ├── remote/                    # Fase 12.1..12.7 — transporte Gateway + identidade + comandos + eventos
│   │   ├── protocol.py, connection.py, manager.py, gateway.py, runtime.py   # 12.1
│   │   ├── crypto/devices/credentials/sessions/pairing/auth/registrar/identity_events  # 12.2
│   │   ├── agent/executor/remote_commands/jarvis_session/status            # 12.3
│   │   ├── resume                                                           # 12.4
│   │   ├── events.py            # 12.5 — barramento pub/sub p/ SSE + replay sanitizado
│   │   ├── outbox.py            # 12.7 — fila persistente de re-entrega de resultados
│   │   ├── errors.py            # 16 — RemoteError estruturado (taxonomia estável, never stack)
│   │   ├── limits.py            # 16 — rate limit (auth/mensagem) + concorrência, reset em testes
│   │   ├── sessions.py          # 16 — TTL/expiração/limpeza + revogação individual de sessões
│   │   ├── message.py           # 16 — mensagem conversacional via AI Core (reply + SSE)
│   │   ├── config.py            # 16 — TTL/CORS/helpers centralizados da camada de acesso
│   │   ├── link.py              # 23/24 — CoreLink WAN (conexão outbound do Core ao relé)
│   │   ├── link_runtime.py      # 23/24 — singleton supervisionado do CoreLink
│   │   └── gateway_prefs.py     # 23 — persistência best-effort do URL do relé
│   ├── gateway/                  # Fase 23/24 — relé WAN standalone (hub, server, limits, backpressure, registry, ops, config, main)
│   ├── websearch/                 # Fase 14 — providers de busca (DDG) + registry
│   ├── research/                  # Fase 14 — SSRF, fetcher, extração, evidência, agente, síntese, knowledge
│   ├── perception/                # Fase 15 — Perception Layer: abstração, provider Windows, state, tool
    │   ├── proactive/                 # Fase 17 — Proactive Agent: events, policy, decision, delivery, scheduler, engine, observer, api
    │   ├── action/                    # Fase 18 — Computer Action Layer: models, registry, validator, safety, executor, adapters, state, observer, tool, service
    │   ├── computer_agent/            # Fase 19 — Computer Use Agent: models, store, planner (unit + Router), verifier, recovery, security, events, agent, service, atlas, tool
    │   ├── identity/                  # Fase 19 — identidade do assistente (display name VEGA; bloco system prompt + overview)
│   ├── schemas/                   # modelos Pydantic base (inclui ToolCall/Declaration, ops, research)
│   ├── services/                  # chat, memory, summarizer, agent, approvals, permissions, audit, ops, atlas, agent_core, research_service
│   ├── tools/                     # Tool Engine: base, registry, builtins + web_search/web_fetch (Fase 14) + observe_computer (Fase 15)
│   └── main.py
└── tests/                   # pytest (Gemini 100% mockado)
```

## Tool Engine (Fase 3)

O modelo **propõe** chamadas de ferramenta (declaradas no registro); o **Core decide e executa**.
Nunca executa chamadas inventadas — só as registradas. Habilite o loop no chat com
`"tools": true` (retorna SSE). Ferramentas embutidas: `get_current_time`, `get_system_info`,
`store_memory`, `recall_memory`, `get_system_stats`, `list_processes`, `open_app`, `kill_process`
(Fase 3/5), `list_dir`/`read_file`/`write_file`/`make_dir`/`delete_path` (Fase 7),
`dev_list_tools`/`dev_get_tool_schema`/`dev_get_config`/`dev_diagnostics` (Fase 8) e
`wake_on_lan` (Fase 12.6; nível 2, envia magic packet UDP),
`web_search`/`web_fetch` (Fase 14; nível 0/1), `observe_computer`
(Fase 15; nível 0; percepção estruturada do computador via Perception Layer) e
`computer_action` (Fase 18; nível 1; ações controladas de mouse/teclado/janela via
Computer Action Layer — validada, autorizada e auditada; desabilitada por padrão) e
`computer_use` (Fases 19/20; nível 2; executa o loop do Computer Use Agent com goal:
planeja, verifica, executa e observa **dentro do turno de chat**, transmitindo os
eventos reais via `agent_event` no SSE; desabilitada por padrão).

## Computer (Fase 5)

Controle do computador com segurança em camadas:

- `get_system_stats`/`list_processes` — **nível 0**, somente leitura, automáticas.
- `observe_computer` — **nível 0** (Fase 15): observação estruturada do computador via Perception Layer
  (capacidades detectadas reflexivamente, janela ativa, stats, processos — sem inventar capacidades;
  screenshot opcional e manual, nunca automático; binário nunca em logs/eventos).
- `open_app` — **nível 1** (risco médio): abre app ou arquivo via `os.startfile`.
- `kill_process` — **nível 3** (risco alto): encerra processo por PID; bloqueado por padrão,
  só executa com aprovação explícita do usuário (fluxo `approval_pending` da Fase 4).

O `SystemController` (`app/computer/controller.py`) usa **PowerShell nativo + stdlib** (sem
dependências extras) com `CREATE_NO_WINDOW` e nunca toca na IA. Os mesmos dados ficam
disponíveis na API read-only `/api/system/stats` e `/api/system/processes` para demos e
integrações.

## Perception Layer (Fase 15)

Fundação da camada de **PERCEBER** do futuro Computer Use (PERCEBER→INTERPRETAR→
PLANEJAR→AGIR→OBSERVAR→VERIFICAR→RECUPERAR). **SEM agente autônomo de controle
de UI, sem OCR, sem loops de Computer Use.** A percepção é:

- **Independente do LLM**: o modelo recebe `ComputerObservation` estruturado,
  nunca tenta adivinhar o estado da máquina.
- **L0 segura/determinística**: somente leitura, via APIs nativas do Windows
  (ctypes para janela ativa, PowerShell para stats/processos), sem dependências
  externas obrigatórias.
- **Capability discovery reflexiva**: capacidades reais descobertas do ambiente
  (OS, bibliotecas opcionais) — nunca inventadas (screenshot/OCR/vision/A11Y = False
  se a biblioteca não estiver presente).
- **Screenshot opcional e manual**: nunca automático/contínuo; exige
  `perception_screenshot_enabled=true` e bibliotecas `PIL`/`mss` (ausentes por padrão);
  apenas metadata (timestamp/dimensões/hash) entra na memória; binário nunca em
  logs, eventos ou payloads.
- **ComputerState bounded em memória**: histórico de observações com TTL e teto de
  entradas; screenshots NUNCA mantidos indefinidamente.
- **Integração no Agentic Core**: passo `kind="observe"` e tool `observe_computer`
  (LEVEL_0), seguindo o pipeline existente Tool Registry → Permission Engine → Audit.
- **Eventos sanitizados**: `computer.observation.*` na Central de Operações, sem
  binário de screenshot nem segredos.

## Permissions (Fase 4)

Toda ferramenta tem um nível de permissão declarado; o usuário pode ajustá-lo por ferramenta
(`PUT /api/permissions/{tool}`), persistindo o override em `tool_policies`:

- `LEVEL_0` — leitura segura (hora, info do sistema, recall) → automática.
- `LEVEL_1` — ação reversível (gravar memória) → automática.
- `LEVEL_2` — alteração → **exige aprovação do usuário**.
- `LEVEL_3` — potencialmente destrutivo → bloqueado por padrão (só com aprovação explícita).

Fluxo de aprovação: o modelo propõe uma ferramenta nível ≥ 2 → o agente cria um
`ApprovalRequest`, pausa o turno (`evento approval_pending`) e emite `approval_request`.
O usuário decide com `POST /api/approvals/{id}/respond` (SSE) — a resposta flui em tempo
real, aprovados executam e negados voltam ao modelo como recusa. Pedidos expiram em
`APPROVAL_TTL_SECONDS` (10 min padrão). Tudo fica registrado no trilho `/api/audit`.

## Filesystem (Fase 7)

Acesso a arquivos em sandbox com root configurável (`config.files_root`, padrão `~`):
o `SystemController` de filesystem (`app/filesystem/controller.py`) **rejeita** caminhos
absolutos e escapes `..`, garantindo que o JARVIS nunca acesse arquivos fora do diretório
permitido. Ferramentas (em `app/tools/filesystem.py`) por nível de permissão:

- `list_dir` / `read_file` — **nível 0** (somente leitura, automáticas).
- `write_file` / `make_dir` — **nível 2** (exigem aprovação).
- `delete_path` — **nível 3** (bloqueado por padrão, só com aprovação explícita).

## Developer Tools (Fase 8)

Ferramentas internas de **desenvolvimento e inspeção** via Tool Engine — somente operações
**predefinidas** pelo próprio Core, sem execução arbitrária:

- `dev_list_tools` (nível 0) — catálogo das ferramentas registradas, com nível efetivo, risco e
  flags de confirmação/bloqueio (reflete overrides persistidos).
- `dev_get_tool_schema` (nível 0) — inspeciona o schema de parâmetros de uma ferramenta.
- `dev_get_config` (nível 1, risco médio) — configuração de runtime, com **segredos sempre
  mascarados** (chaves/tokens/senhas); aceita filtro por seção (ex.: `tts_`).
- `dev_diagnostics` (nível 0) — versões (Python/sistema/app), contagens e estado interno
  permitido (cache de TTS, modelo de IA configurado, estados do agente).

**Separação arquitetural preservada:** o Core **nunca** executa shell, terminal, subprocessos ou
código arbitrário nesta fase (regra assegurada por teste). A execução no sistema operacional
continua sendo papel do futuro **Local Agent**, isolado do Core e com suas próprias políticas.

## Voz (Fase 6/6b)

Controle por voz do navegador — **STT 100% no navegador** (Web Speech API) e **TTS neural**
(backend `/api/tts`, vozes Microsoft Edge) com **fallback automático** para a voz local do
navegador quando o serviço externo estiver fora:

- **STT (ditar):** botão de microfone no composer (Chromium/Safari). Ao falar, a transcrição
  preenche o campo; quando ativo, o campo ganha um anel pulsante de energia. Detecção de
  recurso: o botão só aparece se o navegador tiver `SpeechRecognition`.
- **TTS (ler respostas):** botão de alto-falante liga/desliga a leitura das respostas do JARVIS
  em voz alta. Voz neural `pt-BR-AntonioNeural` (perfil **Antonio**, padrão — masculino, calmo,
  natural; alternativas em `pt-BR`/`pt-PT`/`en-US`) gerada no backend via `edge-tts`; se o
  serviço externo falhar, cai automaticamente para a voz local `pt` do navegador. O estado fica
  em `localStorage` (`jarvis.tts`) e é reaplicado sozinho a cada sessão.
- **SpeechManager (Fase 6b — UX de fala):** o frontend (`js/speech.js`) prepara cada resposta
  pelo novo `GET /api/tts/speech`, que devolve o `speech_text` limpo e formatado **sem alterar o
  texto visual** (`display_text`), sugerindo também os blocos de fala (`utterances`). Uma
  **fila** reproduz um bloco por vez (nunca dois ao mesmo tempo), é **interruptível**
  (enviar novo comando cancela a fala) e expõe o estado **SPEAKING** (ponto de status pulsa em
  verde + hint "JARVIS falando..."). Falha de TTS **nunca** derruba o JARVIS (usa fallback e
  segue silenciosamente).
- **Sanitização + formatação de fala (backend `app/speech/`+`app/services/tts.py`):** o texto
  falado remove/padroniza **Markdown** (`*`, `#`, `_`), **código** ("Encontrei um trecho de
  código..."), **URLs** ("há um link disponível na tela"), **JSON/HTML** e **emojis**; converte
  **números para palavras em pt-BR** (decimais, porcentagens, unidades/temperaturas), mantém
  siglas legíveis e protege marcas/versões ("Windows 11", "Ryzen 5 5600G"). A classificação de
  **contexto** (NORMAL/CASUAL/INFORMATION/CONFIRMATION/ALERT/ERROR/SYSTEM/URGENT) permite
  modular a entonação. O `display_text` na tela permanece 100% intacto.
- **Provedor de voz substituível:** `app/services/tts_providers/` (ABC `TTSProvider` + `edge.py`
  + `registry.py`) e cache LRU de áudio (`config.tts_cache_size`). Troque a voz/provedor via
  variáveis `TTS_*` em `config` (`tts_voice`, `tts_rate`, `tts_pitch`, `tts_volume`,
  `tts_timeout`, `tts_fallback_attempts`).
- **Mãos-livres (palavra de ativação):** o botão de barras de energia ativa a escuta contínua.
  Ao ouvir o comando "Jarvis" (ex.: "Olá Jarvis"), o JARVIS captura o que vier em seguida e
  envia sozinho. A palavra pode vir junto (ex.: "Jarvis, que horas são?") ou separada. Fica em
  `localStorage` (`jarvis.handsfree`) e se rearma sozinho após cada resposta.
- Privacidade: a escuta (STT) acontece no seu navegador — as transcrições do microfone vão ao
  serviço de voz da engine do navegador, nunca ao nosso backend. Apenas o **texto** da resposta
  é enviado ao `/api/tts`/`/api/tts/speech` para virar áudio (voz neural via Microsoft Edge).

## Dispositivos & PWA (Fase 9)

Frontend é um **PWA instalável** e o Core reconhece a **classe de dispositivo**:

- **Ícones raster reais** (`frontend/icons/`): `icon-192.png`, `icon-512.png` (purpose `any`),
  `icon-maskable-512.png` (seguro para máscaras de icone do Android) e `apple-touch-icon.png`
  (iOS) — gerados a partir da marca, sem dependências. `manifest.webmanifest` completo
  (`id`, `launch_handler`, `display_override`, `categories`) e meta tags PWA de iOS
  (`apple-mobile-web-app-*`) + `apple-touch-icon` no `index.html`.
- **Botão "Instalar"**: o header mostra um botão quando o navegador dispara
  `beforeinstallprompt`; ao tocar, abre o prompt nativo de instalação do PWA
  (`js/app.js`) e some após instalado (`appinstalled`).
- **Detecção de dispositivo** (`app/services/device.py`): normaliza o `User-Agent` em
  `DeviceType` (`desktop`/`mobile`/`web`) mais a flag `touch`. Rastreadores/CLI caem em `web`.
  Sem fingerprint nem persistência — apenas para a UI/se cliente ajustar a experiência.
   Disponível em `GET /api/device/info` e, de forma compacta, no campo `device` de `GET /health`.

## Atlas (Fase 10)

O **Atlas** é o projeto Django "Knowledge Operating System + AI Assistant" (em
`D:\dowloads\Atlas`) consumido pelo JARVIS como **serviço externo** via HTTP/JWT —
fonte de inteligência e orquestração de conhecimento. Cliente dedicado em
`app/services/atlas_client.py` com cache de token (login + refresh), isolamento de
falhas e **nenhum segredo em logs**.

- **Delegação automática**: em `POST /api/sessions/{id}/messages`, quando
  `ATLAS_ENABLED=true` o Core envia o histórico ao `POST /api/assistant/chat/` do
  Atlas (provê resposta + `sources` + `classification` + `proposals` + `agent_run`).
  Se o Atlas estiver **desabilitado, não configurado, ou indisponível**, o fluxo cai
  automaticamente para o **Gemini local** (fallback) sem tocar a experiência do chat.
- **Transparência**: a resposta do Atlas (que não é SSE) é reemitida como SSE pelo
  Core (`start → chunk → done`) e persiste com `source=atlas` + metadados no campo
  `metadata` da mensagem.
- **`GET /api/atlas/status`**: estado atual (habilitado/configurado/base_url — sem
  segredos). **`GET /api/atlas/health`**: healthcheck real (503 se desabilitado/não
  configurado/indisponível; 401 se credenciais inválidas).
- **Config**: `ATLAS_ENABLED`, `ATLAS_BASE_URL` (padrão `http://127.0.0.1:8000`),
  `ATLAS_EMAIL`, `ATLAS_PASSWORD`, `ATLAS_TIMEOUT` — ver `.env.example`.

## AI Core + Central de Operações (Fase 11)

O **AI Core** (`app/ai/core.py`) orquestra cada turno do chat e o **AI Router**
(`app/ai/registry.py`) escolhe o provedor:

- **AI Router**: resolve o primeiro provedor **configurado** que atende à tarefa
  (`generate`/`embed`), com fallback automático. A ordem vem de `AI_PROVIDER_ORDER`
  (padrão `local,gemini,deterministic`). O **Gemini deixou de ser obrigatório**.
- **LocalLLMProvider** (`app/ai/providers/local_llm.py`) — **motor principal local**:
  LLM generativo via **llama.cpp** (`qwen2.5-3b-instruct-q4_k_m`) rodando 100% na
  máquina com `llama-cpp-python`, **sem rede e sem chave API**. Modelo de embedding
  separado (`nomic-embed-text-v1.5`, 768-dim). Implementa o mesmo contrato
  `AIProvider` (`generate`/`stream`/`analyze`/`embed`/`tools`/`health_check`),
  carrega o GGUF de forma **lazy e compartilhada** (singleton), executa em
  `asyncio.to_thread`, e faz **tool calling** próprio (parse do formato texto
  `<tool_call>` do Qwen) — nunca executa ferramenta não registrada.
  Config em `AI_LOCAL_LLM_*` (paths, `n_ctx`, `n_threads`, `temperature`);
  desabilite com `AI_LOCAL_LLM_ENABLED=false` para voltar ao Gemini-only.
- **DeterministicProvider** (`app/ai/providers/deterministic.py`) — *safety fallback*
  final: responde de forma previsível e offline (horário, data, aritmética segura
  via AST, saudação). Sem LLM generativo e **sem** capacidade `tools`/`embed`.
- **Observabilidade**: cada turno registra `execution_events` (tabela SQLite) —
  `chat.started`, `provider.selected`, `path.selected`, `chat.completed`,
  `chat.failed`, além de `fallback_triggered` (quando o provedor primário falha e
  cai no fallback) e `local_model.started/completed/failed` (ciclo do modelo local) —
  sempre **sanitizado** (nunca conteúdo de mensagens, tokens, chaves ou secrets).
  Fonte da Central de Operações.
- **Central de Operações** (aba do SPA): `GET /api/ops/overview` (AI Core,
  provedores, memória, tarefas, tools, Atlas), `GET /api/ops/providers`,
  `GET /api/ops/providers/{name}/health`, `GET /api/ops/events`.

## Agentic Core & Workspace (Fase 13)

Camada de **tarefas agênticas**: o JARVIS transforma metas de alto nível em planos de passos
executáveis e os processa de ponta a ponta — TASK → PLAN → EXECUTE → OBSERVE → VERIFY →
RECOVER → COMPLETION → RESPONSE — **reutilizando todo o pipeline existente** (Tool Registry,
Permission Engine, Auditoria, `agent._run_tool`). Nenhum loop de agente novo foi criado.

- **Modelo `AgentTask`** (`app/models/tasks.py`): id, sessão, objetivo, status
  (`planned/running/completed/failed/cancelled`), `plan_json`/`progress_json` (passo a passo
  com status `pending/running/done/failed/skipped`, tentativas e sumário), `steps_done/total`,
  erro e ponte `approval_id` para a "pausa" por aprovação.
- **Planner determinístico** (`app/services/agent_core.py` → `plan_for`): classifica o
  objetivo por regex (`análise` → lista arquivos + diagnóstico; `testes` → diagnóstico +
  comando permitido; `código` → listagem somente leitura) e monta planos **sem reflexão de
  prompt para o LLM** — determinístico, audável e limitado por `agent_core_max_steps`.
- **Execução**: `run_agent_task` (async generator SSE) percorre o plano passo a passo,
  verificando cada resultado (`verify_step`: `exit_ok`/`contains`). Falha com guard `retry`
  re-executa o **mesmo passo** até `agent_core_max_retries`; `skip` ignora o passo sem derrubar
  a tarefa; `stop` marca `failed` e encerra. A completude só é marcada se a tarefa ainda está
  `running` (cancelada não é ressuscitada).
- **Aprovações (L2/L3)**: passo de nível ≥ 2 cria um `ApprovalRequest` que vincula
  `task.approval_id`, pausa a tarefa e emite `approval_request/approval_pending` (mesma
  mecânica do chat). `POST /api/approvals/{id}/respond` detecta a ligação e **retoma a tarefa**
  via `resume_agent_task` (aplicado, uma única vez; negado → `cancelled`; 409 se já decidido).
- **Workspace API** (`app/api/workspace.py`): `GET /api/workspace/tasks` (lista),
  `POST /api/workspace/tasks` (cria e roda — SSE), `GET/DELETE /api/workspace/tasks/{id}` e
  `POST /api/workspace/tasks/{id}/cancel`.
- **Config**: `agent_core_enabled`, `agent_core_max_steps` (12), `agent_core_max_retries` (1).
- **Web Search (esqueleto)**: `app/websearch/` com ABC `SearchProvider` + `SearchResult`/
  `SearchQuery` e um `DuckDuckGoSearchProvider` **placeholder (`enabled=False`, sem rede)** —
  contrato pronto para o provedor real futuro.
- **Observabilidade**: eventos `agent.task.*` (started/planned/step/completed/failed) e
  contadores `agent_tasks` por status na Central de Operações.

## Web Research + Knowledge + Atlas (Fase 14)

Pesquisa web **real e local-first** com segurança de entrada punitiva (SSRF), coleta que
**nunca depende de LLM**, e síntese com citações estruturais reais — integrada ao Agentic
Core como nova capacidade `kind="research"` e exposta por tools `web_search`/`web_fetch`.

- **Hall de busca** (`app/websearch/`): ABC `SearchProvider` (`search`/`health`/`capabilities`),
  `SearchResult` com fonte/rank/fetched_at/metadata, `DuckDuckGoSearchProvider` real (endpoint
  HTML/Lite, sem API key; transporte injetável) e `SearchProviderRegistry` (singleton, reset
  para testes). Config `WEB_SEARCH_ENABLED`/`WEB_SEARCH_PROVIDER`/`WEB_SEARCH_TIMEOUT`/
  `WEB_SEARCH_MAX_RESULTS`/`WEB_SEARCH_RETRIES`.
- **Fetcher com SSRF em 3 camadas e sem exceções inseguras** (`app/research/ssrf.py` +
  `fetcher.py`): só `http`/`https` com allow-list de portas {80,443,8080,8443} (extensível por
  teste); credenciais embutidas/`user@host` rejeitadas; resolução DNS obrigatória para TODO IP
  do hostname (localhost/RFC1918/link-local/CGNAT/multicast/reservado/IPv6-mapped/NAT64/metadata
  de nuvem bloqueados); **re-validação a cada redirect** (anti DNS-rebinding); limite de
  bytes/redirects/timeout; MIME binário rejeitado; charset do header/`<meta>`. Resolver é
  injetável — testes nunca tocam a rede interna.
- **Extração** (`extract.py`): parser apenas-stdlib remove script/style/nav/footer/form,
  captura título/headings/links absolutos http(s) e texto principal limitado. Conteúdo web é
  sempre dado **NÃO-CONFIÁVEL** (anti prompt-injection).
- **Research Agent** (`app/research/agent.py`): orçamento de consultas/páginas/bytes/duração;
  coleta agrega evidência por URL com `build_search_evidence`; Síntese **local-first** no Router
  (primário → fallback → determinístico dentro do orçamento de tokens) com separação
  rígida `<web-content>` vs instruções confiáveis; **citações reescritas estruturalmente** —
  nenhuma URL inventada sobrevive ao parser (`parse_synthesis_answer`); detector de
  prompt-injection registra em `uncertainties` (nunca obedece).
- **Knowledge ledger** (`knowledge.py` + modelo `KnowledgeRecord`): candidatos só nascem de
  evidências reais; confiança `SOURCE_CONFIRMED`/`MULTI_SOURCE_CONFIRMED`/`MODEL_INFERRED`;
  dedup por `content_hash`/claim; conflito NUNCA sobrescreve silenciosamente (só registra);
  textos sanitizados (secrets mascarados) antes da persistência.
- **Política do Atlas** (`ATLAS_WRITE_MODE` = `suggest` default): `suggest` apenas marca
  `suggested` no ledger; `auto`/`user_confirmed` escrevem via `POST /api/knowledge/`
  (`store_knowledge` com retry pós-login); falha do Atlas nunca derruba a pesquisa.
- **Integração**: `plan_for("Pesquise na web…")` → `kind="research"`; passo pesquisa roda
  `run_web_research` (wrapper único API/agente) com eventos `research.*` e contadores no bloco
  `research` da Central de Operações. Tools `web_search` (L0) e `web_fetch` (L1) registradas no
  Tool Registry (→ Permission → Audit).
- **API** (`app/api/research.py`): `POST /api/research` (SSE: `research.started` →
  coleta/síntese → `research.result` → `done`), `GET /api/research/{run_id}`,
  `GET /api/research/{run_id}/sources`, `POST /api/research/{run_id}/knowledge/promote`.
- **Config**: `web_search_*`, `web_fetch_*`, `web_research_*` (queries 3, páginas 4, 2 MiB,
  40 s, min 2 evidências), `web_tools_status`, `atlas_write_mode` — ver `.env.example`. O
  modelo `ResearchRun` e `KnowledgeRecord` entram nos testes/hermeticidade (nunca rede real).

## Endpoints

| Rota | Descrição |
|---|---|
| `GET /` | frontend (chat) — servido pelo Core na mesma porta |
| `POST /api/sessions` | cria sessão |
| `GET /api/sessions` | lista sessões |
| `GET/DELETE /api/sessions/{id}` | detalhe / exclusão |
| `GET /api/sessions/{id}/messages` | histórico da sessão |
| `POST /api/sessions/{id}/messages` | envia mensagem (JSON ou streaming SSE com `stream:true`) |
| `POST /api/memories` | cria memória (fact/preference/note; `session_id` opcional) |
| `GET /api/memories` | lista (filtra por `session_id`/`kind`) ou busca por relevância com `query` |
| `GET/DELETE /api/memories/{id}` | detalhe / exclusão |
| `GET /api/approvals/pending` | pedidos de aprovação aguardando decisão |
| `POST /api/approvals/{id}/respond` | decide (approve/deny) e retoma o turno (SSE) |
| `GET /api/permissions` | catálogo de ferramentas + nível efetivo |
| `PUT/DELETE /api/permissions/{tool}` | ajusta / remove override de nível |
| `GET /api/audit` | trilho de auditoria (execuções e decisões) |
| `GET /api/system/stats` | CPU, memória, disco e boot (somente leitura) |
| `GET /api/system/processes` | processos em execução (`limit` 1-200) |
| `GET /health` | saúde da API + banco (SQLite) |
| `GET /health/ai` | healthcheck do provedor ativo via AI Router (`ok`/`unconfigured`/`error` + `code`) |
| `GET /api/tts/ping` | disponibilidade da voz neural (Edge TTS) |
| `GET /api/tts/speech?text=...&split=` | prepara fala: `display_text` (intacto) + `speech_text` + `context` + `utterances` |
| `GET /api/tts?text=...` | MP3 falado (`audio/mpeg`; `voice` opcional) |
| `GET /api/device/info` | tipo de dispositivo detectado (`device` + `touch`) |
| `GET /api/atlas/status` | estado do Atlas (habilitado/configurado/base_url) |
| `GET /api/atlas/health` | healthcheck do Atlas (login real) — veja acima |
| `GET /api/ops/overview` | Central de Operações: AI Core, provedores, memória, tarefas, tools, Atlas, percepção |
| `GET /api/ops/providers` | estado dos provedores de IA do AI Router |
| `GET /api/ops/providers/{name}/health` | healthcheck ao vivo de um provedor |
| `GET /api/ops/events` | eventos observáveis recentes (sanitizados; `session_id`/`event_type`/`limit`) |
| `GET /api/workspace/tasks` | lista tarefas agênticas (Fase 13) |
| `POST /api/workspace/tasks` | cria e executa uma tarefa (SSE; plano via `agent_core`) |
| `GET/DELETE /api/workspace/tasks/{id}` | detalhe / exclusão de tarefa |
| `POST /api/workspace/tasks/{id}/cancel` | cancela uma tarefa em execução |
| `POST /api/research` | pesquisa web (SSE; `session_id` obrigatório) — Fase 14 |
| `GET /api/research/{run_id}` | resultado da pesquisa (fatos, inferências, incertezas, citações) |
| `GET /api/research/{run_id}/sources` | fontes reais usadas na síntese |
| `POST /api/research/{run_id}/knowledge/promote` | promove candidatos ao conhecimento (política `ATLAS_WRITE_MODE`)
| `GET /api/remote/events` | SSE de eventos remotos ao vivo + replay recente (identidade, conexão, comandos) — Fase 12.5 |
| `GET /api/remote/devices` | lista devices do Device Bridge (identidade/estado; exige `REMOTE_ENABLED=true`) — Fase 21 |
| `POST /api/remote/devices/register` | registra um device **PENDING** (id emitido pelo Core) para clientes finos — Fase 21 |
| `GET /api/remote/devices/{id}/info` | info sanitizada de um device — Fase 21 |
| `POST /api/remote/devices/{id}/rename` | renomeia um device (auditado) — Fase 21 |
| `POST /api/remote/devices/{id}/capabilities` | capabilities ativas de um device — Fase 21 |
| `POST /api/remote/devices/{id}/revoke` | revoga um device (estado + credenciais + sessões) — Fase 21 |
| `POST /api/remote/pairings/validate` | ancora um device PENDING com o código de emparelhamento (cria credencial + sessão) — Fase 21 |
| `POST /api/remote/heartbeat` | heartbeat dos clientes finos (`token` no corpo) — connect/reconnect/heartbeat/disconnect — Fase 21 |
| `GET /api/proactive/status` | estado sanitizado da camada proativa (flags + contagens) — Fase 17 |
| `GET /api/proactive/schedules` | lista os schedules proativos persistentes — Fase 17 |
| `POST /api/proactive/schedules` | cria um schedule (one-shot/interval/cron), auditado — Fase 17 |
| `PATCH/DELETE /api/proactive/schedules/{id}` | atualiza / remove um schedule (auditado) — Fase 17 |
| `GET /api/proactive/stream` | SSE de mensagens proativas (replay + ao vivo) — Fase 17 |
| `GET /api/remote/status` | estado sanitizado do agente remoto (connection_state, pending_commands, …) |
| `GET /api/remote/gateway` | status sanitizado do Core Link WAN (relé; `REMOTE_GATEWAY_ENABLED=true`) — Fases 23/24 |
| `POST /api/remote/gateway/connect` | conecta o Core ao relé (URL opcional; persistido best-effort, nunca secrets) — Fase 23 |
| `POST /api/remote/gateway/disconnect` | desconecta o Core do relé — Fase 23 |
| `GET /api/remote/mobile-capabilities` | registry canônico de capabilities de dispositivo (Fase 25) |
| `POST /api/remote/gateway/command` | despacha um comando de dispositivo ao móvel (idempotente por `command_id`) — Fase 25 |
| `GET /api/remote/gateway/command/{command_id}` | status de um comando em vôo/resultado recente — Fase 25 |
| `POST /api/remote/devices/{id}/commands/poll` | busca comandos de dispositivo pendentes para este device (box LAN, device_type mobile/tablet) — Fase 26 |
| `POST /api/remote/devices/{id}/commands/result` | publica o resultado canônico de um comando executado (box LAN; idempotente) — Fase 26 |
| `GET /health` | saúde da API + banco (SQLite) e device detectado pelo User-Agent |
| `GET /docs` | OpenAPI (Swagger UI) |

Envie `{"content": "...", "stream": true, "tools": true}` em
`POST /api/sessions/{id}/messages` para acionar o Tool Engine (SSE com eventos
`start → tool_start/tool_done → [approval_request → approval_pending] → chunk → done`).

- API + frontend: `http://127.0.0.1:8100` (o Atlas segue na 8000, não é alterado).
- WebSocket reservado na porta `8101` (contrato Core ↔ Local Agent — fase futura).

## Executar

Ver `SETUP.md` para o passo a passo completo (venv, `.env`, testes). Sem `GEMINI_API_KEY`
e até **offline**, o **AI Router** usa o **Local LLM** (motor generativo local) para
conversar/escrever/codificar — e, se o modelo local também não estiver disponível, cai no
**fallback determinístico** (modo de segurança: horário/data/aritmética/saudação). O Core
nunca fica sem resposta.

Suíte de testes (do diretório `backend/`):

```bat
..\.venv\Scripts\python -m pytest -q
```

**744 testes verdes hoje**, cobrindo as Fases 12.1..12.8 (transporte, identidade/emparelhamento,
agente/comandos, retomada pós-aprovação, hardening, WoL, outbox de re-entrega, SSE de eventos
e a finalização/endurecimento da 12.8), a **Fase 13** (pipeline agêntico TASK→PLAN→EXECUTE→
OBSERVE→VERIFY→RECOVER→COMPLETION, recuperação com retry/skip, pausa e retomada por aprovação
e a Workspace API), a **Fase 14** (Web Search + Research Agent + Knowledge, com SSRF/DDG/
Fetcher/Extrator/Síntese/ledger/hermeticidade), a **Fase 15** (Perception Layer: contratos
de percepção, provider Windows (L0), ComputerState bounded em memória, screenshot seguro
só metadata, tool `observe_computer` LEVEL_0 no Registry, passo `kind="observe"` no
Agentic Core, eventos `computer.observation.*` sanitizados, capability discovery reflexiva
e 28 testes de contracts/provider/store/tool/agentic/segurança), a **Fase 16** (Remote Access
Layer & Agent Access, 18 testes herméticos) e a **Fase 17** (Proactive Agent: 39 testes herméticos
de events/policy/scheduler/delivery/segurança/API/SSE), a **Fase 18** (Computer Action
Layer), a **Fase 19** (Computer Use Agent & Identity) e a **Fase 19.5** (VEGA Identity,
Memory & Experience) — **744 no total**. A **Fase 20 (V1)** adiciona 19 testes de
integração e estabilização → **763 no total**. Os testes frontend da **Fase 19.6** (mapa de
estados VEGA, `frontend/tests/vega-state.test.mjs`) somam **7 certificados**. A **Fase 21
(VEGA Multiplatform — Device Bridge)** adiciona **53 testes herméticos**
(`tests/test_fase21_devices.py`) + E2E isolado com banco real em arquivo
(register→pair→heartbeat→rename→info→list→claim→disconnect→**reboot**, `ALL_OK`) + **6 testes
Node** do bridge desktop (`desktop/bridge.test.mjs`) → backend **816 passed, 1 skipped**. A
**Fase 22** adiciona **7 testes** → backend **823 passed, 1 skipped**. A **Fase 23** adiciona
**20 testes herméticos** (`tests/test_fase23_gateway.py` — relé hub + CoreLink e2e em memória)
→ backend **843 passed, 1 skipped**. A **Fase 24** adiciona **56 testes herméticos**
(`tests/test_fase24_wan.py` — relay heartbeat WAN com correção P1/P5, TTL de AUTH em voo,
rate limit por peer, wake-event da mailbox, limites, snapshot/vocabulário do CoreLink,
tunáveis do runtime, schema/API e 2 E2E em processo com banco real) → backend
**899 passed, 1 skipped**. A **Fase 25** adiciona **31 testes herméticos** em
`tests/test_fase25_mobile_commands.py` (registry/validação de capabilities, dispatch/idempotência/
timeout/histórico do CoreLink, roteamento `mobile_command`/`command_result` pelo relé, schemas e
API) e registra `MessageType.MOBILE_COMMAND` (aditivo) → backend **931 passed, 1 skipped**.
A **Fase 26** (VEGA Mobile Agent Orchestration) adiciona **33 testes herméticos** em
`tests/test_fase26_mobile_agent.py` (tools `mobile_*` registradas, inbox LAN idempotente e
limitado, resolução de alvo/transporte e dispatch do `mobile_agent`, padrões de intenção
pt-BR, API LAN `commands/poll|result` com 503) → backend **966 passed, 1 skipped**.
O Desktop valida `npm test` em `desktop/` (**28 testes Node**:
bridge 6 + version 3 + updates 6 + config 5 + wan 8). Os
`dev_*` usam `asyncio.run` (compatíveis com o
Python 3.14, sem depender de event loop pré-existente). Os testes são herméticos: forçam
`ENV=test`, `GEMINI_API_KEY=""`, `DATABASE_URL=sqlite:///:memory:`,
`ATLAS_ENABLED=false`, `AI_LOCAL_LLM_ENABLED=false` e `REMOTE_ENABLED=false` **antes** de
importar o app — nunca tocam serviços reais nem o `.env` da máquina.

## Infra remota (Fase 12.x — foundation 12.1)

O **JARVIS se conecta a uma Gateway externa como CLIENTE** (`wss://…`, WebSocket outbound) —
**nenhuma porta de entrada é aberta no PC**. Com `REMOTE_ENABLED=false` (padrão) **nenhum
worker ou conexão é criado**: o flag é a chave para ativar tudo.

- Flags (`backend/app/core/config.py` + `.env.example`): `REMOTE_ENABLED`, `REMOTE_GATEWAY_URL`,
  `REMOTE_DEVICE_ID`, `REMOTE_DEVICE_TOKEN` (+ `REMOTE_HEARTBEAT_INTERVAL`,
  `REMOTE_CONNECT_TIMEOUT`, `REMOTE_MAX_RECONNECT_DELAY`).
- Package `app/remote/`: `protocol.py` (envelope versionado
  `{version, type, message_id, device_id, command_id, timestamp, payload}` — campos extras são
  rejeitados), `connection.py` (`RemoteConnection`, `WebSocketConnection` outbound,
  `InMemoryConnection`/`MemoryPipe` em processo para testes), `manager.py` (receive loop +
  heartbeat + despacho por tipo), `gateway.py` (`RemoteGateway` com auditoria/ops),
  `runtime.py` (lifespan; ativa só se `should_start()`).
- Tipos de mensagem: `hello, hello_ack, heartbeat, heartbeat_ack, command, command_ack,
  command_result, error`. Estados: `DISCONNECTED, CONNECTING, CONNECTED, RECONNECTING, STOPPING`.
- Auditoria (`AuditLog`): `device_connected`, `device_disconnected`, `remote_connection_failed`,
  `remote_message_received`, `remote_message_sent`. Ops (`ExecutionEvent`, prefixo `remote.*`),
  sempre com metadados sanitizados.
- Segurança: **o transporte nunca executa tools** (um `command` recebido é apenas acusado com
  `execution: "not_implemented"`); o `REMOTE_DEVICE_TOKEN` só viaja no cabeçalho `Authorization`
  e jamais em logs/auditoria/eventos.
- Testes usam um **FakeGateway em processo** (`tests/remote_fakes.py`) — sem internet, sem portas.

## Identidade, credenciais e emparelhamento (Fase 12.2)

Camada de **identidade local** consumida pela Fase 16 (Remote Access Layer) para autenticar devices.
Aditiva e **desabilitada por padrão** — todos os endpoints devolvem `503` com `REMOTE_ENABLED=false`.

- Modelos (`Device`, `Credential`, `PairingRequest`, `RemoteSession`) com as tabelas
  `remote_*`; **tokens e pair-codes são armazenados somente como hash `sha256:`** e nunca
  aparecem em logs/auditoria/ops/payloads.
- **Identidade sempre derivada da credencial**: `device_id`/`pairing_id`/`session_id` alegados
  pelo cliente nunca resolvem identidade — discrepância em `claimed_device_id` → rejeição
  (audit sanitizado).
- Emparelhamento: código de 10 dígitos (entropia uniforme), TTL (10 min), máx. 5 tentativas
  por request, bloom/abuso global (gate com token-bucket), máx. 10 requests ativos, consumo
  atômico (`UPDATE … WHERE status`) — single-use, sem replay de código.
- Revogação (device → credenciais → sessões ativas), expiração e **múltiplas credenciais por
  device**; verificação com `hmac.compare_digest` contra SÓ o hash.
- Bootstrap local (§9): com `REMOTE_DEVICE_ID`+`REMOTE_DEVICE_TOKEN` no `.env`, o lifespan
  cria o root device na primeira subida (idempotente; device revogado não ressuscita). O
  `.env` é a fonte do segredo inicial; o banco guarda apenas o hash.
- API (`/api/remote/*`, package `app/remote/`): `status`, `pairings` (create/submit),
  `validate`, `devices`, `credentials`, `sessions`, `auth`, **`sessions/{id}/revoke`** (Fase 16),
  **`message`** (Fase 16 — conversação via AI Core). Regra mestre: **identidade nunca
  concede bypass ao Permission Engine** — nenhum comando é executado por este caminho.
- Auditoria/ops próprios (`remote.identity`): `device_created/authenticated/…`, `credential_*`,
  `pairing_*`, `session_*` — com metadados sanitizados.
- Fora de escopo desta fase (executadas na Fase 16): Remote Access Layer — mensagens conversacionais via AI Core, rate limiting, payload guard, sessões com TTL/expiração/revogação individual, erros estruturados com correlação, CORS restritivo.

## Agente remoto persistente e execução de comandos (Fase 12.3)

Mantém **uma conexão persistente e segura** com a Gateway (outbound) e **executa comandos**
recebidos — mas **sempre pelo pipeline interno existente**, sem duplicar regras de segurança.
Também aditivo: com `REMOTE_ENABLED=false` (padrão) **zero comportamento novo**.

- **Transporte**: reutiliza o WebSocket outbound da 12.1. Com novo `command_handler` na
  `RemoteGateway`, um `command` recebido é roteado ao `RemoteAgent` (não mais acusado como
  `not_implemented`).
- **`RemoteAgent`** (`app/remote/agent.py`): supervisor que autentica com a Gateway
  (`AuthRequest` → credencial), envia heartbeat, e reconecta com **backoff exponencial**
  (`remote_reconnect_min_delay → max_delay` com jitter). **REVOKED é terminal** — uma
  credencial revogada não dispara reconnect infinito. Despacha comandos sob um `asyncio.Lock`
  (processamento serializado; sem dupla execução em reconnect).
- **Idempotência atômica** (`app/remote/remote_commands.py`): cada comando é persistido na
  tabela `remote_commands` com `command_id` UNIQUE por device ANTES de qualquer execução.
  `REGISTERED → EXECUTING → EXECUTED|FAILED`, mais `PENDING_APPROVAL` e `EXPIRED` (TTL
  `remote_command_ttl_seconds`). Reenvio do mesmo `command_id` → rejeitado (sem replay).
- **executor** (`app/remote/executor.py`): **reutiliza** `Tool Registry`, `Permission Engine`
  (`effective_level`), `AuditLog` e `agent._run_tool`. Níveis L0/L1 executam; **L2/L3 criam um
  `ApprovalRequest` vinculado à Sessão JARVIS estável do device (`Device.jarvis_session_id`)**
  e o comando fica `PENDING_APPROVAL` — nunca executa sem decisão do usuário. Payload/tool/
  argumentos são validados (teto `remote_command_max_payload`); tool não registrada → `failed`.
- **Identidade**: sempre derivada da credencial; o device deve estar `ACTIVE` (revalidação a
  cada comando). Auditoria/status sanitizados — **nunca** token, credential, pairing code ou
  payload sensível.
- **Status** (`GET /api/remote/status`): `RemoteHealthState` somente para observabilidade
  (connection_state, authenticated, healthy, reconnect_count, last_error, …).
- **Fora de escopo desta fase**: UI/SSE mobile e Wake-on-LAN. O dispositivo continua
  **local-first**; a Gateway não é o cérebro nem executa tools Windows.

## Retomada pós-aprovação de comandos remotos (Fase 12.4)

Um comando remoto de nível ≥ 2 fica `PENDING_APPROVAL` até o usuário decidir. Esta fase
**entrega o resultado da decisão** sem criar um loop de agente novo — a execução continua
via o pipeline interno (Tool Registry → Permission Engine → AuditLog → `agent._run_tool`).

- **Ligação Approval ↔ Comando**: `RemoteCommand.approval_id` aponta para o
  `ApprovalRequest` criado na chegada do comando (Fase 12.3) — ligação estável e
  independente da conexão.
- **`app/remote/resume.py`** (`resume_remote_command`): aplica a decisão de forma
  **transacional e offline-safe**. Aprovado → re-claim (PENDING_APPROVAL → EXECUTING),
  executa via `_run_tool` e marca `EXECUTED/FAILED` (audita `remote.command.approved`);
  negado → marca `FAILED` com "negado pelo usuário" (audita `remote.command.denied`), sem
  executar tool.
- **Entrega best-effort à Gateway** (`RemoteAgent.deliver_result` /
  `runtime.deliver_command_result`): após persistir, tenta enviar o `COMMAND_RESULT` pela
  conexão ativa do device. **Nunca lança e nunca é requisito da execução** — se o device
  estiver offline, o resultado permanece consultável.
- **Fonte de recuperação offline**: `GET /api/remote/commands/{device_id}/{command_id}`
  devolve o resultado persistido (status, output sanitizado, erro), sem expor secrets.
- **Wiring**: o endpoint de decisão (`POST /api/approvals/{id}/respond`) detecta approvals
  de comando remoto pela ligação `approval_id` e retoma o comando remoto **em vez** de rodar
  o loop do agente local (que continua intacto para o chat). `applied` é marcado no endpoint.
- **`GET /api/remote/status`** agora reflete `pending_commands` (comandos em andamento,
  incl. aguardando aprovação) a partir do banco.
- **Fora de escopo (futuras da 12.x)**: UI/SSE mobile, Wake-on-LAN, re-entrega direcionada
  por fila quando a Gateway assume o retry.

## Hardening corretivo da 12.4 (Fase 12.4-R1)

O 12.4 entregou a retomada; o **12.4-R1 endurece concorrência e consistência** na retomada
de `RemoteCommand` pós-aprovação, sem mudar a arquitetura (push best-effort + consulta).

- **Claim atômico** (`_claim_for_execution`): a transição `REGISTERED|PENDING_APPROVAL →
  EXECUTING` virou um único `UPDATE ... WHERE status IN (...)` (rowcount == 1). Sob duas
  tentativas concorrentes (`A:APPROVE + B:APPROVE`), apenas UMA vence; a outra recebe erro
  sem re-execução — fecho de concorrência no nível do banco (SQLite WAL/file).
- **Aprovação decidida uma única vez** (`mark_decided` usa `UPDATE ... WHERE status=pending`):
  decisões concorrentes sobre o mesmo pedido só efetivam UMA; as demais retornam "já decidido".
  Comando remoto é identificado por `approval_id` em QUALQUER estado — um pedido concorrente
  nunca cai no loop local do agente.
- **UNIQUE em `RemoteCommand.approval_id`** (1:1 Approval↔Command) + migração idempotente
  (`uq_remote_commands_approval_id`) p/ bancos legados; um approval não governa dois comandos.
- **Consistência Approval ↔ Command**: `applied` só é marcado APÓS a retomada bem sucedida —
  nunca `applied=True` com o comando ainda `PENDING_APPROVAL`. Falha na retomada deixa o
  approval não-aplicado (recuperável), jamais em estado falso.
- **Estado `INTERRUPTED`**: um comando preso em `EXECUTING` (crash entre claim e resultado)
  é recuperado explicitamente (`recover_executing`) para `INTERRUPTED` (resultado desconhecido)
  — SEM re-execução automática de operações não idempotentes; o comando terminal não é
  sobrescrito.

## UI/SSE mobile de eventos remotos (Fase 12.5)

Deixa a atividade remota **visível em tempo real** no frontend, sem expor segredos.

- **Barramento de eventos** (`app/remote/events.py`): pub/sub em processo, thread-safe
  (funciona de produtores síncronos e assíncronos), com **replay limitado** sanitizado.
  Nunca persiste nem publica secrets — apenas metadados (device_id, status, command_id,
  connection_state).
- **Fontes**: identidade (`identity_events` → `remote.identity.*`), conexão do agente
  (`remote.connection.connected/reconnecting/revoked`), transições de comando
  (`remote.command.*` em `register/claim/mark_*`) e resultados.
- **Endpoint SSE** (`GET /api/remote/events`): reenvia o histórico recente e depois transmite
  eventos ao vivo com heartbeat; exige `REMOTE_ENABLED=true` (503 caso contrário).
- **Painel Remote (mobile)** (`frontend/js/remote.js` + aba "Remote" no HUD): conecta um
  `EventSource`, mostra status (dispositivo/conexão/count) e um feed tipado de eventos
  (conexão, comando, identidade), com visual reutilizando os tokens da Central de Operações.

## Wake-on-LAN (Fase 12.6)

Ferramenta `wake_on_lan` (nível 2, aprovação) que envia um **magic packet UDP** para ligar um
device remotamente:

- `app/tools/wol.py`: `parse_mac` (remove separadores antes de fatiar), `build_magic_packet`
  (6×`FF` + 16×MAC) e `_udp_sendto` (socket UDP, monkeypatchável para testes) que envia à
  broadcast/porta configuradas.
- Config (`REMOTE_`/`WOL_*` em `config` + `.env.example`): `WOL_ENABLED`, `WOL_DEFAULT_BROADCAST`,
  `WOL_DEFAULT_PORT` (padrão 9).
- O WoL **exige um ponto de entrada sempre-on** no device-alvo (BIOS/WoL habilitado); por isso
  foi tratado como ferramenta e não como transporte.

## Fila de re-entrega de resultados (Fase 12.7)

Garante que nenhum `COMMAND_RESULT` se perca quando o device está offline no momento do push:

- **`RemoteOutbox`** (tabela `remote_outbox`, UNIQUE `(device_id, command_id)`): persiste o
  resultado de forma transacional antes do push imediato.
- **`app/remote/outbox.py`**: `enqueue_result` (idempotente), `list_undelivered`,
  `mark_delivered`, `payload_of`, `purge_delivered` (housekeeping).
- **`runtime.deliver_command_result`**: enfileira e, se houver agente ativo conectado, tenta o
  push imediato e marca entregue em caso de sucesso. Offline → o push devolve `False` e o
  resultado **permanece na fila**.
- **Re-entrega no reconnect** (`RemoteAgent._flush_pending_results`): após (re)conectar, o
  agente entrega os resultados pendentes de forma direcionada e os marca como entregues.

## Finalização e endurecimento do Remote (Fase 12.8)

Fecha a Fase 12 com auditoria, correções de segurança/robustez e testes de validação das
ETAPAS 2–17, sem mudar a arquitetura nem o default `REMOTE_ENABLED=false`.

- **Segurança (shell injection)**: `run_allowed_command` (`app/computer/controller.py`) passou
  de `shell=True` (allowlist só do primeiro token) para `shell=False` + `shlex.split` — a injeção
  `ping x & calc` nunca mais é interpretada por um shell.
- **Timeout no transporte**: `connection.receive()` usa `asyncio.wait_for(..., timeout)` e
  converte `TimeOutError` → `ConnectionError` (evita leitura presa).
- **Heartbeat resiliente**: `_heartbeat_loop` só encerra em `ConnectionError`; exceções genéricas
  são logadas e o loop continua (sem derrubar o heartbeat em falha pontual).
- **`find_stuck_executing`**: filtro de idade corrigido (`created_at <= cutoff`, em vez do
  comparador quebrado que era sempre verdadeiro).
- **`purge_delivered`**: virou um único `DELETE ... WHERE delivered_at IS NOT NULL AND
  delivered_at <= cutoff` no banco (sem carregar tudo em memória).
- **`end_session`**: removido branch morto.
- **SSE**: eventos sanitizados (drops de aninhados/listas/secrets) e 503 quando `REMOTE_ENABLED=false`.
- **Validação da 12.8** (`tests/test_remote_fase128.py`, ETAPAS 2–17): máquina de estados,
  idempotência em restart, backoff com jitter e máximo, reconnect sem busy-loop, revogação
  bloqueando retomada pós-aprovação, WoL com MAC inválido, concorrência, comando duplicado,
  age filter, purge SQL, receive timeout e freios de injeção de shell.
- **Frontend**: `js/remote.js` adicionado ao pré-cache do Service Worker (`sw.js`).

## Remote Access Layer & Agent Access (Fase 16)

Fronteira de acesso remoto **controlada** sobre a fundação 12.x para um cliente autenticado
conversar com o agente local. Mantém o default `REMOTE_ENABLED=false` (tudo `503`) e **nunca
duplica o AI Core** — cada mensagem remota entra no MESMO fluxo do chat local.

- **Erros estruturados** (`app/remote/errors.py`): taxonomia estável (`REMOTE_DISABLED`,
  `UNAUTHORIZED`, `FORBIDDEN`, `INVALID_REQUEST`, `INVALID_SESSION`, `SESSION_EXPIRED`,
  `SESSION_REVOKED`, `PAYLOAD_TOO_LARGE`, `RATE_LIMITED`, `TIMEOUT`, `INTERNAL_ERROR`) com
  `http_status` mapeado; o middleware `RemoteErrorHandlerMiddleware` converte em
  `{"type":"error","request_id","code","message"}` — **nunca stack trace / paths / detalhes**.
- **Correlação** (`X-Request-ID`, UUID): ecoa no header, nos erros, na auditoria e em cada evento
  SSE das respostas em streaming; `request_id` ausente/inválido é gerado pelo servidor.
- **Sessão com TTL** (`app/remote/sessions.py`): `ensure_session_valid` (expirada → `ENDED` +
  `session.expired`; revogada → `SESSION_REVOKED`; device inativo → `FORBIDDEN`), limpeza em
  lote `expire_stale_sessions` e revogação individual `POST /api/remote/sessions/{id}/revoke`
  (evento `session.revoked`). A sessão é o resultado da autenticação, nunca o segredo.
- **Limites** (`app/remote/limits.py`): rate limit de autenticação (token-bucket por IP + global,
  anti brute-force) e de mensagens por device, teto de mensagens simultâneas e payload guard
  HTTP `413` antes de qualquer processamento (`RemotePayloadGuardMiddleware`).
- **Mensagem conversacional** (`POST /api/remote/message`): autentica com o mesmo contrato
  Bearer, valida sessão/limites e chama `ai_core.handle_message` — provider vindo da dependency
  `get_ai_provider` (mesma do chat local; override nos testes), tools via Permission Engine
  existente, streaming reusa o SSE enriquecido com `request_id`/`session_id`.
- **CORS restritivo**: apenas com `REMOTE_ENABLED=true` e `REMOTE_CORS_ORIGINS` configurado;
  `*` é sempre rejeitado pelo helper `remote_cors_origin_list()` (`app/remote/config.py`).
- **Config** (`app/core/config.py`, seção "Remote Layer (Fase 16)"): TTL, payload, rate, CORS.
- **Testes** (`tests/test_remote_fase16.py`, 18 testes herméticos): contrato de erro, request_id,
  brute-force 429, payload 413, mensagem reply/SSE via FakeProvider (sem rede), TTL/expiração/
  limpeza/revogação granular e CORS nunca `*`.

## Proactive Agent (Fase 17)

Infraestrutura a partir da qual o JARVIS **age sem ser perguntado**, de forma **opt-in,
controlada e auditável**. Tudo é **OFF por padrão** (`PROACTIVE_ENABLED=false`): enquanto
desligado, nenhum worker, item de inbox ou mensagem proativa é criado.

- **Event engine determinístico SEM LLM** (`app/proactive/events.py`): catálogo de eventos
  **aberto** (`ns.sub`), sanitização obrigatória de payload (nunca secrets — tokens, senhas,
  credentials são descartados por fragmento de chave), dedup por `event_id` (UNIQUE). A métrica
  `proactive.llm_invocations` é **sempre 0** (o caminho de decisão não chama LLM).
- **Policy conservadora** (`app/proactive/policy.py`): quiet hours (`22:00-07:00`,
  `proactive_quiet_hours`), cooldown por tipo de evento (`proactive_default_cooldown_seconds`),
  rate limit via `RateLimiter` (reuso Fase 16; `max_per_hour=12` / `max_per_day=48`), prioridade
  (`low` nunca notifica — anti-spam). Reavalia adiados quando a janela reabre (`deferred_sweep`).
- **Decisão** (`app/proactive/decision.py`): `IGNORE/DEFER/NOTIFY/ASK/EXECUTE`. **EXECUTE nunca
  é livre**: a ÚNICA porta é `gate_execute` — Tool Registry → Permission Engine → Auditoria.
  L0/L1 (leitura segura) executam; **L2/L3 → ASK** (mensagem acionável, sem auto-run); tool
  desconhecida ou device revogado → IGNORE + audit `proactive.blocked`.
- **Scheduler persistente** (`app/proactive/scheduler.py`): one-shot / interval / cron com
  fuso horário (`proactive_timezone_offset_minutes`), reconstruído do banco a cada restart
  (catch-up sem dupla execução), habilitação global via `proactive_scheduler_enabled`.
- **Entrega idempotente** (`app/proactive/delivery.py`): `ProactiveMessage` dedup por
  `dedup_key` UNIQUE + TTL (`proactive_message_ttl_seconds`). Web via SSE
  `/api/proactive/stream` (reuso `consume_broker`/`RemoteEventBroker`, replay + vivo); se
  `remote_enabled`, o MESMO evento vai também à camada remota (Fase 12.5).
- **Worker no lifespan** (`app/proactive/engine.py`): async, **só ativa com alguma flag ligada**;
  `tick()` sincroniza (fire_due + deferred_sweep + expire_old) com lock e isolamento de falha;
  emite `system.ready`/`system.shutdown` no start/stop.
- **API** (`/api/proactive/*`): `status` (flags + contagens sanitizadas), CRUD auditado de
  schedules (`/schedules`), stream SSE. Bloco `proactive` em `GET /api/ops/overview` e card +
  `EventSource` no frontend (`js/ops.js`).
- **Config** (`.env.example`): `PROACTIVE_ENABLED`, `PROACTIVE_SCHEDULER_ENABLED`,
  `PROACTIVE_MAX_PER_HOUR`, `PROACTIVE_MAX_PER_DAY`, `PROACTIVE_DEFAULT_COOLDOWN_SECONDS`,
  `PROACTIVE_QUIET_HOURS`, `PROACTIVE_TIMEZONE_OFFSET_MINUTES`, `PROACTIVE_INTERRUPT_ON_CRITICAL`,
  `PROACTIVE_TICK_SECONDS`, `PROACTIVE_MESSAGE_TTL_SECONDS`.
- **Testes** (`tests/test_proactive_fase17.py`, 39 herméticos): sanitização/dedup, quiet
  hours/cooldown/rate/prioridade, scheduler (incl. cron com timezone, restart e catch-up),
  delivery (web/remote/offline/replay/idempotência/expiração), segurança (tool desconhecida,
  L1 executa/L2 ask/L3 ask/device revogado), anti-spam (100 eventos → 1 notificação), LLM-off,
  API (status/CRUD/validação/auditoria/overview) e SSE (replay + connected sem bloquear).

## Computer Action Layer (Fase 18)

Fundação para a futura camada de **AGIR** do JARVIS sobre o computador, de forma
**segura, validada, autorizada e auditável**. Não é o Computer Use Agent completo
(loops Percepção→Ação→Observação, visão/OCR, planner automático e recuperação
autônoma chegam em fases posteriores) — é a **infraestrutura de ações** sobre a
qual o agente computadorizado será construído.

- **Única porta de execução** (`app/action/executor.py`): `ActionExecutor.execute()`
  executa o pipeline obrigatório `request → validação → safety → permission →
  rate limit → cancellation/timeout → OS adapter → audit → event → result`.
  Não existe `/execute_anything`, `/shell` nem `/raw_input` — bypass, em qualquer
  forma, está excluído por construção.
- **Única tool estruturada** (`computer_action`, nível 1, risco médio): recebe
  `action_type` + `params` (dict JSON validado por tipo) e delega ao executor.
  Tipos: `mouse_move`, `click`, `double_click`, `right_click`, `scroll`,
  `key_press`, `hotkey`, `type_text`, `focus_window`.
- **Segurança em camadas**: `ActionSafetyPolicy` bloqueia permanentemente
  combinações perigosas (`CTRL+ALT+DEL`, `CTRL+SHIFT+ESC`, `ALT+F4`, `CTRL+ALT+F2`),
  exige confirmação para `ALT+TAB`, e trata `type_text` como **dado puro**
  (nunca executa shell; padrões de comando são rejeitados). A lista de bloqueadas
  é extensível por config (`COMPUTER_ACTION_BLOCKED_HOTKEYS`).
- **dry_run** (`dry_run=true`): executa toda a cadeia de validação/segurança/
  autorização/rate-limit **sem chamar o adapter** — retorno claro do que seria
  executado. Não há bypass do Permission Engine nem no dry-run.
- **Decisão combinada** (Safety + Permission): `effective_level` existente decide
  confirmação/bloqueio; L2 exige `metadata.confirmed`, L3 bloqueado por padrão.
- **Reuso obrigatório**: Tool Registry, Permission Engine, `RateLimiter`,
  `log_action` (auditoria), `record_event`/`ExecutionEvent` (ops), e o padrão de
  capability discovery de `ComputerCapabilities` (agora `ActionCapabilities`).
- **Adaptadores** (`app/action/adapters/`): ABC (`ComputerAdapter`) + `WindowsAdapter`
  (ctypes/user32: SetCursorPos, SendInput, mouse_event, FindWindow) +
  `UnavailableComputerAdapter` (reporta `UNAVAILABLE`, nunca finge sucesso).
  Sem dependências externas.
- **Estados observáveis**: eventos sanitizados `computer.action.*`
  (`requested/accepted/executed/failed/rejected/timeout/cancelled/unavailable/dry_run`)
  e bloco `computer_actions` na Central de Operações (`GET /api/ops/overview`) +
  card no frontend — só contagens/metadata, nunca payloads ou combinações.
- **Auditoria sanitizada**: `log_action` registra `action_id + ação + resumo`
  (ex.: `type_text(chars=19)`), **nunca** o texto digitado nem o conteúdo completo.
- **OFF por padrão** (`COMPUTER_ACTIONS_ENABLED=false`): a Fase 18 nunca ativa
  controle de mouse/teclado silenciosamente em instalações existentes.
- **Config** (`.env.example`): `COMPUTER_ACTIONS_ENABLED`,
  `COMPUTER_ACTION_TIMEOUT_SECONDS`, `COMPUTER_ACTION_MAX_TYPE_TEXT_CHARS`,
  `COMPUTER_ACTION_MAX_SCROLL_AMOUNT`, `COMPUTER_ACTION_MAX_HOTKEY_KEYS`,
  `COMPUTER_ACTION_RATE_CAPACITY`, `COMPUTER_ACTION_RATE_REFILL_PER_SECOND`,
  `COMPUTER_ACTION_MAX_ACTIONS_PER_MINUTE`, `COMPUTER_ACTION_BLOCKED_HOTKEYS`.
- **Testes** (`tests/test_computer_action_fase18.py`, 61 herméticos, sem hardware
  real — `FakeComputerAdapter`): validação, segurança (bloqueadas/confirmação/
  text), permissionamento (L1/L2/L3), rate limit, timeout, cancelamento, dry-run
  (sem chamada física), adapter unavailable/failure, auditoria e sanitização,
  ausência de bypass (sem `execute_anything`/`shell`/`raw_input` no Registry) e
  comportamento com `COMPUTER_ACTIONS_ENABLED=false`.

## Computer Use Agent & Identity (Fase 19)

Agente de **Computer Use** (loops Percepção→Ação→Observação) sobre a fundação
das Fases 15/18 + Agentic Core da Fase 13, além da fundação de **identidade**
(display name **VEGA**, independente do provider de IA).

- **Pipeline do agente** (`app/computer_agent/`): **Planner** (unitário ou Router
  via provider de IA com contrato JSON) → **Executor** (`ComputerAgent`)
  Percebe→Planeja→**Verifica** (estrutural, invariantes, reuso do Pipeline de
  Verificação da Fase 13 sem duplicação) → Executa `ActionExecutor` → Observa →
  **Recuperação** (retry/skip/replan com tetos) → Conclusão. Autonomia C0–C4
  (LLM **nunca** altera o próprio nível); C2+ exige confirmação
  (`require_confirmation_l2`), C4 exige `explicit_authorization` → cria approval
  (WAITING_CONFIRMATION) retomável por `POST /api/approvals/{id}/respond`.
- **Limites rígidos**: `max_steps`/`max_actions`/`max_retries`/timeout + detector
  de loop (`computer.loop.prevented`); prompt-injection na observação bloqueado
  (`computer.security.prompt_injection`); autonomia acima do nível de `effective_level`
  bloqueada (`computer.security.autonomy_blocked`).
- **Reuso obrigatório, zero duplicação**: Perception (`run_perception`), Action
  Layer (`ActionExecutor` + options com `requested_by/session_id/device_id/
  cancelled_cb`), Tool Registry + Permission Engine + Auditoria, Approvals (as
  mesmas `create_approval/mark_decided` da Fase 13, com ramo de retomada no
  `/respond`), `sse_event` (SSE), Ops (`record_event` com eventos sanitizados
  `computer.task.*`/`computer.observation.*`/`computer.action.*`/`computer.verification.*`/
  `computer.recovery.*`) e Knowledge Store (`KnowledgeCandidateTx` + `store_candidate`
  com `sanitize_text`; conhecimento autônomo apenas quando `can_write_atlas`).
- **API** (`/api/computer/*`): `status`, `POST /tasks` (503 enquanto OFF),
  `GET /tasks/{id}`, `POST /tasks/{id}/cancel`, `POST /tasks/{id}/run` (SSE com
  o mesmo `sse_event` do chat), `GET /tasks`. Tool `computer_use` (nível 2, risco
  médio) registrada no Tool Registry.
- **Execução confidencial**: `secret` executa via `AtomicDirectory.execute` com
  arquivos temporários limpos; meta sanitizada (nunca screenshots/binários/
  segredos). `observe`/`none` também avançam o passo (sem travar o plano).
- **Identidade (VEGA)**: `app/identity/` com `assistant_name/tone/verbosity/
  proactivity/humor/formality` → bloco `identity` em `/api/ops/overview` + bloco
  no prompt sistêmico (`identity_system_block`) + card no frontend. Nome técnico
  do projeto/repositório permanece JARVIS.
- **OFF por padrão** (`COMPUTER_AGENT_ENABLED=false`): o agente nunca atua
  silenciosamente em instalações existentes.
- **Config** (`.env.example`): `COMPUTER_AGENT_ENABLED`, `COMPUTER_AGENT_AUTONOMY`,
  `COMPUTER_AGENT_MAX_STEPS`, `COMPUTER_AGENT_MAX_ACTIONS`, `COMPUTER_AGENT_MAX_RETRIES`,
  `COMPUTER_AGENT_TIMEOUT_SECONDS`, `COMPUTER_AGENT_REQUIRE_CONFIRMATION_L2`,
  `COMPUTER_AGENT_MAX_PLAN_RETRIES` + bloco `ASSISTANT_*`.
- **Testes** (`tests/test_computer_agent_fase19.py`, 42 herméticos, sem hardware
  real — `FakeProvider`/planner fake): planner (contrato/erro/rotas/injeção),
  verificação (aprova/reprova/estrutura/invariantes/erro), recovery (retry/
  skip/replan/limites), execução do loop (0–3 passos, confirmação L2, C4/
  approval, denied pula para o próximo passo, condemnação no fim do plano),
  observação (success/none/injection/screenshot ausente/fracasso), security
  (prompt-injection, autonomy_blocked, secret, plan retries), cancelamento,
  eventos, API e identidade + retomada por approval.

## VEGA Identity, Memory & Experience (Fase 19.5)

Identidade **VEGA** consolidada como camada própria, **Memory Core** local-first
independente de IA/ATLAS, conhecimento validado no contexto do chat e presença
**sempre derivada de sinais reais**. O Atlas vira formalmente **OPCIONAL**.

- **Memory write policy** (`app/services/memory.py` + colunas aditivas em
  `Memory`): `content` é **sanitizado antes de persistir** (reuso `sanitize_text`
  — nunca secrets no storage/contexto; cobertura também em pt-BR,
  `senha/segredo/chave`); `kind` é **classificado deterministicamente** quando
  omitido (`preference`/`decision`/`project`/`fact`); metadata `project`/
  `confidence`/`source`/`expires_at` (**TTL**). Expiradas nunca entram na
  listagem, na busca ou no contexto. Migração idempotente em `_ensure_memories_schema`.
- **Conhecimento validado no prompt**: `build_system_prompt` injeta
  `[Memórias relevantes]` (semântico quando há embedding; lexical no fallback) e
  `[Conhecimento validado]` (ledger local `KnowledgeStore.search` — só status
  confiável `validated/confirmed/user_confirmed/persisted`, dedup por
  `content_hash`, filtro por projeto). Sugeridos/rejeitados **nunca** entram no
  contexto (nada de inferência não verificada no prompt).
- **Atlas OPCIONAL (formalizado)**: import lazy em `ai/core.py` — desligamento
  ou indisponibilidade do Atlas **nunca atinge o chat da VEGA** (fallback local
  garantido, `ATLAS_ENABLED=false` é o padrão); `GET /api/ops/overview` expõe
  `atlas.optional=true`.
- **Presença real** (`app/services/presence.py`): estado **derivado de sinais
  reais**, nunca fingido — prioridade `offline > error > waiting_confirmation >
  working > thinking > idle` (erro recente 90s, aprovação pendente, `AgentTask`
  ativa, loop do Computer Agent, turno de chat aberto). Transição registra
  `vega.state.changed` (rate-limited por processo; só com
  `VEGA_PRESENCE_ENABLED`). Endpoints `GET /api/vega/identity`,
  `GET /api/vega/state`, `GET /api/vega/state/labels`.
- **Personalidade ≠ segurança**: mudar `ASSISTANT_*` **nunca** altera
  `ToolPolicy`/`effective_level` (identidade é camada própria do prompt, isolada
  do Permission Engine).
- **Frontend**: branding VEGA (title/meta/brand/empty-state/placeholder),
  indicador de presença no rodapé (polling 5s), nota "Atlas é opcional — não é
  dependência da VEGA." na Central de Operações.
- **Testes** (`tests/test_fase195.py`, 25 herméticos): write policy/classificação/
  metadata/TTL/filtro de projeto, conhecimento no prompt (inclui "sugerido nunca
  entra"), Atlas desabilitado/indisponível/fallback local, identidade separada de
  permissões, presença (derivação/transições/endpoints) e privacidade (secrets
  fora do storage e do contexto). **744 testes verdes no total**.
- **Config** (`.env.example`): `VEGA_PRESENCE_ENABLED=true` (+ bloco `ASSISTANT_*`).

## VEGA Visual Identity & UX (Fase 19.6)

**Fase puramente VISUAL/UX** — nenhum backend reimplementado. Transforma o
frontend em uma experiência visual coerente com a identidade **VEGA** (futurista,
elegante e tecnológica, **sem cyberpunk/neon exagerado**), preservando arquitetura,
funcionalidades e todos os IDs existentes.

- **Núcleo diamante (Diamond Core)**: o núcleo do orb e a marca trocam o "arc
  reactor" por um **diamante/VEGA-kite** (`brand-mark` e `orb-core` SVGs)
  desenhado com ângulos calmos e um **anel único** fino — reduzindo a associação
  com o arc-reactor. Paleta dark cyan/azul preservada (sem troca completa).
- **Estado como linguagem, com texto + cor**: **16 estados** centralizados em
  `frontend/js/vega-state.js` (módulo puro, browser+Node, fonte única — nada de
  strings espalhadas): `offline, error, idle, listening, thinking, working,
  perceiving, planning, observing, executing, verifying, recovering,
  waiting_confirmation, speaking, success, warning`. Cada estado tem `orb` +
  `label` em pt. **Chip de presença** (`#presence-chip`) e frase no empty state
  (`#empty-presence`) sempre mostram **rótulo legível + cor** (nunca só cor), com
  `role="status"`/`aria-live` para leitores de tela.
- **Brilho controlado como estado**: glows suaves (nunca neon em repouso) por
  estado; o orb expõe `data-state` para o CSS e `body[data-vega]`.
- **Estados reais alimentam a UI**: `app.js` roteia voz (`listening/speaking`) e
  chat SSE (`thinking`/`executing`/`success`/`error`/`waiting_confirmation`) por
  `VegaState`; transientes têm precedência temporal (TTL) sobre o polling de
  presença — sem travar a UI; flash de sucesso após resposta completa.
- **Central de Operações reorganizada por hierarquia**: hero + grupos
  **Operação / Contexto / Integrações** com `#ops-computer-agent` em destaque
  (`--span2`) — **todos os IDs de card preservados**.
- **Timeline do Computer Agent** (`#ops-computer-timeline`) construída **só com
  eventos reais** de `recent_events` (prefixo `computer.*`) — **nenhum
  chain-of-thought fabricado**; atividade real (`computer_agent.by_status`) reflete
  no orb quando a Central está aberta (`window.VegaUI`).
- **Identidade externa**: `manifest.webmanifest` (name/short_name/description →
  VEGA), `favicon.svg` (glyph diamante + ponto de presença), textos claros de
  conectividade no painel Remote ("VEGA conectada remotamente" / "VEGA somente
  local"), versões de assets sincronizadas entre `index.html` e `sw.js`
  (CACHE `vega-v22`).
- **Testes**: `frontend/tests/vega-state.test.mjs` — **7 testes Node** do mapa de
  estados (cobertura total de estados, aliases, `resolvePresence`,
  `resolveComputerEvent`, `resolveComputerTask`, `stateMeta`).

## V1 — Integração & Estabilização (Fase 20)

**Fase de integração e estabilização** — o conjunto completo (chat + agente + Computer
Agent + memória + tools + remote + proativo) funciona como um **único sistema coerente**
sem reimplementar módulos funcionais.

- **Presença fine-grained real**: a camada de presença agora **expõe o estado real do
  Computer Agent** em vez de colapsar tudo em `working` — os estados
  `perceiving/planning/executing/observing/verifying/recovering` aparecem ao vivo no
  chip de presença (`/api/vega/state` e labels), sincronizados com o vocabulário único
  de 16 estados do frontend (`frontend/js/vega-state.js`).
- **`computer_use` retorna ao chat (in-turn)**: a tool `computer_use` **executa o loop
  do Computer Agent dentro do turno de chat** (single agent) em vez de criar uma tarefa
  assíncrona — e transmite os **eventos reais** (`computer.task.*`, observações, ações,
  verificação) de volta ao chat via **`agent_event` no SSE**, alimentando um **card de
  tarefa ao vivo** no frontend (streaming de estado). Retomada por `task_id` após
  `waiting_confirmation`, com teto em-turno (`_MAX_IN_TURN_ADVANCES`) para nunca
  monopolizar o turno.
- **Sem escalada de privilégio**: autonomia/permissão do Computer Agent nunca vêm dos
  argumentos da tool (a tool só aceita `goal`/`task_id`) — continuam fixos em
  `computer_agent_autonomy` da config. Segurança/anti-loop/limites intactos.
- **Frontend**: card de tarefa no chat (início/atividade/confirmação/conclusão/falha)
  com estado terminal em português + orb acompanhando os estados reais do agente.
- **Testes**: 19 testes novos herméticos de integração (`test_fase20_integration.py`),
  incluindo retomada por `task_id`, falhas limpas sem contexto, e SSE `agent_event`
  via `client.stream`. Backend: **763 testes** verdes. Validação end-to-end real e
  segura (instância isolada, C0 observe-only) confirmou o fluxo SSE completo.

### Arquitetura V1 — fluxo de mensagens

```
Usuário → POST /api/sessions/{id}/messages (stream:true)
  → AI Core (provider agnóstico)
    → Tool Registry → Permission Engine (computer_use é L2; aprovação quando exigida)
      → computer_use (tool) → loop do Computer Agent IN-TURN (advance, até _MAX_IN_TURN_ADVANCES)
        → sink de eventos reais (computer.task.*, observation, action, verification)
  → run_agent drena eventos → SSE `{"type":"agent_event","payload":{...}}`
Frontend: handleSSELine → handleAgentEvent → card de tarefa ao vivo + orb (estados reais)
Central de Operações: timeline alimentada pelos eventos `computer.*` persistidos via `ev.emit`.
```

### API V1 (delta sobre as fases anteriores)

- `tool computer_use` **in-turn**: `goal` + `session_id` + `task_id` (retomada).
  Devolve JSON string (`ok`, `status`, `task_id`, `actions_total`, `confirmations_required`,
  `approvals`, `summary`, `error`). Autonomia nunca vem dos argumentos.
- SSE do chat agora carrega `agent_event` com o **evento real** do agente
  (`payload.type` = `computer.task.started|completed|failed|cancelled`,
  `computer.observation.received`, `computer.task.planned`, `computer.action.requested|
  executed|failed`, `computer.verification.success|failed`, `computer.recovery.started`,
  `computer.loop.prevented`, `computer.security.*`).
- Presença: `/api/vega/state` e `/api/vega/state/labels` passam a expor os estados
  fine-grained reais (`perceiving/planning/executing/observing/verifying/recovering`)
  alinhados ao vocabulário único de 16 estados do frontend.

## VEGA Desktop & Mobile — Device Bridge (Fase 21)

**UMA IA, MÚLTIPLOS CLIENTES.** A VEGA Desktop (Electron) e a VEGA Mobile (Kotlin/Compose)
são **clientes finos** do mesmo Core — sem segundo AI Core, Memória, Permissões ou Tools
próprios. Elas apenas **REGISTER** (identidade `PENDING` com `id` emitido pelo servidor) →
**PAIRING** (ancora o registro pendente via código de emparelhamento) → **HEARTBEAT** sobre
`/api/remote/*`.

- **Backend (Device Bridge)** (`app/remote/devices.py|pairing.py|message.py|auth.py|
  sessions.py|events.py|identity_events.py` + schemas/modelos): `device_id` é sempre
  **server-issued** (nunca escolhido pelo cliente); tokens/pair-codes guardados só como hash
  `sha256:`; `PENDING`/`REVOKED` **nunca autenticam**; capabilities normalizadas com
  allow-list (junk rejeitado); `claimed_device_id` é **validado** — claim de outro device →
  401 (anti-phishing); auditoria/ops sanitizados.
- **Gate mestre preservado**: `REMOTE_ENABLED=false` (padrão) → 503 em tudo; o Device Bridge é
  uma subcamada de identidade/estado que **nunca executa tools**, não concede permissões e não
  duplica o AI Core (mensagens continuam na `POST /api/remote/message` — Fase 16). Card
  **DEVICES** na Central de Operações (`ops-devices`, Integrações).
- **Desktop** (`desktop/`): Electron 31; `bridge.js` é módulo Node **puro e testável** (0
  dependências de Electron) — `DeviceBridge` com boot/pair/heartbeat
  (connect/reconnect/disconnect) + retry com backoff + store injetável; `main.js` abre a janela
  para `CORE_URL` (padrão `http://127.0.0.1:8100`), tray com ícone PNG gerado em memória e
  single-instance. Testes `desktop/bridge.test.mjs` (**6/6**) contra mock HTTP. Build Windows:
  `npm run dist:win` → `desktop/dist/VEGA-win32-x64/VEGA.exe` (~172 MB).
- **Mobile** (`android/`): Kotlin + Jetpack Compose (AGP 8.4.2, Kotlin 1.9, minSdk 26,
  targetSdk 34); `VegaBridge.kt` **espelha** `bridge.js` (mesmo contrato de API via
  `HttpURLConnection`, sem Gson/Retrofit); `MainActivity.kt` Compose com status/pareamento/
  disconnect; `DEFAULT_CORE_URL` = `http://10.0.2.2:8100` (emulador → host). Build:
  `gradle assembleDebug` → `android/app/build/outputs/apk/debug/app-debug.apk` (exige JDK 17 +
  Android SDK `platforms;android-34`; veja `local.properties`).
- **Config** (`.env.example`): `DEVICE_ENABLED=true`, `DEVICE_MAX_DEVICES=20`,
  `DEVICE_HEARTBEAT_SECONDS=30`, `DEVICE_RECONNECT_ENABLED=true`, `DEVICE_MAX_PENDING=50`.
- **Validação**: 53 testes herméticos novos + E2E isolado com banco sqlite real em arquivo e
  **segundo boot** confirmando **migração idempotente** e persistência do pareamento — `ALL_OK`.
- **Migração idempotente**: `_ensure_device_bridge_schema` re-aplica sem erro em bancos já
  migrados (validado no E2E).
- **Não versionados** (gerados localmente, fora do git): `desktop/dist/` (EXE),
  `android/**/build/` (APK), `android/local.properties`, `.gradle/`, `releases/`.

## Fase 22 — VEGA Distribution & Client Experience ✔

- **Uma única fonte de versão**: `VERSION` (raiz) = `0.22.0`; lida pelo backend
  (`config.py`), sincronizada pelo Desktop (`desktop/scripts/sync-version.js`, drift
  detectado no teste) e lida pelo Gradle (`android/app/build.gradle.kts`) com
  `versionCode` derivado (0.22.0 → 2200). `GET /api/health` e a Central de Operações
  já expõem essa versão. Teste `backend/tests/test_fase22_version.py` garante a paridade.
- **Windows**: `electron-builder` gera instalador **NSIS**
  (`VEGA-<v>-win-x64.exe`), **portátil** (`VEGA-<v>-win-x64-portable.exe`) e **zip**;
  após o pack, ícone + metadados VEGA são reaplicados com `@electron/rcedit`
  (`desktop/scripts/after-pack.js`) porque o `winCodeSign` do builder exigiria
  criação de symlinks (privilégio ausente neste host). Build: `npm run dist`.
- **Android**: `assembleRelease` assinado com keystore **nunca versionado**
  (`android/keystore.properties` + `android/app/keystore/` gitignored; template em
  `android/keystore.properties.example`; CI assina via secrets
  `VEGA_KEYSTORE_BASE64`/`VEGA_KEYSTORE_PASSWORD`/`VEGA_KEYSTORE_KEY_ALIAS`/
  `VEGA_KEYSTORE_KEY_PASSWORD`). APK `VEGA-<v>-android.apk` assinado (v2),
  `versionName=0.22.0`, `versionCode=2200`.
- **Identidade consistente** (`scripts/gen_icons.py`, stdlib puro): ícone **diamante
  em fundo escuro** (`#05080f` + gradiente `#7ce8ff→#3aa8ff→#2f6bff` + ponto `#2fe6a5`)
  → `desktop/resources/icon.ico|icon.png` + ícone adaptável Android
  (`mipmap-anydpi-v26/ic_launcher.xml`).
- **Client experience**: Desktop reescrito — tela de **setup** no primeiro uso (URL do
  Core, conectar, verificar atualização), estados claros (conectado/reconectando/
  indisponível), tray com "Configurações" e "Verificar atualizações", single-instance;
  Android — auto-conexão quando pareado, status legível e versão do app na UI.
- **Auto-update (foundation)**: `desktop/updates.js` consulta o GitHub Release apenas
  para **metadata** (`parseTag`/`compareVersions`/`classifyUpdate`/`checkForUpdate`;
  nunca baixa/executa arquivos) — 6 testes.
- **Release**: `.github/workflows/build-windows.yml`, `build-android.yml` e
  `release.yml` (tag `v*` cria GitHub Release com artefatos + `SHA256SUMS.txt` via
  `scripts/release_checksums.py`); gradle wrapper 8.7 commitado. Publicação local
  requer `gh` (não instalado) — o pipeline executa no GitHub.
- **Artefatos locais**: `releases/0.22.0/` (4 binários + `SHA256SUMS.txt`).
- **Validação**: backend **823 passed, 1 skipped** (7 testes Fase 22 novos); Desktop
  20 testes (6 bridge + 3 version + 6 updates + 5 config).

## Fase 23 — Core Link WAN (relé separado + link outbound do Core) ✔

- **Relé WAN deployável** (`backend/app/gateway/`, processo **standalone** — nunca o app
  Core): uvicorn via `backend/scripts/run_gateway.py`, lê **somente** `GATEWAY_*` (config
  `app/gateway/config.py`), sem banco (estado em memória), com rate limiting/dedup de peers
  por IP, `max_payload_bytes` e `max_peers_per_ip`. Ex.: `GATEWAY_PEER_TOKEN`, `GATEWAY_HOST`,
  `GATEWAY_PORT` (padrão 8200), `GATEWAY_WS_PATH` (padrão `/api/remote/ws`).
- **Core link outbound** (`backend/app/remote/link.py`): o PC conecta ao relé como peer
  `core` (handshake `hello` + `peer_token`, reconnect/backoff, heartbeat) — nenhuma porta de
  entrada é aberta no Windows. Singleton supervisionado em
  `backend/app/remote/link_runtime.py`, iniciado no lifespan do app somente com
  `REMOTE_GATEWAY_ENABLED=true` (padrão **false**: nenhum worker WAN é criado).
  Override de URL via `POST /api/remote/gateway/connect`; persistido best-effort apenas o URL
  (`data/gateway.runtime.json` — identidade/token nunca gravados).
- **Trust relay**: o relé **não decide autorização**. Mobile pendente só pode enviar
  `auth`/`heartbeat`/`close`; o relé roteia o `auth` ao Core e só **atrela** o device ao peer
  após `auth_result ok:true` (vindo do Core com o `target_device_id`). Correlação por
  `request_id` para handshakes em andamento; substituição de core/mobile duplicado fecha o
  antigo graciosamente. Uma única hop: `mobile → core → mobile` (mobile nunca roteia p/ outro
  mobile). Contadores/observabilidade sanitizados (nunca tokens/payloads).
- **Protocolo aditivo** (`app/remote/protocol.py`): novos `MessageType` `AUTH`/`AUTH_RESULT`/
  `MESSAGE`/`MESSAGE_ACK`/`MESSAGE_RESULT`/`AGENT_EVENT`/`COMPUTER_TASK`/`COMPUTER_RESULT`/
  `APPROVAL_RESPOND`/`APPROVAL_RESULT`/`CLOSE` + campo opcional `request_id` — sem breakar a v1
  (Fase 12/16 continuam intactas; vocabulário versionado no teste).
- **Autoridade única no Core**: conversação WAN reusa o **mesmo** AI Core (provider agnóstico,
  mesma `Permission Engine`, `Computer Agent` e fluxo de aprovações `/api/approvals/{id}/respond`).
- **API/UI**: `GET /api/remote/gateway` (status sanitizado), `POST /api/remote/gateway/connect`
  e `/disconnect`, bloco `gateway` em `/api/remote/status` e card **Core Link WAN** na Central
  de Operações; config em `.env.example` (`REMOTE_GATEWAY_*` + notas de deploy do relé).
- **Android/Desktop**: cliente fino **inalterado** — Android fala REST direto com o Core; o leg
  WAN é um caminho alternativo para thin clients fora da LAN e não afeta o app existente.
  Desktop é um setup wizard (sem painéis de runtime), então também não ganha painel WAN.
- **Validação**: backend **843 passed, 1 skipped** (20 testes novos da Fase 23:
  `backend/tests/test_fase23_gateway.py` — hub do relé + CoreLink e2e em memória sem rede).

## Fase 24 — Secure WAN Gateway & Remote Connectivity ✔

Conecta os clientes finos ao **MESMO AI Core fora da LAN** — sem NAT/port-forwarding,
sem segundo Core/Memória/Permissões/Tools. O relé só **transporta**; TODA autoridade
(auth, permissões, IA, aprovações) continua 100% no PC.

```
  VEGA Mobile (fora da LAN)          Relé WAN (VPS)               VEGA Core (PC)
 ┌──────────────────────┐   wss   ┌──────────────────────┐   wss   ┌──────────────────────┐
 │  VegaWan.kt / wan.js │ ─────▶ │   backend/app/gateway │ ─────▶ │  CoreLink (link.py)  │
 │  auth -> heartbeat   │         │   RELÉ PURO: roteia   │         │  auth, AI Core,      │
 │  msg -> result       │ ◀─────  │   envelopes (1 hop)   │ ◀─────  │  approvals, proact.  │
 └──────────────────────┘   relé   └──────────────────────┘   relé   └──────────────────────┘
            sem segundo AI Core          sem banco/estado         autoridade única
```

- **Heartbeat WAN corrigido (P1/P5)**: móvel **pendente** tem o `heartbeat` respondido
  **inline** pelo próprio relé (`heartbeat_ack {ok, pending:true}`) — o Core NUNCA vê batida
  de device não-atrelado; móvel **atrelado** tem o heartbeat encaminhado ao Core, que devolve
  `heartbeat_ack` **roteável** de volta ao móvel (`target_device_id`, correlação `request_id`);
  ACK do Core sem target é descartado e contado (`relays_dropped`).
- **AUTH em voo com TTL** (`auth_timeout_seconds`, default 60 s): o relé correlaciona
  `request_id` do `auth` → `auth_result` (single-try; se o Core não responder, o móvel usa um
  novo `request_id`) com sweep anti-vazamento de memória + contador `auth_timeouts`.
- **Rate limit local por peer** (defesa mínima do relé): token bucket por peer para
  `message`/`computer_task` (heartbeat/ack nunca limitados), Excedido → `error rate_limited`
  e contador `peer_messages_rate_limited` (réplica sempre `peer_messages_rejected` limpa).
- **Backpressure** (`backpressure.py`): `Mailbox` com wake `asyncio.Event` — o server só
  transmite quando há item (sem busy-wait); `put` em mailbox cheia/ fechada é contado e
  descartado.
- **Bootstrap por código de pareamento via relé**: `auth` sem token carrega `pairing_code` +
  `device_name`/capabilities; o Core reusa `PairingService.submit_code` e devolve **`token`
  emitido UMA única vez** (banco guarda só hash `sha256:`) + `conversation_id` +
  `heartbeat_seconds`/`reconnect_enabled`/`message_timeout`/`queue_ttl` ao cliente.
- **Proativas fora da LAN**: fila off-line no CoreLink (bounded 50/device, dedup por
  `event_id`, TTL `remote_gateway_queue_ttl_seconds`) com `flush` no (re)auth.
- **Observabilidade do link** (`CoreLink.snapshot()` → `/api/remote/gateway` + bloco
  `gateway` em ops): `connection_state` (`WanLinkState`: disabled/disconnected/connecting/
  connected/degraded/reconnecting/auth_failed/revoked), latência (`relay_rtt_ms`,
  `message_latency_ms`), `mobile_heartbeats` por device e `queued_proactive` — sempre sem
  secrets/payloads.
- **Config** (`.env.example`): `REMOTE_GATEWAY_*` da Fase 23 +
  `remote_gateway_reconnect_enabled`, `remote_gateway_heartbeat_seconds`,
  `remote_gateway_connect_timeout_seconds`, `remote_gateway_message_timeout_seconds`,
  `remote_gateway_max_backoff_seconds`, `remote_gateway_queue_ttl_seconds` — repassadas ao
  CoreLink pelo `start_remote_link`.
- **Desktop** (`desktop/wan.js`, **8 testes Node novos**): cliente WAN puro Node WebSocket
  com o mesmo vocabulário (`offline/connecting/connected/reconnecting/authentication_error/
  core_unavailable`), `connect/disconnect/revoke`, heartbeat e turnos de mensagem — sem
  dependências novas.
- **Android**: `VegaWan.kt` (OkHttp WebSocket, `okhttp 4.12.0` como dependência única) + card
  **WAN** na `MainActivity` (URL do relé + código de pareamento, estados, device_id, heartbeat,
  token gerado pelo Core; token nunca em URL/logs). Build local indisponível (JDK 8 no host) →
  validação no CI `build-android.yml` (temurin 17, `:app:assembleRelease`).
- **MANUAL VALIDATION REQUIRED** para a voa real/rota CGNAT: o fluxo e2e é validado em
  processo com banco real; o teste de campo com WSS público via proxy (Caddy/nginx) é manual
  (o relé serve `ws://`; `wss://` exige TLS no proxy). Token de pareamento nunca aparece em
  logs/URLs/SSE.
- **Validação**: backend **899 passed, 1 skipped** (56 testes novos da Fase 24 em
  `backend/tests/test_fase24_wan.py`); Desktop **28/28 testes Node** verdes (`npm test`).

## VEGA Mobile — cliente de conversa em voz e texto (Fase 25)

O Mobile deixa de ser painel de conexão e vira um **cliente de conversa real** que conversa
com o **mesmo** AI Core do Windows/Web — ZERO lógica de IA no aparelho (sem segundo Core,
sem Memória, sem Permissões). A mesma conversa e o mesmo modelo de mensagens; transporte
escolhido automaticamente: **HTTP + SSE na LAN** (Device Bridge) ou **WebSocket via relé WAN**
quando fora da LAN.

**Arquitetura** (todas as novas/alteradas em `android/app/src/main/java/app/vega/client/`):

| Camada | Arquivo | Papel |
|---|---|---|
| Root UI | `MainActivity.kt` | 4 abas: Assistente, Central, Dispositivo, Config (Fase 27) |
| Orquestração | `ChatViewModel.kt` | estado global, transporte, presença, turnos, voz |
| Identidade LAN | `VegaBridge.kt` | register → pair → validate → heartbeat (+token/conversation_id expostos) |
| WAN | `VegaWan.kt` | WebSocket (OkHttp); `onTurnFrame` roteia `agent_event`/`message_result` |
| REST/SSE | `net/VegaHttp.kt` | `/api/sessions`, histórico, ops, aprovações, `sendMessageSse` |
| Voz | `voice/SpeechIn.kt` `voice/TtsPlayer.kt` | STT nativo (pt-BR) e TTS do Core |
| UI | `ui/*` | theme (tokens), markdown, orb, abas |

**Endpoints consumidos** (todos reais, já existentes no Core):
- `POST /api/remote/devices/register`, `POST /api/remote/pairings[/validate]`,
  `POST /api/remote/heartbeat`, `POST /api/remote/auth` — identidade/parceria LAN.
- `POST /api/remote/message` (`stream=true`) — SSE do turno (start → tool_start/tool_done →
  approval_request → chunk* → done; `error` intermediário é **não-terminal** — o turno
  continua e termina em `done`; a leitura encerra ao receber `done`/`error` ou fim do fluxo).
- `POST /api/sessions`, `GET /api/sessions`, `GET /api/sessions/{id}/messages` —
  conversas novas/histórico (a mensagem final de `done` traz `session_id` se ainda não havia).
- `POST /api/approvals/{id}/respond` — aprovação binária de ferramenta.
- `GET /api/ops/overview` — painel Operações. `GET /api/vega/state` — presença do Core.
- `GET /api/tts?text=` — áudio `audio/mpeg` do TTS (somente rota LAN; não há TTS via relé WAN).

**Fluxo de conexão** (LAN): botão Conectar → `boot()` tenta HEARTBEAT com o token salvo →
401 ⇒ re-autentica (`/api/remote/auth`) → persistindo 401 ⇒ limpa credenciais e exige novo
pareamento; 503 ⇒ erro explicado (REMOTE/DEVICE desabilitado no Core); falhas de rede entram
em **reconnecting** com backoff 2/4/8/16/30 s (teto 30 s). Cada `heartbeat` renova
`heartbeatsTotal` e, na 1ª vez, propaga `conversation_id` para conversa ativa.
**WAN**: `connect()` (precisa `ws://`/`wss://`) → hello → auth (token ou código) → heartbeat;
recupera com backoff exponencial + jitter; senha revogada/sessão não reconhecida ⇒
`authentication_error` terminal no app.

**Estados**: enum `ConnectionState` (INITIALIZING/CONNECTING/ONLINE/RECONNECTING/OFFLINE/ERROR)
espelhando `vega-state.js`; presença canônica de 16 estados + aliases (ex.: `online→idle`) em
`VegaPresence` (testada). O orb animado e o chip de presença traduzem a presença ao vivo do Core.

**Streaming (UI)**: mensagem do assistente nasce como SENDING → vira STREAMING ao entrar o
1º `chunk` (conteúdo markdown renderizado) → DONE no `done` (a resposta persistida do Core
substitui o texto acumulado). O core das ferramentas roda como chips acima do balão
(`tool_start`/`tool_done`). Ao chegar `approval_request`, um card Aprovar/Negar é fixado acima
do compositor e o SSE permanece aberto aguardando a resposta.

**Config da URL do Core**: aba Config → "URL do Core" (persistida em `SharedPreferences`);
default `http://127.0.0.1:8100` — com `adb reverse tcp:8100 tcp:8100` o aparelho fala com o
Core do PC **sem expor nada na rede**; alternativa Wi-Fi: `HOST=0.0.0.0` no `.env` do backend
+ porta liberada. `RECORD_AUDIO` é pedido em runtime só ao ativar a entrada por voz.

**Voz**: microfone → `SpeechRecognizer` (pt-BR) → o texto entra no **mesmo** `send()` do teclado
(histórico idêntico). Resposta falada: toggle "Ler respostas em voz alta" na aba Config → ao
concluir o `done`, reproduz `GET /api/tts?text=<resposta>` (presença `speaking` enquanto toca).

**Decisões**: transporte primário LAN (HTTP+SSE), WAN sobreposto no **mesmo** pipeline de
eventos; sem duplicação de lógica de IA/permissoões no APK; sem libs de navegação/DataStore
(nativas); markdown com parser próprio testável (whitelist de inline/block; links só
http/https); tokens nunca em URL/logs/SSE; TTS só em LAN (relé não transporta áudio).

**Limitações conhecidas**: áudio de entrada depende do STT nativo (off-line na qualidade da
ROM); sem notificações push/foreground; sem cartões de agente para computador; sem criptografia
de armazenamento das conversas; TTS pede Core na LAN. Sem chave de IA externa no host, o Core
responde com o fallback determinístico — o pipeline de chat/SSE é o mesmo.

**Validação**: `:app:testDebugUnitTest` → **32/32 testes JUnit** verdes (`TurnEventTest` 12,
`VegaPresenceTest` 7, `MarkdownParserTest` 13) nesta fase da conversa; a **Fase 25 (Device
Control)** soma `MobileCapabilitiesTest` (8) e `MobileCommandExecutorTest` (13) → **53/53**.
Contrato SSE validado contra o Core real em
execução (`POST /api/remote/message` → `start…chunk…done`, conteúdo persistido,
`GET /api/sessions/{id}/messages`, `/api/vega/state`, `/api/tts`). Instalação em aparelho físico
e fluxo de voz em campo = validação manual desta fase.

**Próxima fase**: validação em campo no Galaxy A15 (chat, histórico, reconexão, voz);
notificações push/foreground quando o Core proativa; cartões de agente para computador por
`computer_task`; e ambientar o WAN com WSS real (validação manual pendente).

## VEGA Mobile Control — comandos de dispositivo (Fase 25)

O Core pode **despachar comandos de dispositivo ao móvel** de forma remota (WAN/LAN), com
validação em **duas camadas** e vocabulário de status único. A DIREÇÃO é aditiva sobre o
protocolo WAN da Fase 24: Core → móvel `mobile_command`; móvel → Core `command_result`
(permitido no relé apenas para devices atrelados).

**Modelo de capabilities** (fonte única no Core, `backend/app/remote/mobile_capabilities.py`):
todas de **risco baixo** — informação (`DEVICE_INFO`, `BATTERY_STATUS`, `NETWORK_STATUS`,
`MEDIA_STATUS`) e ajustes locais (`OPEN_URL`, `VIBRATE`, `SET_VOLUME`, `OPEN_APP`,
`SET_BRIGHTNESS`). `ACCESSIBILITY_CONTROL` é **declarada, não executável** nesta fase
(scaffold do serviço de acessibilidade; resposta `unsupported`). O registry do Core valida
capability + args (tipos exatos, sem extras) antes de despachar (`validate_command`).

**Executor Android** (`mobile/`): espelho local do registry + `MobileOps` (fronteira de
sistema, testável) + `MobileCommandExecutor` (lógica pura). Regras locais de segurança:

- `OPEN_APP` só abre pacotes da **allowlist** (`com.android.settings`, `com.android.chrome`,
  `org.mozilla.firefox`) — fora dela, `denied`;
- `SET_BRIGHTNESS` exige `WRITE_SETTINGS` concedido — sem ele, `denied`;
- capability desconhecida ou não executável → `unsupported`;
- **timeout** por comando (`timeout_ms`, default 15 s) → `timeout`;
- **idempotente por `command_id`** (reentrante na fila → `cancelled`; histórico limitado).

**Status canônico** (Core e móvel falam o mesmo vocabulário): `pending/running/success/failed/
denied/unsupported/timeout/cancelled`.

**CoreLink** (`backend/app/remote/link.py`): `dispatch_mobile_command()` idempotente,
`_pending_commands` limitado a 100, TTL lazy (comandos sem resposta viram `timeout`),
histórico limitado a 50 e `_on_command_result` correlaciona por `command_id` + device
atrelado (o relé rotula `device_id` — o Core nunca confia no valor alegado).

**API do Core** (exige WAN conectado; 503 quando o Gateway Link está desligado):
- `GET /api/remote/mobile-capabilities` — registry canônico (fonte para o Core/Desktop);
- `POST /api/remote/gateway/command` — corpo `{device_id, capability, args?, timeout_ms?,
  command_id?}` → 201 `{accepted: true, command}` (400 args inválidos/409 target não
  vinculado);
- `GET /api/remote/gateway/command/{command_id}` — status em vôo ou resultado recente.

**UI Android** (`ui/DeviceControlScreen.kt`, 5ª aba "Dispositivo"): estado do serviço de
acessibilidade (scaffold), allowlist de pacotes, registry de capabilities e os últimos
comandos executados. `VegaAccessibilityService` usa `canRetrieveWindowContent=false` —
**nunca lê a árvore**. Nenhuma capability desta fase executa tap/swipe/digitação.

**Validação**: backend **931 passed, 1 skipped** (31 novos da Fase 25 em
`tests/test_fase25_mobile_commands.py`); Android `:app:testDebugUnitTest` **53/53 verdes**
(`MobileCapabilitiesTest` 8 + `MobileCommandExecutorTest` 13 + suíte anterior 32) e APK debug
compila.

## VEGA Mobile Agent Orchestration & UX (Fase 26)

O AI Core/Agent passa a **consumir as capabilities móveis por tool calls estruturadas**:
`Agent → mobile_{tool} → mobile_command → WAN/LAN → executor Android → command_result → resposta
natural`. O móvel continua **cliente fino** (sem AI Core/segunda memória): é executor de
comandos e sensores; o Core decide. Sem chave de IA no APK, sem root/ADB, sem contornos.

**Tools `mobile_*`** (`backend/app/tools/mobile.py`): 9 tools registradas (`mobile_device_info`,
`mobile_battery_status`, `mobile_network_status`, `mobile_media_status` → leitura; `mobile_open_url`,
`mobile_vibrate`, `mobile_set_volume`, `mobile_set_brightness`, `mobile_open_app` → ajustes),
nível L0/L1, risco **low**, sem approval explícito. O agente recebe apenas o **nome amigável** do
dispositivo (parâmetro `device`); `device_id`/tokens são resolvidos no Core.

**`mobile_agent.py`** seleciona o dispositivo com segurança: hint exato → busca borrada →
desambiguação (o agente pergunta quando ambíguo); sem hint usa o único device entregável ou o
da sessão ativa. Transporte: **WAN preferido** (device atrelado) ou **LAN** (inbox do Core).
Despacho com `timeout_ms` (default 15 s), `_wait_terminal` e eventos `mobile.command.*`
sanitizados. `ok=True` **só** em `success` — denied/timeout/unsupported/failed sempre `ok=False`
(nunca se finge sucesso).

**Inbox LAN** (`mobile_inbox.py`): fila em memória por device (bounded, 20 pendentes / 50
resultados), **idempotente por `command_id`**, status canônico validado, `reset()` para testes.

**Intentos pt-BR** (`app/ai/intent.py`): 9 padrões conservadores de "modo assistente pessoal"
(requerem "celular/telefone/aparelho") → confiança 0.96, vencendo desempates contra
padrões de computador ("abra o chrome no meu celular").

**Android** (canais WAN + LAN):
- `VegaWan` (WAN): ingestão `mobile_command` → `ChatViewModel.handleMobileCommand` → executor →
  `sendCommandResult` (Fase 25);
- `VegaBridge` (LAN): polling `POST /api/remote/devices/{id}/commands/poll` a cada ~2,5 s
  enquanto conectado + `reportCommandResult`; executor compartilhado com deduplicação
  (`historical` terminal → re-poll sem re-executar efeitos);
- **Cards estruturados no chat**: `TurnEvent.ToolDone` parseia `output`/`detail` como JSON
  (`mobile_command_result`) → `MobileCommandCard` renderizado na bolha do assistente
  (status/device/transporte/resultado), com `contentDescription` acessível;
- **Ações rápidas** no chat vazio ("Bateria", "Dispositivo", "Rede") e tokens `VegaSpacing`.

**Validação**: backend **966 passed, 1 skipped** (33 novos da Fase 26 em
`tests/test_fase26_mobile_agent.py`); Android `:app:testDebugUnitTest` **61/61 verdes**
(`MobileCommandCardTest` 5, `TurnEventTest` 14, `MobileCommandExecutorTest` 14 + suíte anterior)
e APK debug compila. Validação física (Galaxy A15) e WAN/WSS real = validação manual pendente.

## Multi-turn Agentic Context & Mobile Redesign (Fase 27)

A VEGA ganha **memória de contexto multi-turn real no Core** — sem segundo AI Core, sem LLM
no celular, sem mudança de arquitetura. O contexto é **informação para o prompt**, nunca
autorização: nada eleva permissões nem bypass do Permission/Safety.

**Camada de contexto determinística** (`backend/app/ai/turn_context.py`):

- `Session.context_json` (coluna aditiva + migração idempotente `_ensure_session_context_schema`).
- `DeviceContext`: o **último resultado móvel bem-sucedido** da sessão (nome amigável do
  dispositivo, capability, status, transporte). Persiste com TTL de **10 min** (`fresh`).
  Gravado de forma best-effort pelo tool runner `mobile` após cada outcome.
- `ActiveTaskContext`: derive do modelo `AgentTask` (`planned/running`) — objetivo, passos
  concluídos, próximo passo, última ferramenta. Tarefas terminais nunca voltam ao contexto.
- Renderização **sanitizada**: `build_system_prompt` injeta `[Continuidade de dispositivo]`
  (só o NOME — `device_id`/transporte/credenciais **nunca** vão ao LLM) e `[Tarefa ativa]`.

**Continuidade determinística** (`app/ai/intent.py` → `detect_continuation`): com contexto de
dispositivo fresco, *"Agora pesquisa FIAP"* resolve direto para `mobile_open_url` no **mesmo**
dispositivo da sessão (Google Search); *"Agora abre o youtube"* → `https://youtube.com`.
Sem contexto fresco, a ambiguidade segue o fluxo normal (explícito > sessão > único online >
perguntar). Wiring em `run_agent` (mantém `local_first`).

**Mobile "Personal Assistant"** (`android/`):

- Navegação 5 → 4 abas: **Assistente** (home/chat), **Central** (conversas + operações),
  **Dispositivo**, **Config** (`model/NavTabs.kt`).
- Home com identidade **VEGA Personal Assistant**, chip de **continuidade** — último
  dispositivo bem-sucedido do histórico local (real, nunca fabricado) — e ações rápidas.
- `MobileCommandCard` evoluído: **glyph de status + rótulo humanizado** em pt-BR
  (`MobileStatusLabels`) acima do resumo/resultado.
- Marca única: "Fale com a VEGA…"; Desktop continua o Command Center.

**Validação**: backend **992 passed, 1 skipped** (26 novos da Fase 27 em
`tests/test_fase27_multi_turn_context.py` — contexto/TTL/task/continuidade/integração e E2E
hermético turno 1→2: "abra o chrome no meu celular" → "agora pesquisa fiap"); Android
`:app:testDebugUnitTest` **70/70 verdes** (`NavTabsTest` 4 + `MobileContinuityTest` 5 + suíte),
`assembleDebug`/`assembleRelease` BUILD SUCCESSFUL. Validação física (Galaxy A15) e WAN/WSS
real = validação manual pendente.

## Download

Distribuições oficiais publicadas como **GitHub Release**:
[VEGA Releases](https://github.com/lz-soareash/Jarvis/releases) ·
[Latest Release](https://github.com/lz-soareash/Jarvis/releases/latest)

### Windows

- [`VEGA-0.22.0-win-x64.exe`](https://github.com/lz-soareash/Jarvis/releases/tag/v0.22.0) —
  instalador (NSIS, 64 bits)
- [`VEGA-0.22.0-win-x64-portable.exe`](https://github.com/lz-soareash/Jarvis/releases/tag/v0.22.0) —
  versão portátil (executa sem instalação)
- [`VEGA-0.22.0-win-x64-portable.zip`](https://github.com/lz-soareash/Jarvis/releases/tag/v0.22.0) —
  pasta portátil compactada

### Android

- [`VEGA-0.22.0-android.apk`](https://github.com/lz-soareash/Jarvis/releases/tag/v0.22.0) —
  aplicativo Android (APK assinado, v2)

### Checksums

- [`SHA256SUMS.txt`](https://github.com/lz-soareash/Jarvis/releases/tag/v0.22.0) — verificação dos
  downloads. Ex.: baixe o arquivo junto e rode no diretório dos downloads:
  `sha256sum -c SHA256SUMS.txt`.

### Instalação e primeiro uso

- **Windows (instalador)**: execute e siga o assistente (NSIS). No primeiro uso o app abre a tela de
  configuração: informe a URL do Core (padrão `http://127.0.0.1:8100`), conecte e, ao parear, a janela
  principal abre com conexão automática. O Core deve estar rodando com `REMOTE_ENABLED=true` e
  `DEVICE_ENABLED=true`.
- **Windows (portátil)**: extraia/execute o `.exe` portátil (ou o ZIP); o restante é igual.
- **Android**: instalar um APK fora da Play Store pode exigir permitir "instalar aplicativos
  desconhecidos" para a origem do download. A tela conecta a partir do campo de URL do Core e
  reconecta automaticamente quando um pareamento já existe.

## Roadmap (resumo)

0. Foundation ✔ · 1. Chat (backend + frontend) ✔ · 2. Memory/Context ✔ · 3. Tool Engine ✔ ·
4. Permissions ✔ · 5. Computer ✔ · 6. Voz (STT/TTS no navegador) ✔ · 6b. Voz (UX de fala: fila,
sanitização, provedores, SPEAKING) ✔ · 7. Filesystem ✔ · 8. Developer ✔ · 9. Mobile/Devices/PWA ✔ ·
10. Atlas ✔ · 11. AI Core + Central de Operações ✔ · 12. Remote (foundation 12.1 ✔ + identidade/
emparelhamento 12.2 ✔ + agente persistente/execução de comandos 12.3 ✔ + retomada
pós-aprovação/entrega 12.4 ✔ + hardening 12.4-R1 ✔ + UI/SSE mobile 12.5 ✔ + Wake-on-LAN 12.6 ✔ +
fila de re-entrega 12.7 ✔ + **finalização/endurecimento 12.8 ✔ (Fase 12 COMPLETA)**: transporte
outbound + dispositivos/credenciais/pairing + agente com reconnect/backoff, comandos executados
só via Permission Engine com aprovação para níveis ≥ 2, resultado da decisão entregue best-effort
à Gateway + consultável offline, eventos remotos em tempo real (SSE) no painel mobile, WoL via
magic packet, outbox persistente que re-entrega resultados no reconnect, e auditoria + correções
de segurança/robustez — shell=False, receive timeout, heartbeat resiliente, filas e age filters
corrigidos, idle timeout, injeção bloqueada, retomada pós-aprovação com revogação respeitada) ·
13. Web Search + Agentic Core & Workspace ✔ (Fase 13 COMPLETA: modelo `AgentTask` + pipeline
agêntico TASK→PLAN→EXECUTE→OBSERVE→VERIFY→RECOVER→COMPLETION reutilizando Tool Engine/
Permission/Auditoria, recuperação com retry/skip, pausa por aprovação (nível ≥ 2) com retomada
pelo `respond`, Workspace API + SSE, esqueleto de Web Search sem rede, eventos `agent.task.*`
na Central de Operações) · 14. Web Research + Knowledge + Atlas ✔ (Fase 14 COMPLETA:
pesquisa web real local-first sem API key — DDG HTML/Lite — com SSRF em 3 camadas e sem
exceções inseguras, Fetcher com revalidação por redirect, extração stdlib, Research Agent com
orçamentos e síntese via Router (local→gemini→determinístico) com citações estruturais reais
e anti prompt-injection, ledger `KnowledgeRecord` com confiança/proveniência/dedup/conflito,
política `ATLAS_WRITE_MODE` (default `suggest`, nunca escreve sem confirmação), capacidade
`kind="research"` no Agentic Core, tools `web_search`/`web_fetch`, API `/api/research` SSE e
bloco `research` na Central de Operações) · 15. Perception Layer & Computer State ✔ (Fase 15 COMPLETA:
percepção L0 segura/determinística — `PerceptionProvider` abstrato + `WindowsPerceptionProvider`
via ctypes/PowerShell nativos, `ComputerObservation` estruturado sem binário, capability discovery
reflexiva (OS/bibliotecas reais), `ObservationStore` bounded/TTL em memória, screenshot seguro
só metadata (binário nunca em logs/eventos), tool `observe_computer` LEVEL_0 no Tool Registry,
passo `kind="observe"` no Agentic Core (reutilizando pipeline Registry→Permission→Audit),
eventos `computer.observation.*` sanitizados, bloco `perception` na Central de Operações,
config `perception_enabled`/`perception_screenshot_enabled` e 28 testes herméticos de
contracts/provider/store/tool/agentic/segurança) · 16. Remote Access Layer & Agent Access ✔ (Fase 16 COMPLETA:
fronteira de acesso remoto controlada sobre a fundação 12.x — `RemoteError` estruturado com taxonomia
estável (`REMOTE_DISABLED/UNAUTHORIZED/FORBIDDEN/INVALID_REQUEST/INVALID_SESSION/SESSION_EXPIRED/
SESSION_REVOKED/PAYLOAD_TOO_LARGE/RATE_LIMITED/TIMEOUT/INTERNAL_ERROR`) convertido por middleware em
`{"type":"error","request_id",...}` sem stack trace; `request_id` corrido via header X-Request-ID em
respostas/erros/auditoria/SSE; sessões com TTL de atividade (`REMOTE_SESSION_TTL_SECONDS`),
expiração/limpeza (`ensure_session_valid`/`expire_stale_sessions`) e revogação individual
`POST /api/remote/sessions/{id}/revoke` com evento `session.revoked`; rate limiting de autenticação
(anti brute-force por IP + global) e por device, teto de concorrência e payload guard HTTP (413) antes
do AI Core; `POST /api/remote/message` conversacional REUTILIZANDO o AI Core — provider agnóstico
(dependency `get_ai_provider`, mesma do chat local), tools via Permission Engine existente (sem bypass),
resposta JSON ou SSE enriquecido com `request_id`/`session_id`; CORS restritivo configurável
(`REMOTE_CORS_ORIGINS`, nunca `*`); tudo OFF por padrão (503) e 18 testes herméticos novos —
577 testes verdes) · 17. Proactive Agent ✔ (Fase 17 COMPLETA: infraestrutura de proatividade
controlada e opt-in — event engine determinístico SEM LLM com catálogo aberto, sanitização
obrigatória de payloads (nunca secrets) e dedup por `event_id`; policy conservadora com quiet
hours (`22:00-07:00`), cooldown, rate limit (`max_per_hour=12`/`max_per_day=48` via `RateLimiter`)
e prioridade; decisão `IGNORE/DEFER/NOTIFY/ASK/EXECUTE` onde a ÚNICA porta de execução é
`gate_execute` (Tool Registry → Permission Engine → Auditoria; L2/L3 → ASK, tool desconhecida/
device revogado → IGNORE, nunca auto-run); scheduler persistente one-shot/interval/cron
(recovery de restart, catch-up, sem dupla execução); entrega Web SSE `/api/proactive/stream`
(reuso `consume_broker`/`RemoteEventBroker`) + Remoto quando habilitado, idempotente por
`dedup_key` com TTL; worker async no lifespan (tick: fire_due + deferred_sweep + expire_old)
que só ativa com flags ligadas; API `/api/proactive/*` (status/CRUD de schedules auditado), bloco
`proactive` na Central de Operações e card/EventSource no frontend; tudo OFF por padrão
(`PROACTIVE_ENABLED=false`) e 39 testes herméticos novos — 616 testes verdes) ·
18. Computer Action Layer ✔ (Fase 18 COMPLETA: infraestrutura de ações controladas
de mouse/teclado/janela com **única porta de execução** (`ActionExecutor`) e pipeline
`request→validação→safety→permission→rate limit→cancellation/timeout→OS adapter→audit→event→result`,
sem APIs de bypass (`/execute_anything`, `/shell`, `/raw_input`); única tool
estruturada `computer_action` (nível 1) com `action_type`+`params` validados por tipo;
`ActionSafetyPolicy` bloqueando `CTRL+ALT+DEL`/`CTRL+SHIFT+ESC`/`ALT+F4`/`CTRL+ALT+F2`
e exigindo confirmação para `ALT+TAB`, extensível por config; `type_text` tratado como
dado puro (nunca shell); `dry_run` valida/autoriza toda a cadeia SEM chamada física;
reuso de Tool Registry, Permission Engine, `RateLimiter`, `log_action`,
`record_event`/`ExecutionEvent` e padrão `ComputerCapabilities`; adaptadores ABC +
Windows (ctypes/user32) + Unavailable (nunca finge sucesso); eventos sanitizados
`computer.action.*` + bloco `computer_actions` na Central de Operações + card no
frontend; auditoria sanitizada (nunca payloads/combinações); tudo OFF por padrão
(`COMPUTER_ACTIONS_ENABLED=false`) e 61 testes herméticos novos — 677 testes verdes) ·
19. Computer Use Agent & Identity ✔ (Fase 19 COMPLETA: agente de Computer Use
sobre Perception+Action com pipeline Percebe→Planeja→Verifica→Executa→Observa→
Recupera→Conclui, reuso de `run_perception`, `ActionExecutor`, Tool Registry,
Permission Engine, Auditoria, Approvals (retomada C4 via `/respond`) e `sse_event`;
autonomia C0–C4 com LLM nunca alterando o próprio nível, C2+ confirmação e C4 com
`explicit_authorization`; verificador estrutural/invariantes + recuperação
retry/skip/replan com limites rígidos (max_steps/actions/retries/timeout + anti-loop);
observação com detecção de prompt-injection e execução `secret` por `AtomicDirectory`
sem artefatos; eventos sanitizados `computer.*` + blocos `computer_agent` na Central
de Operações; tool `computer_use` (L2); API `/api/computer/*` (status/tasks/cancel/run SSE)
e 42 testes herméticos novos — 719 testes verdes) + fundação de **identidade**
(display name VEGA com nome técnico JARVIS preservado; bloco `identity` no
`/api/ops/overview` + prompt sistêmico + card no frontend) ·
19.5. VEGA Identity, Memory & Experience ✔ (Fase 19.5 COMPLETA: Memory Core
local-first com write policy — sanitização antissecrets (inclui pt-BR),
classificação determinística de `kind`, metadata `project`/`confidence`/`source`/
`expires_at` (TTL) e migração idempotente; `[Conhecimento validado]` do ledger no
prompt (só status confiável, dedup, filtro por projeto; sugerido nunca entra);
Atlas formalmente OPCIONAL (import lazy, `atlas.optional`, fallback local — VEGA
100% independente); presença derivada de sinais reais (`offline/error/
waiting_confirmation/working/thinking/idle`) com transições `vega.state.changed`
e endpoints `/api/vega/identity|state|state/labels`; personalidade (`ASSISTANT_*`)
nunca altera permissões; branding VEGA no frontend + indicador de presença;
25 testes herméticos novos — 744 testes verdes) ·
19.6. VEGA Visual Identity & UX ✔ (Fase 19.6 COMPLETA: **fase puramente visual/UX**,
sem reimplementar backend; núcleo **diamante (Diamond Core)** no orb e na marca,
anel único fino e brilho controlado como linguagem de estado — sem cyberpunk/neon
exagerado; **16 estados** centralizados em `frontend/js/vega-state.js` (módulo puro
browser+Node, fonte única) com **rótulo legível + cor** no chip de presença e no
empty state (`role=status`/`aria-live`); voz/chat SSE mapeados por `VegaState` com
precedência temporal de transientes sobre o polling de presença + flash de sucesso;
Central de Operações reorganizada por hierarquia (hero + Operação/Contexto/
Integrações) **preservando todos os IDs**; timeline do Computer Agent **só com
eventos reais** (`recent_events` `computer.*`, sem chain-of-thought) + atividade
real refletida no orb (`window.VegaUI`); identidade externa VEGA (manifest, favicon
diamante, textos claros no Remote, versões de assets sincronizadas em
`sw.js`/CACHE `vega-v22`); **7 testes Node** do mapa de estados) ·
20. V1 ✔ (Fase 20 COMPLETA: integração e estabilização de ponta a ponta — presença
com estados fine-grained REAIS do Computer Agent `perceiving/planning/executing/
observing/verifying/recovering` (vocabulário único de 16 estados + labels); `computer_use`
roda o loop do agente **dentro do turno de chat** transmitindo os eventos reais via
`agent_event` no SSE (card de tarefa ao vivo: started/observation/planned/action/
verification/completed/failed/cancelled, estados terminais em português, orb
sincronizado); retomada por `task_id` pós-`waiting_confirmation`; teto in-turn; modelo
nunca elevando autonomia (tool só aceita `goal`/`task_id`); timeline da Central alimentada
pelos eventos `computer.*` reais persistidos; 19 testes herméticos novos —
763 testes verdes) ·
21. VEGA Multiplatform — Device Bridge ✔ (Fase 21 COMPLETA: clientes finos Desktop — Electron →
`VEGA.exe`, `bridge.js` Node puro testável 6/6 — e Mobile — Kotlin + Jetpack Compose →
`app-debug.apk`, `VegaBridge.kt` espelhando o mesmo contrato — sobre o MESMO Core; **UMA IA,
MÚLTIPLOS CLIENTES** sem segundo AI Core/Memória/Permissões/Tools; Device Bridge no backend:
`device_id` SERVER-ISSUED, tokens/pair-codes só hash `sha256:`, PENDING/REVOKED nunca
autenticam, capabilities normalizadas com allow-list, `claimed_device_id` validado
(claim alheio → 401), gate mestre `REMOTE_ENABLED` preservado (503), endpoints
`/api/remote/devices/register`, `/api/remote/pairings/validate`, `/api/remote/devices`
(info/rename/capabilities/revoke), `/api/remote/heartbeat` (`token` no corpo); card DEVICES na
Central de Operações; migração idempotente; 53 testes herméticos novos + E2E isolado ALL_OK com
segundo boot — backend 816 passed, 1 skipped) ·
22. VEGA Distribution & Client Experience ✔ (Fase 22 COMPLETA: **uma única fonte de
versão** `VERSION` raiz → backend/Desktop/Android com `versionCode` derivado; Windows
**distribuível** — instalador NSIS `VEGA-<v>-win-x64.exe`, portátil e zip via
`electron-builder` com identidade reaplicada por `@electron/rcedit`; Android
`assembleRelease` **assinado** com keystore nunca no git (template em
`keystore.properties.example`; CI via secrets); **identidade consistente** diamante
on-dark (`scripts/gen_icons.py` stdlib → ico/png + adaptável Android); **client
experience** — setup no primeiro uso, estados conectado/reconectando/indisponível,
tray com verificação de atualização, auto-conexão Android; **auto-update foundation**
metadata-only (`updates.js`, 6 testes); **CI/release** `.github/workflows`
(build-windows/build-android/release com SHA256SUMS via `release_checksums.py`) e
gradle wrapper 8.7; backend **823 passed, 1 skipped**) ·
23. Core Link WAN ✔ (Fase 23 COMPLETA: relé WebSocket **standalone** (`backend/app/gateway/`,
deployável, lê só `GATEWAY_*`) + core link outbound do Core como peer `core` — thin clients fora
da LAN autenticam via relé com **trust relay** (o relé não decide autorização; device é atrelado
apenas após `auth_result ok:true` do Core); autoridades (auth/permissões/IA) continuam 100% no
Core — conversação WAN reusa o MESMO AI Core/Computer Agent/fluxo de aprovações; protocolo v1
extendido de forma **aditiva** (AUTH/AUTH_RESULT/MESSAGE/MESSAGE_ACK/MESSAGE_RESULT/
AGENT_EVENT/COMPUTER_TASK/COMPUTER_RESULT/APPROVAL_RESPOND/APPROVAL_RESULT/CLOSE + request_id
opcional); API `/api/remote/gateway` (status/connect/disconnect), bloco `gateway` nos status e
card na Central de Operações; gate `REMOTE_GATEWAY_ENABLED=false` por padrão; Android/Desktop
inalterados; 20 testes novos — backend **843 passed, 1 skipped**) ·
24. Secure WAN Gateway & Remote Connectivity ✔ (Fase 24 COMPLETA: clientes finos conversam
com o MESMO AI Core **fora da LAN** sem NAT/port-forwarding — o relé (`backend/app/gateway/`)
só TRANSPORTA; toda autoridade (auth/permissões/IA) continua no Core; heartbeat WAN corrigido
(móvel pendente responde inline no relé, atrelado roteia ao Core e `heartbeat_ack` retorna
roteável por `target_device_id`); AUTH em voo com TTL (sweep + contador `auth_timeouts`),
rate limit por peer e wake-event na mailbox; bootstrap por código de pareamento via relé com
`token` emitido UMA vez + `conversation_id`/tunables; fila off-line de proativas (bounded,
dedup, TTL) com flush no (re)auth; observabilidade rica do link (`WanLinkState` + latência +
`mobile_heartbeats` + `queued_proactive`); Desktop (`desktop/wan.js`, 8 testes Node) e Android
(`VegaWan.kt` OkHttp + card WAN, validado no CI) como thin clients WAN — sem segundo AI
Core/Memória/Tools; MANUAL VALIDATION REQUIRED para rota real/CGNAT e WSS via proxy; **56
testes backend novos — 899 passed, 1 skipped**) ·
25. VEGA Mobile — Cliente de Conversa ✔ (Fase 25 COMPLETA: chat em voz e texto no aparelho com o
**mesmo** AI Core do PC; `ChatViewModel` orquestra transporte automático **HTTP+SSE (LAN)** /
**WebSocket WAN** no mesmo pipeline de eventos; história e nova conversa por `/api/sessions`;
config de URL persistida + `adb reverse` (default `127.0.0.1:8100`); STT nativo pt-BR como
entrada e TTS `GET /api/tts` como resposta falada; design system de tokens do Web; abas Chat/
Histórico/Operações/Config/(Dispositivo Fase 25); **32/32 testes JUnit** e contrato SSE validado contra o Core real;
instalação em aparelho físico = validação manual) ·
25b. VEGA Mobile Control — Device Command (Fase 25 COMPLETA: Core despacha comandos de
**dispositivo** ao móvel — `mobile_command`/`command_result` aditivos no relé WAN; registry de
capabilities de baixo risco no Core (`mobile_capabilities.py`) com validação em duas camadas;
executor Android puro com allowlist local (`OPEN_APP`), `WRITE_SETTINGS` para brilho, timeout/
idempotência por `command_id` e vocabulário canônico de status; `ACCESSIBILITY_CONTROL`
declarada mas `unsupported` nesta fase (scaffold com `canRetrieveWindowContent=false`); API
`/api/remote/gateway/command`; 5ª aba "Dispositivo"; backend **931 passed, 1 skipped** e Android
**53/53 testes JUnit**) ·
26. VEGA Mobile Agent Orchestration & UX ✔ (Fase 26 COMPLETA: o Agent consome as capabilities
móveis por **tools `mobile_*`** estruturadas (`Agent → mobile_command → WAN/LAN → executor →
`command_result`); `mobile_agent` resolve dispositivo/transporte com desambiguação segura;
inbox LAN no Core (`commands/poll|result`) + polling no `VegaBridge`; intentos pt-BR de
assistente pessoal; **cards `MobileCommandCard`** no chat (output JSON estruturado) com
acessibilidade e **ações rápidas**; backend **966 passed, 1 skipped** e Android **61/61 testes
JUnit**; validação em campo no Galaxy A15 pendente) ·
27. V2 — Multi-turn Agentic Context (planejado): memória do turno (agenda de passos e
justificativas) + contexto inter-turno persistente para tarefas longas ·
28. V3 — Computer Use mais profundo (planejado): gestão de janelas, drag/scroll contínuo,
uso de atalhos seguros e tolerância a layout (por via segura e confirmada) ·
29. V4 — Planejamento hierárquico (planejado): tasks decomponíveis com dependências,
paralelismo controlado e view de progresso na Central de Operações ·
30. V5 — Proativo contextual (planejado): silêncio ativo, monitoramento de estados
(janela/carga/agenda) e sugestões com confirmação explícita ·
31. V6 — Pesquisa agêntica (planejado): research multi-iteração com síntese em
conhecimento persistente e fontes citáveis ·
32. V7 — Voz agêntica (planejado): TTS proativo de estados/resultados e comando
hands-free com confirmação auditiva ·
33. V8 — Perfil do usuário (planejado): memória de preferências com consentimento,
estilos de interação e affordances por dispositivo ·
34. V9 — Autonomia governada (planejado): políticas por tarefa/domínio, revisão de
decisões passadas e auditoria de confiança, sempre com supervisão humana.

Cada fase termina funcional, testada, documentada e sem quebrar a anterior.