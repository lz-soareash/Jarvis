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

## 6. Executar os testes

```bat
cd backend
..\.venv\Scripts\python -m pytest -v
```

### Testes e configuração

- Os testes forçam `GEMINI_API_KEY=""` e `DATABASE_URL=sqlite:///:memory:` (em `tests/conftest.py`),
  portanto **nunca** fazem chamadas reais à API nem tocam seu banco de dados real.
- Config do pytest em `backend/pytest.ini` (`asyncio_mode=auto` para testes async).

## 7. Estrutura do banco

- SQLite criado automaticamente no caminho de `DATABASE_URL` (padrão `backend/data/jarvis.db`) no startup.
- Fase 1: tabelas `sessions` e `messages` (chat). Fase 2: tabela `memories` (fato/preferência/nota/resumo)
  mais busca semântica por embeddings e resumo rolante de conversas longas. Tarefas, dispositivos, auditoria
  e WebSocket (`8101`) entram nas fases seguintes, sempre via SQLAlchemy 2.x — a migração p/ Postgres é troca de URL.
- Se subir versão com novas colunas, delete o `backend/data/jarvis.db` (dados de dev) ou migre manualmente.