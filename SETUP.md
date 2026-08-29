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
- Opcional: `MAX_CONTEXT_MESSAGES` (janela de mensagens enviadas ao Gemini por resposta; padrão `20`).

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
- Fase 1: tabelas `sessions` e `messages` (chat). Modelos de tarefas, dispositivos, auditoria e WebSocket
  (`8101`) entram nas fases seguintes, sempre via SQLAlchemy 2.x — a migração para PostgreSQL é troca de URL.