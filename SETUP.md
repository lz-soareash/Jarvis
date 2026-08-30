# SETUP — JARVIS Core

Guia de instalação e execução local (Windows). Requisitos: Python 3.13+.

## 1. Clonar e entrar no projeto

```bat
git clone https://github.com/lz-soareash/Jarvis.git
cd Jarvis
```

## 2. Ambiente virtual (Python 3.13)

```bat
py -3.13 -m venv .venv
.venv\Scripts\activate
```

> Em outros shells: `source .venv/bin/activate` (Linux/macOS).

## 3. Dependências

```bat
pip install -r requirements-dev.txt
```

(`requirements-dev.txt` inclui as de runtime. Para produção, basta `pip install -r requirements.txt`.)

## 4. Variáveis de ambiente

```bat
copy .env.example .env
```

Edite o `.env` e preencha:

- `GEMINI_API_KEY=` com sua chave do Google AI Studio (obrigatória para o `/health/ai` responder `ok`).
- As portas: API `8100`, WebSocket `8101`. O Atlas usa a `8000` — o JARVIS não a altera.
- Opcional: `MAX_CONTEXT_MESSAGES` (janela de mensagens ao Gemini; padrão `20`),
  `GEMINI_EMBED_MODEL` (embeddings; padrão `text-embedding-004`), `MEMORY_CONTEXT_LIMIT`,
  `SUMMARIZE_AFTER_MESSAGES` e `SUMMARY_CHUNK` (resumo rolante de conversas longas).
  Sem chave, a busca de memórias usa fallback lexical local e não há embeddings.

> Nunca versione o `.env` (o `.gitignore` já o exclui). Sem chave, o Core funciona todo,
> exceto chamadas de IA (devidamente reportado como `unconfigured`).

## 5. Executar o servidor

```bat
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8100
```

A API **e o frontend (chat)** sobem juntos em `http://127.0.0.1:8100/` (o Core serve os
estáticos de `frontend/` na raiz). É o único comando necessário para usar o JARVIS.

OpenAPI: http://127.0.0.1:8100/docs

Healthcheck:

```bat
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/health/ai
```

Teste rápido do chat (sem chave, o stream pode responder com o evento `error` — esperado):

```bat
curl -N -X POST http://127.0.0.1:8100/api/sessions/SEU_ID/messages ^
  -H "Content-Type: application/json" ^
  -d "{\"content\":\"oi\",\"stream\":true}"
```

Teste rápido da memória (Fase 2):

```bat
curl -X POST http://127.0.0.1:8100/api/memories ^
  -H "Content-Type: application/json" ^
  -d "{\"content\":\"gosto de café\",\"kind\":\"preference\"}"

curl "http://127.0.0.1:8100/api/memories?query=caf%C3%A9"
```

Teste rápido do Tool Engine (Fase 3) — precisa de chave para o loop:

```bat
curl -N -X POST http://127.0.0.1:8100/api/sessions/SEU_ID/messages ^
  -H "Content-Type: application/json" ^
  -d "{\"content\":\"guarde que gosto de café\",\"stream\":true,\"tools\":true}"
```

Teste rápido da política de permissões (Fase 4) — sem chave, via API:

```bat
REM lista os níveis efetivos
curl http://127.0.0.1:8100/api/permissions

REM exige aprovação para gravar memória (nível 2)
curl -X PUT http://127.0.0.1:8100/api/permissions/store_memory ^
  -H "Content-Type: application/json" ^
  -d "{\"permission_level\":2}"

REM pedidos aguardando decisão
curl http://127.0.0.1:8100/api/approvals/pending

REM trilho de auditoria
curl http://127.0.0.1:8100/api/audit
```

Teste rápido do Computer (Fase 5) — read-only, funciona até sem chave:

```bat
REM métricas do computador (CPU, memória, disco, boot)
curl http://127.0.0.1:8100/api/system/stats

REM processos em execução (limit 1-200)
curl "http://127.0.0.1:8100/api/system/processes?limit=5"

REM catálogo mostra as 4 ferramentas de computador (stats/processos níveis 0, open_app 1, kill_process 3)
curl http://127.0.0.1:8100/api/permissions
```

> `kill_process` só executa após aprovação explícita (nível 3, bloqueado por padrão). As rotas
> `GET /api/system/*` não executam nada destrutivo.

## 6. Recursos de voz (Fase 6 — só no navegador)

Nenhuma dependência nem mudança no servidor. No frontend (`http://127.0.0.1:8100/`):

- **Microfone (ditar):** o botão aparece no composer em Chromium/Safari (Web Speech API).
- **Alto-falante (ler respostas):** o botão de voz liga/desliga a leitura em voz alta das
  respostas do JARVIS; preferência salva em `localStorage`.
- Navegadores sem suporte simplesmente não exibem os botões.

## 7. Executar os testes

```bat
cd backend
..\.venv\Scripts\python -m pytest -v
```

### Testes e configuração

- Os testes forçam `GEMINI_API_KEY=""` e `DATABASE_URL=sqlite:///:memory:` (em `tests/conftest.py`),
  portanto **nunca** fazem chamadas reais à API nem tocam seu banco de dados real.
- Config do pytest em `backend/pytest.ini` (`asyncio_mode=auto` para testes async).

## 8. Estrutura do banco

- SQLite criado automaticamente no caminho de `DATABASE_URL` (padrão `backend/data/jarvis.db`) no startup.
- Fase 1: tabelas `sessions` e `messages` (chat). Fase 2: tabela `memories` (fato/preferência/nota/resumo)
  mais busca semântica por embeddings e resumo rolante de conversas longas. Fase 3: Tool Engine (registro
  de ferramentas, function calling no Gemini, loop do agente via SSE) sem novas tabelas. Fase 4:
  Permissions com as tabelas `approval_requests`, `tool_policies` e `audit_logs` (aprovação interativa
  nível ≥ 2, override de nível por ferramenta e trilho auditável). Fase 5: Computer — controle do
  computador via `SystemController` (PowerShell + stdlib, sem novas tabelas; stats/processos read-only,
  `open_app` nível 1 e `kill_process` nível 3). Tarefas, dispositivos e WebSocket
  (`8101`) entram nas fases seguintes — a migração p/ Postgres é troca de URL.
- Se subir versão com novas colunas, delete o `backend/data/jarvis.db` (dados de dev) ou migre manualmente.