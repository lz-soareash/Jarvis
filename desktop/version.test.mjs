/* Fase 22 — versão central: raiz/VERSION é a ÚNICA fonte, package.json segue. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const version = readFileSync(path.join(root, "VERSION"), "utf8").trim();
const pkg = JSON.parse(readFileSync(path.join(root, "desktop", "package.json"), "utf8"));
const { readRootVersion, isValidSemver } = await import("./scripts/sync-version.js");

test("VERSION central é semver x.y.z", () => {
  assert.ok(isValidSemver(version), `versão inválida: ${version}`);
});

test("desktop/package.json sincronizado com a fonte única", () => {
  assert.equal(pkg.version, version);
});

test("readRootVersion lê a raiz/VERSION sem prefixo v", () => {
  assert.equal(readRootVersion(), version);
});