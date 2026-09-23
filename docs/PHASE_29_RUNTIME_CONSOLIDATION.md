# FASE 29 — Consolidação do Runtime Remoto e Preparação para Validação Física

> **Validação em dispositivo físico NÃO realizada nesta fase.**
>
> A Fase 30 será reservada exclusivamente para um período prolongado de uso real
> do VEGA no dispositivo físico (Galaxy A15). Esta fase apenas consolida e
> prepara o sistema para que essa validação seja diagnóstica e sem surpresas.

- Baseline: `VEGA v0.25.4` (release publicado, commit `6107e9c`).
- Branch: `main`.
- Princípio norteador: **NÃO redesenhar a arquitetura**. Cada alteração exigiu:
  entender a implementação atual, localizar a origem do comportamento, verificar
  mecanismos equivalentes já existentes, reutilizar abstrações e fazer o menor
  delta possível.

---

## 1. Auditoria do Estado Atual

### 1.1 Backend (runtime remoto)

| Área | Estado | Evidência |
|---|---|---|
| Criação/renovação de sessão remota | OK — `touch_session`/`ensure_session_valid`; expiração **lazy** marca `ENDED` + evento `session.expired` | `app/remote/sessions.py:41-59,77-85,145-195` |
| Heartbeat WAN | OK — ACK roteável, rejeição honesta `ok:false` (revogado / not_authenticated), rate-limit | `app/remote/link.py:489-568` |
| Heartbeat HTTP | OK — eventos `connect/reconnect/heartbeat/disconnect` + `heartbeat_seconds=30` | `app/api/remote.py:451-510` |
| WebSocket (uma conexão por device) | OK — kick de duplicados, idle close 90s, close codes | `app/gateway/registry.py`, `server.py:27,81-85` |
| Reconexão (CoreLink antigo) | OK — backoff com jitter, flush do outbox, parada em revogação | `app/remote/agent.py:170-198,281-295,393-424` |
| Aprovações | OK — decisão idempotente (atomic), resposta 410 ao expirar | `app/services/approvals.py:97-130`, `app/api/approvals.py:64-65` |
| Timeouts de comandos móveis | OK — 15s com poll, fila com teto e sweep em memória | `app/ai/mobile_agent? / app/remote/link.py:1204-1233` |
| Erros HTTP | OK — 401/403/404/409/410/413 coerentes, `no-store`, payload guard chunked | `app/main.py:145-301`, `app/api/operations.py:34-42` |
| Segurança de logs | OK — zero vazamento de token/secret na varredura dos pacotes `app/remote`, `app/gateway`, `app/api` | — |

### 1.2 Android (cliente fino)

| Área | Estado | Evidência |
|---|---|---|
| Máquina de estados do chat | Turno sempre encerra em DONE/ERROR via `TurnLifecycle` (correlação + epoch) | `app/vega/client/model/TurnLifecycle.kt` |
| Input/Enviar | Bloqueado apenas enquanto houver mensagem SENDING/STREAMING | `ui/ChatScreen.kt:75,236` |
| Conexão LAN (Device Bridge) | heartbeat com backoff, re-auth em 401, estado `error` em 503 | `VegaBridge.kt:238-284` |
| Conexão WAN | handshake→auth→connected; backoff com jitter; half-open detectado por 3 heartbeats sem ACK | `VegaWan.kt:170-305,540-557` |
| Auth Bearer (Fase 28.1) | token no header `Authorization`, nunca em URL/logs | `net/VegaHttp.kt:43-50` |
| Timeouts | HTTP json 6s/20s; SSE entre-bytes; WAN heartbeat/handshake | `net/VegaHttp.kt:29-37`, `VegaWan.kt:149-153,282-287` |
| Lifecycle da Activity | Socket permanece ativo em background (design: receber comandos móveis) | `MainActivity.kt` |

### 1.3 Lacuna de estado provada (origem da correção principal)

**Caminho HTTP (LAN)**: se a conexão cai no meio da Stream, o OkHttp chama
`onFailure` → `TurnEvent.ErrorEvent` → a mensagem vira ERROR. O caminho tem
saída automática.

