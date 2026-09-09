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