# JARVIS — Personal AI Agent

Assistente pessoal inteligente, **local-first**, modular, seguro, escalável e preparado
para múltiplos dispositivos e um futuro modo remoto.

> **JARVIS = "O que posso fazer?"** (agente/execução)
>
> **ATLAS = "O que eu sei?"** (conhecimento — integração futura, opcional)

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
| Testes | pytest (+ pytest-asyncio, TestClient) |

Sem Docker/Redis/Celery nesta fase (não há necessidade real ainda).

## Estrutura

```
frontend/                  # interface concha: HTML + CSS + JS Vanilla + PWA
└── (index.html, sw.js, manifest.webmanifest, css/, js/, icons/)
    # js/ops.js + css/ops.css: Central de Operações (aba do SPA) — Fase 11
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
│   │   └── config.py            # 16 — TTL/CORS/helpers centralizados da camada de acesso
│   ├── websearch/                 # Fase 14 — providers de busca (DDG) + registry
│   ├── research/                  # Fase 14 — SSRF, fetcher, extração, evidência, agente, síntese, knowledge
│   ├── perception/                # Fase 15 — Perception Layer: abstração, provider Windows, state, tool
│   ├── proactive/                 # Fase 17 — Proactive Agent: events, policy, decision, delivery, scheduler, engine, observer, api
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
`web_search`/`web_fetch` (Fase 14; nível 0/1) e `observe_computer`
(Fase 15; nível 0; percepção estruturada do computador via Perception Layer).

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
| `GET /api/proactive/status` | estado sanitizado da camada proativa (flags + contagens) — Fase 17 |
| `GET /api/proactive/schedules` | lista os schedules proativos persistentes — Fase 17 |
| `POST /api/proactive/schedules` | cria um schedule (one-shot/interval/cron), auditado — Fase 17 |
| `PATCH/DELETE /api/proactive/schedules/{id}` | atualiza / remove um schedule (auditado) — Fase 17 |
| `GET /api/proactive/stream` | SSE de mensagens proativas (replay + ao vivo) — Fase 17 |
| `GET /api/remote/status` | estado sanitizado do agente remoto (connection_state, pending_commands, …) |
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

**559+57 testes verdes**, cobrindo as Fases 12.1..12.8 (transporte, identidade/emparelhamento,
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
de events/policy/scheduler/delivery/segurança/API/SSE) — **616 no total**.
(`asyncio.get_event_loop()` no Python 3.13) foram corrigidas. Os testes são
herméticos: forçam `ENV=test`, `GEMINI_API_KEY=""`, `DATABASE_URL=sqlite:///:memory:`,
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
(`PROACTIVE_ENABLED=false`) e 39 testes herméticos novos — 616 testes verdes) · 18. V1.

Cada fase termina funcional, testada, documentada e sem quebrar a anterior.