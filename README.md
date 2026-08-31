# JARVIS — Personal AI Agent

Assistente pessoal inteligente, **local-first**, modular, seguro, escalável e preparado
para múltiplos dispositivos e um futuro modo remoto.

> **JARVIS = "O que posso fazer?"** (agente/execução)
>
> **ATLAS = "O que eu sei?"** (conhecimento — integração futura, opcional)

## Regras fundamentais

- IA exclusivamente **Google Gemini** (nunca OpenAI).
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
O `GeminiProvider` é a única implementação atual. O resto do sistema nunca depende do SDK.

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
backend/
├── app/
│   ├── ai/
│   │   └── providers/       # base.py (AIProvider) + gemini.py
│   ├── api/                 # rotas (health, chat, memory, approvals, permissions, audit, system)
│   ├── computer/            # SystemController (stats, processos, abrir/encerrar apps) — Fase 5
│   ├── core/                # config, logging estruturado, enums (estados/permissões)
│   ├── db/                  # SQLAlchemy (Base, engine, sessão)
│   ├── models/              # Session / Message / Memory / governança (SQLAlchemy 2.x)
│   ├── schemas/             # modelos Pydantic base (inclui ToolCall/Declaration)
│   ├── services/            # chat, memory, summarizer, agent, approvals, permissions, audit
│   ├── tools/               # Tool Engine: base, registry, builtins (tempo, sistema, memória, computador)
│   └── main.py
└── tests/                   # pytest (Gemini 100% mockado)
```

## Tool Engine (Fase 3)

O modelo **propõe** chamadas de ferramenta (declaradas no registro); o **Core decide e executa**.
Nunca executa chamadas inventadas — só as registradas. Habilite o loop no chat com
`"tools": true` (retorna SSE). Ferramentas embutidas: `get_current_time`, `get_system_info`,
`store_memory`, `recall_memory`, `get_system_stats`, `list_processes`, `open_app`, `kill_process`
(Fase 3/5), `list_dir`/`read_file`/`write_file`/`make_dir`/`delete_path` (Fase 7) e
`dev_list_tools`/`dev_get_tool_schema`/`dev_get_config`/`dev_diagnostics` (Fase 8).

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
| `GET /health/ai` | healthcheck do Gemini (`ok` / `unconfigured` / `error` + `code`) |
| `GET /api/tts/ping` | disponibilidade da voz neural (Edge TTS) |
| `GET /api/tts/speech?text=...&split=` | prepara fala: `display_text` (intacto) + `speech_text` + `context` + `utterances` |
| `GET /api/tts?text=...` | MP3 falado (`audio/mpeg`; `voice` opcional) |
| `GET /api/device/info` | tipo de dispositivo detectado (`device` + `touch`) |
| `GET /health` | saúde da API + banco (SQLite) e device detectado pelo User-Agent |
| `GET /docs` | OpenAPI (Swagger UI) |

Envie `{"content": "...", "stream": true, "tools": true}` em
`POST /api/sessions/{id}/messages` para acionar o Tool Engine (SSE com eventos
`start → tool_start/tool_done → [approval_request → approval_pending] → chunk → done`).

- API + frontend: `http://127.0.0.1:8100` (o Atlas segue na 8000, não é alterado).
- WebSocket reservado na porta `8101` (contrato Core ↔ Local Agent — fase futura).

## Executar

Ver `SETUP.md` para o passo a passo completo (venv, `.env`, testes). Sem `GEMINI_API_KEY`,
o chat de stream responde com o evento `error` e o endpoint comum com `503` — o Core segue funcional.

## Roadmap (resumo)

0. Foundation ✔ · 1. Chat (backend + frontend) ✔ · 2. Memory/Context ✔ · 3. Tool Engine ✔ ·
4. Permissions ✔ · 5. Computer ✔ · 6. Voz (STT/TTS no navegador) ✔ · 6b. Voz (UX de fala: fila,
sanitização, provedores, SPEAKING) ✔ · 7. Filesystem ✔ · 8. Developer ✔ · 9. Mobile/Devices/PWA ✔ ·
10. Atlas · 11. Agent loop · 12. Web · 13. Visão · 14. Proativo · 15. Remote · 16. V1.

Cada fase termina funcional, testada, documentada e sem quebrar a anterior.