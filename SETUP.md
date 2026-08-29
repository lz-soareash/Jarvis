# SETUP — JARVIS Core

Guia de instalação e execução local (Windows). Requisitos: Python 3.13+ e Node (apenas para fases futuras de frontend).

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

> Nunca versione o `.env` (o `.gitignore` já o exclui). Sem chave, o Core funciona todo,
> exceto chamadas de IA (devidamente reportado como `unconfigured`).

## 5. Executar o servidor

```bat
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8100
```

OpenAPI: http://127.0.0.1:8100/docs

Healthcheck:

```bat
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/health/ai
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

- Fase 0: SQLite criado automaticamente no caminho de `DATABASE_URL` (padrão `backend/data/jarvis.db`) no startup.
- Modelos de domínio (sessões, tarefas, dispositivos, auditoria...) entram nas fases seguintes,
  sempre via SQLAlchemy 2.x — a migração futura para PostgreSQL é troca de URL.