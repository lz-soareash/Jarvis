/* Fase 30 — watch de atividade do agente para wake-lock (sem Electron, herméticos).
   Usa mock.timers do node:test p/ validar o grace de idle sem espera real. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createActivityWatch, DEFAULT_IDLE_GRACE_MS, IDLE, ACTIVE } from "./activity.js";

test("começa idle e inativo", () => {
  const watch = createActivityWatch();
  assert.equal(watch.state, IDLE);
  assert.equal(watch.active, false);
});

test("noteActivity ativa e notify muda com a transição", () => {
  const changes = [];
  const watch = createActivityWatch({ onChange: (active) => changes.push(active) });
  watch.noteActivity();
  assert.equal(watch.active, true);
  assert.equal(watch.state, ACTIVE);
  assert.deepEqual(changes, [true]);
});

test("noteIdle libera imediatamente", () => {
  const changes = [];
  const watch = createActivityWatch({ onChange: (active) => changes.push(active) });
  watch.noteActivity();
  watch.noteIdle();
  assert.equal(watch.active, false);
  assert.deepEqual(changes, [true, false]);
});

test("grace expira e volta a idle sem nova atividade", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const changes = [];
  const watch = createActivityWatch({ graceMs: 1000, onChange: (active) => changes.push(active) });
  watch.noteActivity();
  assert.equal(watch.active, true);
  t.mock.timers.tick(1001);
  assert.equal(watch.active, false);
  assert.equal(watch.state, IDLE);
  assert.deepEqual(changes, [true, false]);
});

test("qualquer nova atividade reinicia o grace (turno longo)", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const watch = createActivityWatch({ graceMs: 1000 });
  watch.noteActivity();
  t.mock.timers.tick(900);
  assert.equal(watch.active, true, "ainda ativo antes do grace");
  watch.noteActivity(); // novo token: o timer de 1s reinicia
  t.mock.timers.tick(600);
  assert.equal(watch.active, true, "atividade renovou o grace");
  t.mock.timers.tick(500);
  assert.equal(watch.active, false, "expirou após o grace renovado");
});

test("noteActivity após idle volta a ativar", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const watch = createActivityWatch({ graceMs: 500 });
  watch.noteActivity();
  t.mock.timers.tick(501);
  assert.equal(watch.active, false);
  watch.noteActivity();
  assert.equal(watch.active, true);
});

test("stop limpa timer e libera", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const watch = createActivityWatch({ graceMs: 1000 });
  watch.noteActivity();
  watch.stop();
  assert.equal(watch.active, false);
  t.mock.timers.tick(5000); // sem NoRef/leak: não dispara nada
  assert.equal(watch.active, false);
});

test("DEFAULT_IDLE_GRACE_MS exportado (90s)", () => {
  assert.equal(DEFAULT_IDLE_GRACE_MS, 90000);
});