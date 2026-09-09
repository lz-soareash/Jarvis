/* =============================================================
   VEGA Desktop — fundação de Auto-Update (Fase 22, §12).

   SOMENTE METADADOS: este módulo consulta o "latest release" do repositório
   (GitHub API) e compara com a versão local. NUNCA baixa nem executa
   binários — a instalação/atualização real é etapa futura, controlada.

   Módulo NODE PURO (testável com mock de `fetch`).
   ============================================================= */
"use strict";

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
  parseTag,
  parseVersion,
  compareVersions,
  classifyUpdate,
  checkForUpdate,
};