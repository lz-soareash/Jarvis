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
│   ├── api/                 # rotas (health, chat) — cliente-agnóstico
│   ├── core/                # config, logging estruturado, enums (estados/permissões)
│   ├── db/                  # SQLAlchemy (Base, engine, sessão)
│   ├── models/              # Session / Message (memória operacional de sessão)
│   ├── schemas/             # modelos Pydantic base
│   ├── services/            # chat: sessões, contexto básico, respostas/SSE
│   └── main.py
└── tests/                   # pytest (Gemini 100% mockado)
```

## Endpoints

| Rota | Descrição |
|---|---|
| `GET /` | frontend (chat) — servido pelo Core na mesma porta |
| `POST /api/sessions` | cria sessão |
| `GET /api/sessions` | lista sessões |
| `GET/DELETE /api/sessions/{id}` | detalhe / exclusão |
| `GET /api/sessions/{id}/messages` | histórico da sessão |
| `POST /api/sessions/{id}/messages` | envia mensagem (JSON ou streaming SSE com `stream:true`) |
| `GET /health` | saúde da API + banco (SQLite) |
| `GET /health/ai` | healthcheck do Gemini (`ok` / `unconfigured` / `error`) |
| `GET /docs` | OpenAPI (Swagger UI) |

- API + frontend: `http://127.0.0.1:8100` (o Atlas segue na 8000, não é alterado).
- WebSocket reservado na porta `8101` (contrato Core ↔ Local Agent — fase futura).

## Executar

Ver `SETUP.md` para o passo a passo completo (venv, `.env`, testes). Sem `GEMINI_API_KEY`,
o chat de stream responde com o evento `error` e o endpoint comum com `503` — o Core segue funcional.

## Roadmap (resumo)

0. Foundation ✔ · 1. Chat (backend + frontend) ✔ · 2. Memory/Context · 3. Tool Engine ·
4. Permissions · 5. Computer · 6. Filesystem · 7. Developer · 8. Mobile/Devices/PWA ·
9. Atlas · 10. Agent loop · 11. Web · 12. Voz · 13. Visão · 14. Proativo · 15. Remote · 16. V1.

Cada fase termina funcional, testada, documentada e sem quebrar a anterior.