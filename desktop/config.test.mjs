/* Fase 22 — configuração do cliente Desktop (sem Electron, herméticos). */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_CORE_URL,
  DEFAULT_RELEASE_FEED,
  normalizeCoreUrl,
  sanitizeReleaseFeedUrl,
  validateConfig,
  loadConfig,
  saveConfig,
} from "./config.js";

const fakeStore = (initial) => ({
  load: () => initial,
  save: (v) => {
    initial = v;
  },
});

test("normalizeCoreUrl: sem http:// vira http, remove barras", () => {
  assert.equal(normalizeCoreUrl("127.0.0.1:8100"), "http://127.0.0.1:8100");
  assert.equal(normalizeCoreUrl("http://core.local:9000/"), "http://core.local:9000");
  assert.equal(normalizeCoreUrl("  https://vega.example.com  "), "https://vega.example.com");
  assert.equal(normalizeCoreUrl(""), DEFAULT_CORE_URL);
});

test("sanitizeReleaseFeedUrl rejeita esquemas não-http", () => {
  assert.equal(sanitizeReleaseFeedUrl("javascript:alert(1)"), DEFAULT_RELEASE_FEED);
  assert.equal(sanitizeReleaseFeedUrl(""),
    DEFAULT_RELEASE_FEED);
  assert.equal(
    sanitizeReleaseFeedUrl("https://api.github.com/repos/x/Jarvis/releases/latest"),
    "https://api.github.com/repos/x/Jarvis/releases/latest"
  );
});

test("validateConfig aplica defaults e clamps", () => {
  const cfg = validateConfig({ coreUrl: "core.local:9000/" });
  assert.equal(cfg.coreUrl, "http://core.local:9000");
  assert.equal(cfg.releaseFeedUrl, DEFAULT_RELEASE_FEED);
  assert.equal(cfg.firstRun, true);
});

test("loadConfig mescla armazenado sobre defaults", () => {
  const store = fakeStore({ coreUrl: "http://10.0.2.2:8100", firstRun: false });
  const cfg = loadConfig(store);
  assert.equal(cfg.coreUrl, "http://10.0.2.2:8100");
  assert.equal(cfg.firstRun, false);
  assert.equal(cfg.releaseFeedUrl, DEFAULT_RELEASE_FEED);
});

test("saveConfig persiste merge normalizado", () => {
  const store = fakeStore({ coreUrl: "http://a:1" });
  const out = saveConfig(store, { firstRun: false });
  assert.equal(out.firstRun, false);
  const reloaded = loadConfig(store);
  assert.equal(reloaded.coreUrl, "http://a:1");
  assert.equal(reloaded.firstRun, false);
});