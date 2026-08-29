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
| Frontend | HTML + CSS + JS Vanilla (PWA: manifest + service worker) |
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
`store_memory`, `recall_memory`, `get_system_stats`, `list_processes`, `open_app`, `kill_process`.

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
| `GET /health/ai` | healthcheck do Gemini (`ok` / `unconfigured` / `error`) |
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
4. Permissions ✔ · 5. Computer ✔ · 6. Filesystem · 7. Developer · 8. Mobile/Devices/PWA ·
9. Atlas · 10. Agent loop · 11. Web · 12. Voz · 13. Visão · 14. Proativo · 15. Remote · 16. V1.

Cada fase termina funcional, testada, documentada e sem quebrar a anterior.