# VEGA Android — cliente fino do JARVIS Core (Fase 21 + Fase 22)

Cliente fino. **Não há IA aqui.** Toda a inteligência vive no Core
(`backend/` + `frontend/`). `VegaBridge.kt` espelha o mesmo contrato de API do
Desktop (`bridge.js`) sobre `HttpURLConnection` (sem Gson/Retrofit):
REGISTER → PAIRING → HEARTBEAT, com `device_id` emitido pelo servidor, token só
local e `claimed_device_id` validado.

A tela (`MainActivity.kt`, Jetpack Compose) mostra o estado real do pareamento e
conecta automaticamente quando há token salvo:
**● Conectado / ○ Reconectando… / ○ Servidor indisponível / ◌ Não conectado**,
além da versão do app (`BuildConfig.VERSION_NAME`).

## Visual (Fase 22)

Ícone adaptável **diamante on-dark** (`#05080f` + gradiente `#7ce8ff→#3aa8ff→
#2f6bff` + ponto `#2fe6a5`) em `app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml`
+ `drawable/ic_launcher_foreground.xml` + `values/ic_launcher_background.xml`.
Manifest: `android:icon="@mipmap/ic_launcher"`.

## Versão única

`app/build.gradle.kts` lê `../../VERSION` (raiz do repo) → `versionName` direto e
`versionCode` derivado (0.22.0 → 2200). GIT NUNCA contém keystore.

## Assinatura (release)

Keystore **fora do git**:

1. `cp keystore.properties.example keystore.properties` e preencha
   (`storeFile=keystore/vega-release.jks`). Gere a keystore com `keytool` do JDK 17.
2. Build:

```bash
cd android
./gradlew :app:assembleRelease   # Windows: gradlew.bat
# resultado: app/build/outputs/apk/release/app-release.apk
```

Assinatura v2 (validável com `apksigner verify --verbose app-release.apk`).
Sem `keystore.properties`, o release sai **sem assinatura** (unsigned). No CI
(GitHub Actions `build-android.yml`/`release.yml`) a assinatura usa secrets
`VEGA_KEYSTORE_BASE64`/`VEGA_KEYSTORE_PASSWORD`/`VEGA_KEYSTORE_KEY_ALIAS`/
`VEGA_KEYSTORE_KEY_PASSWORD` (o Gradle decodifica o B64 para `keystore/ci-release.jks`).

> **AES-256-GCM (credenciais WAN) ≠ RSA-2048 (assinatura do APK).** O keystore de
> release usa chave **RSA-2048** (`keytool -keyalg RSA -keysize 2048`)
> **exclusivamente para assinar o APK** (v2). As credenciais WAN do app
> (token/`device_id`) são protegidas com **AES-256-GCM** via Android Keystore
> (Seção "Segurança das credenciais WAN" abaixo). São dois mecanismos
> independentes; o RSA do keystore não criptografa nenhum token.

## Build debug (dev)

```bash
cd android
./gradlew :app:assembleDebug   # app/build/outputs/apk/debug/app-debug.apk
```

Exige JDK 17 + Android SDK `platforms;android-34` (`android/local.properties` →
`sdk.dir`, não versionado). Emulador usa `http://10.0.2.2:8100` como Core.

## Validação do APK de lançamento

```bash
sdk/build-tools/34.0.0/aapt.exe dump badging app-release.apk   # versionName/versionCode/label
sdk/build-tools/34.0.0/apksigner.bat verify --verbose --print-certs app-release.apk
```

## Conexão WAN — fora da LAN (Fase 24)

Quando o Core está fora da LAN local (ou atrás de CGNAT), o app pode falar com o
**MESMO AI Core** através do **relé WAN** (`backend/app/gateway/`, deploy separado)
— sem NAT/port-forwarding e sem segundo AI Core/Memória/Tools: o relé só transporta.

- Implementação: `VegaWan.kt` (OkHttp WebSocket; dependência única `okhttp` no
  `libs.versions.toml`). Estados `offline/connecting/connected/reconnecting/
  authentication_error/core_unavailable`, com heartbeat, reconexão com backoff e
  arquivamento do token só em `SharedPreferences`(`vega_wan`; o token NUNCA vai
  para URL/logs/SSE).
