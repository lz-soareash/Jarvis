# FASE 30 — Auto-Update do Desktop, Wake-Lock e Roteiro de Validação Física

- Baseline: `VEGA v0.25.5` (release publicado, commit `dd88465`).
- Branch: `main`.
- Empatia com o usuário: validação física (Galaxy A15) exige o aparelho **nas
  mãos do usuário**; esta fase entrega as melhorias de computação + o roteiro
  pronto para a Maratona de Validação (teste prolongado em campo).

---

## 1. Melhorias inspiradas na Hydra (launcher Electron open-source)

O `app.asar` da Hydra (`hydralauncher/hydra` v4.1.6) foi extraído fora do
repositório e analisado apenas como referência de padrões (PowerSaveBlocker,
DownloadOrchestrator com grace de reconexão, UpdateManager com electron-updater,
getFileIdentity, dupla via de transporte). Desta análise, **duas ideias** foram
escolhidas e implementadas no Desktop do VEGA:

1. **Wake-lock durante turnos do agente** — padrão `PowerSaveBlockerManager` da
   Hydra. O VEGA usa `powerSaveBlocker.start("prevent-app-suspension")` enquanto
   há atividade de agente via WAN gate, evitando que o Windows suspenda a máquina
   no meio de um turno remoto. Liberação **por grace** (estilo
   `NO_INTERNET_GRACE_MS`/`RECONNECT_DEBOUNCE_MS` da Hydra): `agent_event` e
   `message_result` marcam atividade; sem tráfego por 90s (ou saída do estado
   `connected`) libera.
2. **Auto-update real (instalador NSIS)** — padrão `UpdateManager` da Hydra
   (electron-updater + app-update.yml). O Desktop passa a ter **dois modos**:
   - `metadata` (dev/portátil): só consulta o GitHub API (`updates.js`, já
     existente; nunca baixa/executa).
   - `full` (instalador NSIS empacotado): `electron-updater` baixa em segundo
     plano (`latest.yml` do GitHub Release) e instala no quit; item no tray
     "Reiniciar para instalar atualização" quando baixado.

Bônus: `VEGA_CORE_URL` (env) agora **vence** a config salva do Core URL
(overrides, não fica gravado), alinhando o código ao que já estava no README.

### 1.1 Arquivos tocados (Desktop)

| Arquivo | Mudança |
|---|---|
| `desktop/activity.js` + `activity.test.mjs` | Novo `ActivityWatch` (idle grace 90s), testado com `mock.timers` |
| `desktop/main.js` | `powerSaveBlocker` + wiring `onAgentEvent`/`onMessageResult`/`onState`; updater por modo; env override |
| `desktop/updates.js` + testes | `resolveUpdateMode` (full vs metadata) |
| `desktop/config.js` + testes | `applyEnvOverrides` (`VEGA_CORE_URL`) |
| `desktop/package.json` | dep `electron-updater@^6.6.2`; `build.publish` (github `lz-soareash/Jarvis`); `files` +`activity.js`; `dist` com `--publish never` (artefatos publicados pelo workflow) |

### 1.2 Pipeline de Release (auto-update feed)

O instalador NSIS atualiza por si via GitHub Release, então o feed precisa do
`latest.yml` + blockmap no próprio Release:

| Arquivo | Mudança |
|---|---|
| `.github/actions/build-windows/action.yml` | valida e faz stage de `latest.yml` e `VEGA-<v>-win-x64.exe.blockmap` |
| `.github/workflows/release.yml` | flatten do `latest.yml`; upload de `latest.yml` + `*.exe.blockmap` no Release |

Build local confirmado: `dist/latest.yml` (sha512 do NSIS), `dist/*.blockmap` e
`resources/app-update.yml` (`owner: lz-soareash`, `repo: Jarvis`, `provider: github`)
são emitidos por `npm run dist`.

---

## 2. Decisões Técnicas

- **Classificação de atividade SEM lista de tipos de evento**: qualquer
  `agent_event` (streams em voo) ou `message_result` (fim do turno) conta como
  atividade; o grace de 90s cobre o fim do turno sem precisar saber quais tipos
  são terminais. Simples, robusto a mudanças de protocolo, e cobre o valor real
  (não suspender durante turno).
