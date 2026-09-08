import test from "node:test";
import assert from "node:assert/strict";
import VegaState from "../js/vega-state.js";

test("estados canônicos cobrem todos os estados sustentados", () => {
  const expected = [
    "offline",
    "error",
    "idle",
    "listening",
    "thinking",
    "working",
    "perceiving",
    "planning",
    "observing",
    "executing",
    "verifying",
    "recovering",
    "waiting_confirmation",
    "speaking",
    "success",
    "warning",
  ];
  for (const s of expected) {
    assert.equal(VegaState.canonicalState(s), s, `${s} deve ser canônico`);
  }
});

test("todos os estados têm rótulo legível (os estados críticos precisam de texto, não só cor)", () => {
  for (const [state, meta] of Object.entries(VegaState.VEGA_STATES)) {
    assert.ok(meta.label && meta.label.length > 0, `${state} precisa de label`);
    assert.ok(meta.orb && meta.orb.length > 0, `${state} precisa de orb`);
  }
});

test("estados desconhecidos/aliases resolvem com segurança", () => {
  assert.equal(VegaState.canonicalState(undefined), "idle");
  assert.equal(VegaState.canonicalState(null), "idle");
  assert.equal(VegaState.canonicalState(""), "idle");
  assert.equal(VegaState.canonicalState("EM_EXECUCAO"), "idle");
  assert.equal(VegaState.canonicalState("online"), "idle");
  assert.equal(VegaState.canonicalState("LISTENING"), "listening");
  assert.equal(VegaState.canonicalState("Waiting_Confirmation"), "waiting_confirmation");
});

test("resolvePresence mapeia os estados reais do backend", () => {
  const cases = {
    idle: "idle",
    thinking: "thinking",
    working: "working",
    waiting_confirmation: "waiting_confirmation",
    error: "error",
    offline: "offline",
  };
  for (const [from, to] of Object.entries(cases)) {
    assert.equal(VegaState.resolvePresence({ state: from }), to, `${from} => ${to}`);
    assert.equal(VegaState.resolvePresence({ status: from }), to, `status ${from} => ${to}`);
  }
  assert.equal(VegaState.resolvePresence({ state: "unknown_thing" }), "idle");
  assert.equal(VegaState.resolvePresence(null), "idle");
});

test("resolveComputerEvent usa somente eventos reais do Computer Agent", () => {
  assert.equal(VegaState.resolveComputerEvent("computer.task.planning"), null, "não é evento real");
  assert.equal(VegaState.resolveComputerEvent("computer.task.planned"), "planning");
  assert.equal(VegaState.resolveComputerEvent("computer.action.executed"), "executing");
  assert.equal(VegaState.resolveComputerEvent("computer.action.requested"), "waiting_confirmation");
  assert.equal(VegaState.resolveComputerEvent("computer.task.waiting_confirmation"), "waiting_confirmation");
  assert.equal(VegaState.resolveComputerEvent("computer.verification.success"), "success");
  assert.equal(VegaState.resolveComputerEvent("computer.task.failed"), "error");
  assert.equal(VegaState.resolveComputerEvent("computer.loop.prevented"), "warning");
  assert.equal(VegaState.resolveComputerEvent("computer.task.completed"), "success");
  assert.equal(VegaState.resolveComputerEvent(undefined), null);
});

test("resolveComputerTask mapeia status reais de tarefa sem fabricar estados", () => {
  assert.equal(VegaState.resolveComputerTask("planning"), "planning");
  assert.equal(VegaState.resolveComputerTask("perceiving"), "perceiving");
  assert.equal(VegaState.resolveComputerTask("executing"), "executing");
  assert.equal(VegaState.resolveComputerTask("observing"), "observing");
  assert.equal(VegaState.resolveComputerTask("verifying"), "verifying");
  assert.equal(VegaState.resolveComputerTask("recovering"), "recovering");
  assert.equal(VegaState.resolveComputerTask("waiting_confirmation"), "waiting_confirmation");
  assert.equal(VegaState.resolveComputerTask("completed"), null, "concluída não é estado de atividade");
  assert.equal(VegaState.resolveComputerTask("failed"), null);
  assert.equal(VegaState.resolveComputerTask(undefined), null);
});

test("stateMeta devolve orb + label consistentes", () => {
  const m = VegaState.stateMeta("executing");
  assert.equal(m.state, "executing");
  assert.equal(m.orb, "executing");
  assert.equal(m.label, "executando");
});