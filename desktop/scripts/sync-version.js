/* Fase 22 — sincroniza desktop/package.json com a VERSÃO central (raiz/VERSION).
   Fonte única: arquivo VERSION. Este script é idempotente e roda no CI antes
   do build (npm run sync:version). Saída: versão sem o prefixo "v". */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const PKG = path.join(__dirname, "..", "package.json");

function readRootVersion() {
  const raw = fs.readFileSync(path.join(ROOT, "VERSION"), "utf8").trim();
  return raw.replace(/^v/, "");
}

function isValidSemver(v) {
  return /^\d+\.\d+\.\d+$/.test(v);
}

function syncVersion() {
  const version = readRootVersion();
  if (!isValidSemver(version)) {
    throw new Error(`versão inválida em VERSION: "${version}" (esperado x.y.z)`);
  }
  const pkg = JSON.parse(fs.readFileSync(PKG, "utf8"));
  if (pkg.version !== version) {
    pkg.version = version;
    fs.writeFileSync(PKG, `${JSON.stringify(pkg, null, 2)}\n`, "utf8");
    console.log(`[sync-version] package.json ${pkg.version} -> ${version}`);
  } else {
    console.log(`[sync-version] package.json já em ${version}`);
  }
  // Fase 27.2.1 — o lockfile também carrega a versão raiz (top-level e
  // packages[""]); mantê-lo alinhado evita drift no CI (contrato do AGENTS.md).
  const lockPath = path.join(__dirname, "..", "package-lock.json");
  const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
  let lockChanged = false;
  if (lock.version !== version) {
    lock.version = version;
    lockChanged = true;
  }
  if (lock.packages && lock.packages[""] && lock.packages[""].version !== version) {
    lock.packages[""].version = version;
    lockChanged = true;
  }
  if (lockChanged) {
    fs.writeFileSync(lockPath, `${JSON.stringify(lock, null, 2)}\n`, "utf8");
    console.log(`[sync-version] package-lock.json -> ${version}`);
  } else {
    console.log(`[sync-version] package-lock.json já em ${version}`);
  }
  return version;
}

module.exports = { readRootVersion, isValidSemver };

if (require.main === module) {
  syncVersion();
}