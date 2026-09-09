# VEGA Desktop — cliente fino do JARVIS Core

Client fino (Fase 21). **Não há IA aqui.** Toda a inteligência vive no Core
(`backend/` + `frontend/`) em `http://127.0.0.1:8100` — a janela do Electron
apenas o carrega na mesma origem.

## O que o cliente faz

1. **REGISTER** (`POST /api/remote/devices/register`) — cria um device
   `PENDING` com id emitido pelo servidor; o cliente nunca escolhe seu id.
2. **PAIRING** (`POST /api/remote/pairings` + `validate`) — ancora o registro
   pendente com a identidade declarada (platform `windows`, `client_version`,
   `capabilities`). O token gerado fica só no armazenamento local do app.
3. **HEARTBEAT** (`POST /api/remote/heartbeat`) — batida de vida a cada
   `heartbeat_seconds` devolvido pelo Core; `reconnect` ao reabrir e
   `disconnect` ao sair (best-effort).
4. **Central Ops** — o Core expõe o card Device Bridge (`/api/ops/overview`).

Não possui/duplica: AI Core, Memória, Permissões, Tool Registry, Computer
Agent, Proactive, Audit, autenticação própria.

## Pré-requisitos

- Node 20+ (ambiente Windows validado com Node 24).
- Core rodando com `REMOTE_ENABLED=true` **e** `DEVICE_ENABLED=true` (padrão
  desta instalação: `device_enabled=true`). Se o Core usará CORS, ver
  `REMOTE_CORS_ORIGINS`; a janela Electron carrega o Core na mesma origem.

## Rodar (dev)

```bash
cd desktop
npm install
npm start
```

Timeout/seguidor de Core diferente: `VEGA_CORE_URL` (ex.: se o backend escuta
nesta máquina em outra porta).

## Build `VEGA.exe` (Windows x64)

```bash
cd desktop
npm install
npm run dist:win
# resultado: desktop/dist/VEGA/VEGA.exe   (Electron 31, app-version 1.0.0)
```

O `dist/` é ignorado pelo Git (binário fora do repo). Ícone é gerado em
memória (PNG 16x16) — nenhum asset binário é versionado.