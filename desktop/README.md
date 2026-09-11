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

## Distribuições (Windows x64 — Fase 22)

```bash
cd desktop
npm install
npm run dist
# resultado em desktop/dist/:
#   VEGA-<v>-win-x64.exe            instalador NSIS
#   VEGA-<v>-win-x64-portable.exe   executável portátil
#   VEGA-<v>-win-x64-portable.zip   pasta portátil zipada
```

- Versão vinda de `../VERSION` (raiz do repositório) — **fonte única**; `npm run
  sync:version` reescreve `package.json` e o teste `version.test.mjs` detecta drift.
- Após o pack, `scripts/after-pack.js` reaplica ícone + metadados VEGA com
  `@electron/rcedit` (o `winCodeSign` do builder exigiria symlinks — indisponível
  neste host). `VEGA.exe` final: ProductName=VEGA, FileVersion=`<v>`.
- `dist/` é ignorado pelo Git (binários fora do repo). Ícones `resources/icon.ico|png`
  são gerados por `scripts/gen_icons.py` (stdlib) e versionados.

## Configuração e primeiros passos (Fase 22)

- Primeiro uso abre a janela de **setup**: informe a URL do Core (padrão
  `http://127.0.0.1:8100`), conecte e veja o resultado; depois a janela principal
  abre e o app conecta automaticamente.
- `config.json` fica em `%APPDATA%\vega-desktop\` (`userData`); credenciais do
  pareamento em `device.json` (somente leitura do dono, 0600). Nenhum token é
  registrado em log.
- Tray: Abrir VEGA / Configurações / status / **Verificar atualizações** / Abrir
  Central / Sair. Verificação usa `updates.js` (metadata do GitHub Release — nunca
  faz download/executa); rede fora → linha de update mostra indisponível, sem erro.
- Single-instance: um segundo processo apenas foca a janela existente.

## Conexão WAN — fora da LAN (Fase 24)

Quando o Core está fora da LAN local (ou atrás de CGNAT), o cliente pode falar com
o MESMO AI Core através do **relé WAN** (`backend/app/gateway/`, deploy separado) —
sem NAT/port-forwarding e sem segundo AI Core/Memória/Tools: o relé só transporta.

- Implementação: `wan.js` (Node WebSocket puro, sem dependências novas; validado
  8/8 em `wan.test.mjs`). Mesmo vocabulário de estados do bridge:
  `offline / connecting / connected / reconnecting / authentication_error /
  core_unavailable`, com heartbeat e reconexão com backoff.
- Bootstrap: envie `auth` com o **código de pareamento** (gerado no Core via
  `POST /api/remote/pairings`) + `device_name`; o Core devolve `auth_result` com
  `token` (emitido UMA vez), `device_id` e tunables (`heartbeat_seconds`,
  `reconnect_enabled`, `message_timeout`, `queue_ttl`). O token fica só em
  `device.json` (0600) e nunca aparece em logs/URL/SSE.
- Turnos: `message` → `message_result` (e `agent_event` em streaming): a conversação
  reusa o MESMO AI Core/Computer Agent/fluxo de aprovações do Core.
- Requisitos no Core: `REMOTE_GATEWAY_ENABLED=true` e `REMOTE_GATEWAY_PEER_TOKEN`
  igual ao `GATEWAY_PEER_TOKEN` do relé (ver `.env.example`).
- `wss://` fora da LAN exige um proxy TLS (Caddy/nginx) na frente do relé
  (`ws://`). Validação de campo é manual (`MANUAL VALIDATION REQUIRED`).