- **Portátil nunca auto-atualiza**: `electron-updater` não suporta instalar em
  modo portable com segurança; por isso o portátil mantém o caminho `metadata`.
- **`--publish never` no `dist`**: o build gera `latest.yml`/`app-update.yml`
  localmente; o workflow sobe esses arquivos no GitHub Release manualmente (o
  padrão de upload já existia). Evita que o job `windows` tente publicar antes
  do Release existir.
- **Sem segunda fonte de versão**: `VERSION` (raiz) continua a única fonte;
  `npm run sync:version` mantém `package.json`/lock alinhados.

---

## 3. Testes Executados (gates antes de publicar)

| Suíte | Resultado |
|---|---|
| Backend `pytest` (raiz `backend/`) | **1079 passed, 1 skipped** |
| Android `testDebugUnitTest` / `testReleaseUnitTest` | **153 testes, 0 falhas** |
| Desktop `npm test` | **42 testes, 0 falhas** (bridge 6, version 3, updates 7, config 6, wan 12, activity 8) |
| Build Desktop local `npm run dist` | ok — `latest.yml`, blockmap, `app-update.yml` emitidos |
| `git diff --check` | limpo (sem whitespace) |

---

## 4. Roteiro da Maratona de Validação Física (Galaxy A15)

> **Não executado nesta fase** — requer o celular nas mãos do usuário. Use o
> release candidato desta fase (`VEGA-0.25.6-android.apk`, assinado v2).

### 4.1 Preparação
1. Instalar o APK `VEGA-0.25.6-android.apk` no Galaxy A15.
2. Core local no ar, com `REMOTE_ENABLED`/`DEVICE_ENABLED` habilitados e relé
   WAN (`wss://...`) ativo (igual à Fase 29 §6.1).
3. Parear LAN (URL do Core local) e WAN (endpoint `wss://`).

### 4.2 Cenários (ordem de prioridade)
- [ ] Conversa simples e com tool móvel (cartão `mobile_command`).
- [ ] Aprovação nível 2 (aceitar e negar).
- [ ] **Wi-Fi → 4G/5G → Wi-Fi** durante uma resposta (turno em voo deve encerrar
      como ERROR e liberar o input — correção principal da Fase 29).
- [ ] **Wi-Fi → sem rede → restaurada**, com e sem turno em voo.
- [ ] **Core ligado → desligado → ligado**, com e sem turno em voo.
- [ ] **Half-open WAN** (silenciar tráfego > 3 heartbeats → reconexão).
- [ ] Reinício do app no meio do turno e rotação de tela.
- [ ] Turno longo (> 5 min, via WAN): conferir que o PC **não suspende** durante
      o turno (wake-lock do Desktop Fase 30).
- [ ] **Auto-update (Windows)**: com o instalador NSIS v0.25.5 rodando, publicar
      v0.25.6 e conferir "Verificar atualizações" → baixa em segundo plano →
      "Reiniciar para instalar atualização" → app abre em v0.25.6.

### 4.3 Critério de passagem
- Nenhuma mensagem fica presa em "pensando…"/"enviando…".
- Ao cair a conexão, o turno encerra como erro visível e o input é liberado.
- O app reconecta sozinho quando a rede/Core volta.
- O installador NSIS atualiza a si mesmo pelo GitHub Release.

### 4.4 Coleta de evidências
- `adb logcat` filtrando `vega-chat`, `vega-wan`, `vega` (eventos objetivos de
  início/conclusão/falha de turno, transições e heartbeats). Nenhum token/secret
  aparece nos logs.
- No Desktop, conferir no terminal o log `[vega] wake-lock ativo/liberado`.

---

## 5. Entrega da Fase 30

- Estado do repositório: verificado na execução dos gates; nenhum secret nos
  arquivos alterados.
- Commit criado em `main`; `VERSION` bump → **0.25.6**; tag `v0.25.6` dispara o
  release CI (jobs windows/android/backend-tests + release) que publica o APK,
  instalador NSIS (com `latest.yml` + blockmap) e checksums.
- A validação física (Maratona) fica para o usuário, seguindo o roteiro §4.