- UI: card **WAN** na `MainActivity` — URL do relé (`wss://…`) + **código de
  pareamento** digitável, com botões Conectar/Desconectar WAN e o estado ao vivo
  (device_id, heartbeat, token emitido pelo Core no `auth_result`).
- Bootstrap: envie `auth` com `pairing_code` + `device_name`; o Core devolve o
  `token` (emitido UMA vez) e tunables (`heartbeat_seconds`, `reconnect_enabled`,
  `message_timeout`, `queue_ttl`). Turnos de `message`, tarefas de computador e
  aprovações reusam o MESMO AI Core do pareamento local.
- Requisitos no Core: `REMOTE_GATEWAY_ENABLED=true` e `REMOTE_GATEWAY_PEER_TOKEN`
  igual ao `GATEWAY_PEER_TOKEN` do relé (ver `.env.example`). `wss://` exige proxy
  TLS (Caddy/nginx) na frente do relé.
- Build/Dependência: sem SDK local (JDK 8) o build roda no CI
  (`build-android.yml`, temurin 17, `:app:assembleRelease`).

### Segurança das credenciais WAN (Fases 27.2/27.2.1 — fail-closed)

- WAN token e `device_id` são protegidos com **AES-256-GCM** (IV 12 bytes, tag
  128 bits, `SecureRandom`) e a chave de 256 bits vive no **Android Keystore**
  (`WanSecretStore`, alias `vega_wan_key` — o material nunca é serializado para
  SharedPreferences e morre no factory-reset/desinstalação). O blob versionado
  `v1.` (Base64URL) fica em `SharedPreferences("vega_wan")` sob `enc.wan_token`/
  `enc.wan_device_id`.
- **Plaintext NUNCA é gravado, nem como fallback** (fail-closed). Keystore
  indisponível/falha → a gravação falha, nenhum segredo é armazenado e qualquer
  plaintext legado é **removido**; leituras devolvem nulo (nunca segredo
  parcial/corrompido) — após reiniciar, o aparelho volta a exigir pareamento.
- Migração: uma credencial em claro de versões anteriores só é lida na
  primeira leitura **com Keystore disponível** (lê → criptografa → apaga o
  plaintext). Se a migração falhar, o plaintext é removido (re-pareamento).
- Política decidida em `WanCredentialStore` (pura, testada em JVM, sem Android):
  `WanCiphers` só faz a criptografia; `WanSecretStore` só gerencia a chave.

### LAN vs WAN — endpoints independentes (bugfix)

O app **separa explicitamente** o endpoint LAN do endpoint WAN. Eles são
**independentes** e NUNCA são derivados um do outro:

```
LAN (Core):
    http://192.168.100.112:8100
    → funciona somente quando o dispositivo alcança o Core na rede local.

WAN (relé):
    wss://<PUBLIC_ENDPOINT>/api/remote/ws
    → funciona fora da rede local através do Relay/CoreLink.
```

- `WanEndpoint.kt` centraliza a validação do endpoint WAN (usada em
  `ChatViewModel.connectWan`, `VegaWan.connect` e na UI):
  - exige `wss://` em produção (`ws://` só é aceito em build **debug** contra um
    relé público de teste);
  - **rejeita IP privado** (`10.x`, `172.16–31.x`, `192.168.x`), localhost,
    `127.0.0.1`, `0.0.0.0`, link-local e IP não-público como endpoint WAN;
  - rejeita caminho incompatível com `/api/remote/ws`;
  - nunca guarda credenciais na URL.
- O **mesmo IP pode ser válido para LAN e inválido para WAN**: `http://192.168.x.x:8100`
  é um Core LAN legítimo, mas `ws://192.168.x.x:8200/...` NUNCA é um endpoint WAN.
- Sem WAN configurada o estado é **NÃO CONFIGURADO** (nunca cai para LAN como
  fallback). A tela Config/Central exibe os estados reais por canal:
  LAN conectado/indisponível e WAN configurado/não configurado/inválido/conectado/erro.
- `wss://<PUBLIC_ENDPOINT>/api/remote/ws` — o `<PUBLIC_ENDPOINT>` é configurado
  pelo ambiente (ex.: domínio com proxy TLS na frente do relé). Um **Quick Tunnel
  pode ser usado apenas para testes**; produção deve usar endpoint público estável.