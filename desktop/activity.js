/* =============================================================
   VEGA Desktop — watch de atividade do agente (inspirado no padrão
   PowerSaveBlockerManager + grace de reconexão da Hydra).

   Quando o WAN gate está conectado, cada `agent_event` ou
   `message_result` recebido marca "atividade": o main.js mantém o
   wake-lock (powerSaveBlocker "prevent-app-suspension") para o
   Windows não suspender a máquina durante um turno do agente.

   Classificação POR GRACE, sem depender da lista de tipos de evento:
   - qualquer tráfego do agente == atividade (reset do timer de idle);
   - saída do estado `connected` == fim imediato;
   - sem atividade por `graceMs` == idle (libera o wake-lock).

   Módulo NODE PURO (testável com mock.timers do node:test).
   ============================================================= */
"use strict";

const DEFAULT_IDLE_GRACE_MS = 90000;
const IDLE = "idle";
const ACTIVE = "active";

function createActivityWatch(opts = {}) {
  const graceMs = opts.graceMs != null ? opts.graceMs : DEFAULT_IDLE_GRACE_MS;
  const onChange = opts.onChange || (() => {});
  let state = IDLE;
  let timer = null;

  function setState(next) {
    if (state === next) return;
    state = next;
    onChange(state === ACTIVE);
  }

  function clearTimer() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function refresh() {
    clearTimer();
    timer = setTimeout(() => {
      timer = null;
      setState(IDLE);
    }, graceMs);
    setState(ACTIVE);
  }

  return {
    get active() {
      return state === ACTIVE;
    },
    get state() {
      return state;
    },
    noteActivity() {
      refresh();
    },
    noteIdle() {
      clearTimer();
      setState(IDLE);
    },
    stop() {
      clearTimer();
      setState(IDLE);
    },
  };
}

module.exports = { createActivityWatch, DEFAULT_IDLE_GRACE_MS, IDLE, ACTIVE };