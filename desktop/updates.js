/* =============================================================
   VEGA Desktop — Auto-Update (Fase 22 §12 + Fase 30).

   Dois caminhos, sempre seguros e testáveis:
   * METADATA (dev / portable): consulta o "latest release" via GitHub API e
     compara com a versão local. NUNCA baixa nem executa binários.
   * FULL (instalador NSIS): o main.js usa `electron-updater` (agem no GitHub
     Release / latest.yml) para baixar em segundo plano e instalar no quit.
     Decisão de modo resolvida aqui, herméticamente.

   Módulo NODE PURO (testável com mock de `fetch`).
   ============================================================= */
"use strict";

const UPDATE_MODE_FULL = "full";
const UPDATE_MODE_METADATA = "metadata";

// Portátil NÃO atualiza por si (process.env.PORTABLE_EXECUTABLE_DIR só existe
// em runs do crate portable; electron-updater não instala nesse modo).
function resolveUpdateMode({ isPackaged, isPortable } = {}) {
  if (!isPackaged) return { mode: UPDATE_MODE_METADATA, reason: "dev" };
  if (isPortable) return { mode: UPDATE_MODE_METADATA, reason: "portable" };
  return { mode: UPDATE_MODE_FULL, reason: "nsis" };
}

function parseTag(tag) {
  const v = String(tag || "").trim().replace(/^v/, "");
  return /^\d+\.\d+\.\d+/.test(v) ? v : null;
}

function parseVersion(v) {
  const parts = String(v || "").trim().replace(/^v/, "").split(".");
  const nums = parts.slice(0, 3).map((p) => parseInt(p.replace(/\D.*$/, ""), 10) || 0);
  if (parts.length < 3 || nums.some(Number.isNaN)) return nums; // permissivo
  return nums;
}

function compareVersions(a, b) {
  const pa = parseVersion(a);
  const pb = parseVersion(b);
  for (let i = 0; i < 3; i++) {
    if (pa[i] < pb[i]) return -1;
    if (pa[i] > pb[i]) return 1;
  }
  return 0;
}

function classifyUpdate(currentVersion, latestVersion) {
  const latest = parseTag(latestVersion);
  const updateAvailable = latest !== null && compareVersions(currentVersion, latest) < 0;
  return { latest_version: latest, update_available: updateAvailable };
}

async function checkForUpdate({ currentVersion, releaseFeedUrl, fetchImpl } = {}) {
  const impl = fetchImpl || (typeof fetch === "function" ? fetch : null);
  const current = String(currentVersion || "").trim();
  const result = { current_version: current, latest_version: null, update_available: false, error: null };
  if (!impl || !current) {
    result.error = "fetcher indisponível";
    return result;
  }
  try {
    const res = await impl(releaseFeedUrl, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "vega-desktop" },
      redirect: "follow",
    });
    if (!res.ok) {
      result.error = `HTTP ${res.status}`;
      return result;
    }
    const data = await res.json();
    const tag = data && data.tag_name;
    if (!tag) {
      result.error = "resposta sem tag_name";
      return result;
    }
    const classified = classifyUpdate(current, tag);
    result.latest_version = classified.latest_version;
    result.update_available = classified.update_available;
    return result;
  } catch (err) {
    result.error = String(err && err.message ? err.message : err);
    return result;
  }
}

module.exports = {
  UPDATE_MODE_FULL,
  UPDATE_MODE_METADATA,
  resolveUpdateMode,
  parseTag,
  parseVersion,
  compareVersions,
  classifyUpdate,
  checkForUpdate,
};