**Caminho WAN**: ao enviar, o Android correlaciona `request_id`, mas se o socket
cair **depois do envio** e antes da resposta, **nenhum** frame chega e nada falha
a mensagem — ela fica presa em `SENDING/STREAMING` indefinidamente, bloqueando o
Enviar e deixando a presença em "pensando…". Sem nenhum timer novo, a detecção JÁ
existente (máquina de estados WAN + heartbeat half-open) emite o evento de queda;
a partir daí o cliente precisa encerrar o turno.

---

## 2. Problemas Encontrados e Corrigidos

1. **[Backend] Aprovações expiradas continuavam "pendentes"**
   - `pending_for_session` listava pendências já expiradas; o decisor respondia
     sempre `410`. O mobile/operatório exibia um "pendente" que jamais poderia
     ser decidido (estado inconsistente).
   - Correção: filtro `expires_at > now` na fonte única de listagem
     (`app/services/approvals.py`, `pending_for_session`), usada tanto pela API
     `GET /api/approvals/pending` quanto pela lista de aprovações de operações
     remotas. Sem mudança de contrato: responder continua `410` para expiradas.
   - Teste: `test_pending_list_excludes_expired` (não lista; respond → 410).

2. **[Android] Turno WAN em voo preso quando o socket cai**
   - Encerramento idempotente e testável em `TurnLifecycle.failTurn(assistantId)`
     (remove a correlação, marca o request como terminado → frame tardio é
     descartado).
   - `ChatViewModel.handleTransportLoss`: ao detectar a transição da WAN
     `connected → não-connected` (reutiliza o toggle de estado existente no
     loop de 1s), encerra os turnos WAN em voo com
     "conexão WAN perdida durante a resposta" e libera a UI.
   - Testes: TurnLifecycleTest #18 (queda no meio do turno → ERROR + frame
     tardio ignorado) e #19 (idempotência/escopo do `failTurn`).

3. **[Android] Observabilidade objetiva do turno**
   - Logs em `vega-chat`: início de turno HTTP/WAN (com prefixo do request_id),
     conclusão e falha — sem nunca logar tokens/secrets.
   - Os eventos de transporte (state, heartbeat, erro) já eram logados em
     `vega-wan`/`vega`.

---

## 3. Problemas Encontrados e **Deliberadamente** Não Corrigidos

Mantidos de propósito para minimizar a superfície da Fase 29; cada um tem
proposta separada para as fases seguintes.

1. **GC periódico (sessões expiradas / outbox entregue) desligado em produção**
   - `expire_stale_sessions` e `purge_delivered` existem e são testados, mas só
     são chamados por testes; o `lifespan` não agenda limpeza. Sessões de 1h
     acumulam no SQLite.
   - Impacto: armazenamento/infra, **sem** inconsistência de comportamento
     (a expiração lazy `ensure_session_valid` mantém o contrato correto).
   - Proposta: agendar um `asyncio` task lento no lifespan de `app/main.py`.

2. **Sem timeout rígido de turno no cliente** (SSE `readTimeout` entre-bytes 0)
   - O servidor encerra todo turno legítimo (done/error); abortar um turno
     lento por timeout poderia matar operações de tools longas.
   - O caso de "blackhole silencioso" na LAN fica coberto pelo erro do socket
     (`onFailure`) e, na WAN, pelo half-open do heartbeat → `handleTransportLoss`.
   - Proposta: watchdog tunável (ex.: 5 min) se a Fase 30 observar stalls.

3. **Detecção de device morto é apenas observabilidade**
   - `_sweep_stale_heartbeats` publica `device_disconnected` mas conserva o
     binding. Comportamento é previsível e favorável à reconexão da Fase 30.
   - Proposta: marcar a sessão `ENDED` no dispositivo observado como offline.

4. **Estado `AUTHENTICATED` do enum `RemoteSessionStatus` não é atribuído**
   - Cosmético; a semântica real de "autenticada" recai no audit
     (`app/remote/auth.py`).

