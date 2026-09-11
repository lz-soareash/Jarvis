/* =============================================================
   VEGA Desktop — configuração do cliente (Fase 22).

   Módulo NODE PURO (sem Electron) para ser testável de forma hermética.
   O app injeta um store `{ load():any, save(cfg):void }` (main.js usa
   config.json em userData). NUNCA guarda tokens: credenciais continuam
   em `device.json` (0600) via bridge.js.
   ============================================================= */
"use strict";

const DEFAULT_CORE_URL = "http://127.0.0.1:8100";
const DEFAULT_RELEASE_FEED = "https://api.github.com/repos/lz-soareash/Jarvis/releases/latest";
const DEFAULT_FIRST_RUN = true;
const DEFAULT_WAN_URL = "";

const DEFAULTS = {
  coreUrl: DEFAULT_CORE_URL,
  releaseFeedUrl: DEFAULT_RELEASE_FEED,
  firstRun: DEFAULT_FIRST_RUN,
  wanUrl: DEFAULT_WAN_URL,
};

function normalizeCoreUrl(value) {
  let url = String(value || "").trim();
  if (!url) return DEFAULT_CORE_URL;
  if (!/^https?:\/\//i.test(url)) url = `http://${url}`;
  url = url.replace(/\/+$/, "");
  return url;
}

// Fase 24 — WAN: aceita ws:// e wss:// (nunca http/https; o relé fala WebSocket).
function normalizeWanUrl(value) {
  const url = String(value || "").trim();
  if (!url) return DEFAULT_WAN_URL;
  if (!/^wss?:\/\//i.test(url)) return DEFAULT_WAN_URL;
  return url.replace(/\/+$/, "");
}

function sanitizeReleaseFeedUrl(value) {
  const url = String(value || "").trim();
  if (!/^https?:\/\//i.test(url)) return DEFAULT_RELEASE_FEED;
  return url.replace(/\/+$/, "");
}

function validateConfig(input) {
  const cfg = { ...DEFAULTS, ...(input || {}) };
  cfg.coreUrl = normalizeCoreUrl(cfg.coreUrl);
  cfg.releaseFeedUrl = sanitizeReleaseFeedUrl(cfg.releaseFeedUrl);
  cfg.firstRun = typeof cfg.firstRun === "boolean" ? cfg.firstRun : DEFAULT_FIRST_RUN;
  cfg.wanUrl = normalizeWanUrl(cfg.wanUrl);
  return cfg;
}

function loadConfig(store) {
  return validateConfig(store ? store.load() : null);
}

function saveConfig(store, partial) {
  const merged = validateConfig({ ...(store ? store.load() : null), ...(partial || {}) });
  if (store) store.save(merged);
  return merged;
}

module.exports = {
  DEFAULT_CORE_URL,
  DEFAULT_RELEASE_FEED,
  DEFAULT_FIRST_RUN,
  DEFAULT_WAN_URL,
  DEFAULTS,
  normalizeCoreUrl,
  normalizeWanUrl,
  sanitizeReleaseFeedUrl,
  validateConfig,
  loadConfig,
  saveConfig,
};