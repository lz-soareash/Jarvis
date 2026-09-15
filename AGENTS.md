# AGENTS.md

Instruções para agentes que trabalham neste repositório.

## Regra persistente — APK e GitHub Release

Sempre que uma mudança estiver relacionada ao APK Android (qualquer arquivo em
`android/**`, o arquivo `VERSION`, `android/app/build.gradle.kts` ou o pipeline de
release em `.github/**`), atualize também o GitHub Release:

1. Faça o bump da versão em `VERSION` (fonte única; semver `x.y.z`).
2. Sincronize a versão do Desktop: em `desktop/`, rode `npm run sync:version`
   (mantém `desktop/package.json` e `package-lock.json` alinhados a `VERSION`).
3. Garanta que os gates passem (backend, Android, Desktop) antes de publicar.
4. Commit em `main` e push.
5. Crie e envie a tag anotada correspondente: `git tag -a v<versao> -m "VEGA v<versao>"`
   e `git push origin v<versao>`.

A tag `v*` dispara `.github/workflows/release.yml`, que builda os artefatos
Windows e Android e publica o GitHub Release (`VEGA-<versao>-android.apk`,
`VEGA-<versao>-win-x64*`, `SHA256SUMS.txt`). O release só é criado se **ambos** os
jobs (windows e android) passarem. O APK não é versionado no git; ele existe
apenas como artefato do Release.

## Comandos verificados

- **Backend (pytest)** — a partir de `backend/`:
  `..\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --tb=short`
- **Android (unit tests)** — a partir de `android/`, com JDK 17:
  `gradle\wrapper\gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain testDebugUnitTest testReleaseUnitTest`
- **Desktop** — a partir de `desktop/`: `npm test`
- **Versão do Desktop** — a partir de `desktop/`: `npm run sync:version`