5. **Flake do `node --test --test-force-exit` no Desktop**
   - `UV_HANDLE_CLOSING` (libuv) no teardown em Windows; pré-existente, não
     relacionado a esta fase. Repetição da suíte passa sem falhas.

---

## 4. Decisões Técnicas

- Sem segunda máquina de estados: a Fase 29 **adapta** a nomenclatura existente
  (`MessageStatus.SENDING/STREAMING/DONE/ERROR` + `TurnLifecycle`), em vez de
  criar SUCCESS/ERROR/TIMEOUT/CANCELLED/DISCONNECTED novos. O mapa é:
  - `message_result ok:true` / `done` → **DONE**
  - `message_result ok:false` / `ErrorEvent` → **ERROR**
  - `send()` sem transporte → **ERROR** (banner + mensagem)
  - queda de transporte no meio do turno → **ERROR (DISCONNECTED)** pelo mesmo
    caminho de erro da mensagem
  - sem frame de cancelamento no protocolo → queda sempre encerra (não há
    estado CANCELLED no protocolo atual; documentado no teste #6).
- A detecção de queda da WAN **reutiliza** a máquina de estados + heartbeat
  half-open existentes — nenhum timer/loop novo foi criado.
- A listagem de aprovações não muda o contrato: só deixa de exibir o que seria
  garantidamente `410`.

---

## 5. Testes Executados

| Suíte | Resultado |
|---|---|
| Backend `pytest` (raiz `backend/`) | **1079 passed, 1 skipped** (Fase 28.1: 1078 → +1) |
| Android `testDebugUnitTest` | **153 testes, 0 falhas** (`TurnLifecycleTest`: 17 → 19) |
| Android `testReleaseUnitTest` | verde |
| Android `assembleDebug` / `assembleRelease` | verde |
| Desktop `npm test` | **32 testes, 0 falhas** (flake pré-existente documentado) |
| `git diff --check` | limpo (sem whitespace) |

---

## 6. Instruções para a Fase 30 (validação física no Galaxy A15)

1. **Preparação**: instalar o APK do release candidato da Fase 29
   (`VEGA-<version>-android.apk`, assinado v2); garantir o Core local com
   `REMOTE_ENABLED`/`DEVICE_ENABLED` habilitados e o relé WAN no ar.
2. **Conectar**: Config → LAN (URL do Core local) e WAN (`wss://...`).
3. **Cenários prioritários**:
   - Conversa simples e com tool móvel (cartão `mobile_command`).
   - Aprovação nível 2 (aceitar e negar).
   - **Wi-Fi → 4G/5G → Wi-Fi** durante uma resposta.
   - **Wi-Fi → sem rede → restaurada**, com e sem turno em voo.
   - **Core ligado → desligado → ligado**, com e sem turno em voo.
   - **Half-open WAN** (silenciar o tráfego por > 3 heartbeats).
   - Reinício do app no meio do turno e rotação de tela.
4. **Critério de passagem**: nenhuma mensagem fica presa em "pensando…"/"enviando…";
   ao cair a conexão, o turno encerra como erro visível e o input é liberado;
   o app reconecta sozinho quando a rede/Core volta.
5. **Coleta de evidências**: `adb logcat` filtrando as tags `vega-chat`,
   `vega-wan` e `vega` (eventos objetivos de início/conclusão/falha de turno,
   transições de estado e heartbeats). Nenhum token/segredo aparece nos logs.

---

## 7. Entrega da Fase 29

- Estado do repositório: verificado na execução dos gates; nenhum secret nos
  arquivos alterados.
- Commit desta fase: criado em `main` com alterações apenas de consolidação
  (backend + Android + testes + este documento). Nenhuma mudança arquitetural.
- **Release candidata à validação física**: como a Fase 30 instala o APK no
  dispositivo, esta fase bumpa `VERSION` (0.25.5) e dispara o release CI —
  produzindo o `VEGA-0.25.5-android.apk` (assinado v2), artefatos Windows e
  `SHA256SUMS.txt`, conforme a regra persistente do `AGENTS.md`.