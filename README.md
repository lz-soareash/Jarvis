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
  obrigatoriamente pelo Tool Engine e pelo Permission System (implementados em fases futuras).
- O **Core** não acessa filesystem/subprocess/terminal. O **Local Agent** é o executor.
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
│   ├── api/                       # rotas (health, chat, memory, approvals, permissions, audit, system, ops)
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
│   │   └── outbox.py            # 12.7 — fila persistente de re-entrega de resultados
│   ├── schemas/                   # modelos Pydantic base (inclui ToolCall/Declaration, ops)
│   ├── services/                  # chat, memory, summarizer, agent, approvals, permissions, audit, ops, atlas
│   ├── tools/                     # Tool Engine: base, registry, builtins (tempo, sistema, memória, computador)
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
`wake_on_lan` (Fase 12.6; nível 2, envia magic packet UDP).

## Computer (Fase 5)

Controle do computador com segurança em camadas:

- `get_system_stats`/`list_processes` — **nível 0**, somente leitura, automáticas.
- `open_app` — **nível 1** (risco médio): abre app ou arquivo via `os.startfile`.
- `kill_process` — **nível 3** (risco alto): encerra processo por PID; bloqueado por padrão,
  só executa com aprovação explícita do usuário (fluxo `approval_pending` da Fase 4).

O `SystemController` (`app/computer/controller.py`) usa **PowerShell nativo + stdlib** (sem
dependências extras) com `CREATE_NO_WINDOW` e nunca toca na IA. Os mesmos dados ficam
disponíveis na API read-only `/api/system/stats` e `/api/system/processes` para demos e
integrações.

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
| `GET /api/ops/overview` | Central de Operações: AI Core, provedores, memória, tarefas, tools, Atlas |
| `GET /api/ops/providers` | estado dos provedores de IA do AI Router |
| `GET /api/ops/providers/{name}/health` | healthcheck ao vivo de um provedor |
| `GET /api/ops/events` | eventos observáveis recentes (sanitizados; `session_id`/`event_type`/`limit`) |
| `GET /api/remote/events` | SSE de eventos remotos ao vivo + replay recente (identidade, conexão, comandos) — Fase 12.5 |
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

**406 testes verdes**, cobrindo as Fases 12.1..12.8 (transporte, identidade/emparelhamento,
agente/comandos, retomada pós-aprovação, hardening, WoL, outbox de re-entrega, SSE de eventos
e a finalização/endurecimento da 12.8). Há ainda 9 falhas pré-existentes em `test_developer.py`
(RuntimeError do `asyncio.get_event_loop()` removido no Python 3.14 — ambientais, sem relação
com o Remote). Os testes são
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

Camada de **identidade local** que a Fase 16 (Remote) usará para autenticar devices. Também
aditiva e **desabilitada por padrão** — todos os endpoints devolvem `503` com
`REMOTE_ENABLED=false`.

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
  `validate`, `devices`, `credentials`, `sessions`, `auth`. Regra mestre: **identidade nunca
  concede bypass ao Permission Engine** — nenhum comando é executado por este caminho.
- Auditoria/ops próprios (`remote.identity`): `device_created/authenticated/…`, `credential_*`,
  `pairing_*`, `session_*` — com metadados sanitizados.
- Fora de escopo desta fase (futuras da 12.x): execução remota de tools, Permission Engine
  remoto, UI mobile, SSE, Wake-on-LAN e sync de sessões entre devices.

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
13. Web (próxima — não iniciada) · 14. Visão · 15. Proativo · 17. V1.

Cada fase termina funcional, testada, documentada e sem quebrar a anterior.