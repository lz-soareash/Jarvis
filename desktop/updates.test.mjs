/* Fase 22 — fundação de auto-update (metadata) — NEVER baixa/executa binários. */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseTag,
  parseVersion,
  compareVersions,
  classifyUpdate,
  checkForUpdate,
} from "./updates.js";

test("parseTag aceita v0.22.0 e rejeita lixo", () => {
  assert.equal(parseTag("v0.22.0"), "0.22.0");
  assert.equal(parseTag("0.22.0"), "0.22.0");
  assert.equal(parseTag("0.22"), null);
  assert.equal(parseTag(undefined), null);
});

test("compareVersions ordena corretamente", () => {
  assert.equal(compareVersions("0.22.0", "0.22.0"), 0);
  assert.equal(compareVersions("0.22.0", "0.22.1"), -1);
  assert.equal(compareVersions("0.22.1", "0.23.0"), -1);
  assert.equal(compareVersions("0.23.0", "0.22.9"), 1);
  assert.equal(compareVersions("1.0.0", "0.99.9"), 1);
});

test("classifyUpdate marca update_available apenas quando há versão nova", () => {
  assert.equal(classifyUpdate("0.22.0", "v0.22.0").update_available, false);
  assert.equal(classifyUpdate("0.22.0", "0.22.0").update_available, false);
  assert.equal(classifyUpdate("0.22.0", "v0.23.0").update_available, true);
  assert.equal(classifyUpdate("0.22.0", "v0.23.0").latest_version, "0.23.0");
  assert.equal(classifyUpdate("0.22.0", undefined).update_available, false);
});

test("checkForUpdate usa o feed configurado e expõe metadata", async () => {
  let calledUrl = null;
  const fetchImpl = async (url) => {
    calledUrl = url;
    return { ok: true, json: async () => ({ tag_name: "v0.23.0" }) };
  };
  const out = await checkForUpdate({
    currentVersion: "0.22.0",
    releaseFeedUrl: "https://api.github.com/repos/x/Jarvis/releases/latest",
    fetchImpl,
  });
  assert.equal(calledUrl, "https://api.github.com/repos/x/Jarvis/releases/latest");
  assert.equal(out.current_version, "0.22.0");
  assert.equal(out.latest_version, "0.23.0");
  assert.equal(out.update_available, true);
  assert.equal(out.error, null);
});

test("checkForUpdate jamais falha em rede off: metadata segura", async () => {
  const fetchImpl = async () => {
    throw new Error("ENOTFOUND");
  };
  const out = await checkForUpdate({ currentVersion: "0.22.0", releaseFeedUrl: "x", fetchImpl });
  assert.equal(out.update_available, false);
  assert.equal(out.latest_version, null);
  assert.ok(out.error.includes("ENOTFOUND"));
});

test("checkForUpdate trata resposta não-2xx como indisponível", async () => {
  const fetchImpl = async () => ({ ok: false, status: 404 });
  const out = await checkForUpdate({ currentVersion: "0.22.0", releaseFeedUrl: "x", fetchImpl });
  assert.equal(out.update_available, false);
  assert.equal(out.error, "HTTP 404");
});