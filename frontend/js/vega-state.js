/* =============================================================
   VEGA — UI State Mapping (Fase 19.6)
   backend/evento real → estado semântico → estado visual.

   Fonte única do mapa de estados da interface: nenhuma string
   arbitrária deve espalhar estados pela aplicação. Módulo PURO
   (sem DOM) para poder ser testado em Node e usado no browser.

   Estados sustentados por sinais reais (nada de estado fabricado):
     - presença (/api/vega/state): idle · thinking · working ·
       waiting_confirmation · error · offline
     - voz: listening (microfone) · speaking (TTS)
     - chat SSE: executing (tool_start) · thinking · success (done) · error
     - Computer Agent (eventos reais computer.* / status de tarefa)
   ============================================================= */
"use strict";

const VEGA_STATES = Object.freeze({
  offline: { orb: "offline", label: "off-line" },
  error: { orb: "error", label: "erro" },
  idle: { orb: "idle", label: "ociosa" },
  listening: { orb: "listening", label: "ouvindo" },
  thinking: { orb: "thinking", label: "pensando" },
  working: { orb: "working", label: "operando" },
  perceiving: { orb: "perceiving", label: "percebendo" },
  planning: { orb: "planning", label: "planejando" },
  observing: { orb: "observing", label: "observando" },
  executing: { orb: "executing", label: "executando" },
  verifying: { orb: "verifying", label: "verificando" },
  recovering: { orb: "recovering", label: "recuperando" },
  waiting_confirmation: { orb: "waiting_confirmation", label: "aguardando confirmação" },
  speaking: { orb: "speaking", label: "falando" },
  success: { orb: "success", label: "concluída" },
  warning: { orb: "warning", label: "atenção" },
});

/* Alias: valores observados na prática que se resolvem para um estado canônico. */
const STATE_ALIASES = Object.freeze({
  online: "idle",
  connecting: "idle",
  ready: "idle",
  listening_to_voice: "listening",
  error_occurred: "error",
});

function canonicalState(input) {
  const s = String(input ?? "idle").toLowerCase().trim();
  const resolved = STATE_ALIASES[s] || s;
  return Object.prototype.hasOwnProperty.call(VEGA_STATES, resolved) ? resolved : "idle";
}

function stateMeta(input) {
  const canonical = canonicalState(input);
  return {
    state: canonical,
    orb: VEGA_STATES[canonical].orb,
    label: VEGA_STATES[canonical].label,
  };
}

/* Presença real da VEGA (/api/vega/state): presence.state é um estado
   semântico emitido pelo backend (idle/thinking/working/...); presence.label
   é o texto legível. Preferimos o state canônico. */
function resolvePresence(presence) {
  if (!presence || typeof presence !== "object") return "idle";
  return canonicalState(presence.state || presence.status || presence.label || "idle");
}

const COMPUTER_EVENT_TO_STATE = Object.freeze({
  "computer.task.planned": "planning",
  "computer.observation.received": "perceiving",
  "computer.action.requested": "waiting_confirmation",
  "computer.action.executed": "executing",
  "computer.action.failed": "error",
  "computer.verification.started": "verifying",
  "computer.verification.success": "success",
  "computer.verification.failed": "verifying",
  "computer.recovery.started": "recovering",
  "computer.recovery.completed": "recovering",
  "computer.task.waiting_confirmation": "waiting_confirmation",
  "computer.task.completed": "success",
  "computer.task.failed": "error",
  "computer.task.cancelled": "idle",
  "computer.loop.prevented": "warning",
  "computer.security.prompt_injection": "warning",
  "computer.security.autonomy_blocked": "warning",
});

function resolveComputerEvent(eventType) {
  const s = COMPUTER_EVENT_TO_STATE[String(eventType || "")];
  return s ? canonicalState(s) : null;
}

/* Status de tarefa real (computer.task.status): planning/perceiving/executing/
   observing/verifying/recovering/waiting_confirmation. */
const COMPUTER_TASK_TO_STATE = Object.freeze({
  planning: "planning",
  perceiving: "perceiving",
  executing: "executing",
  observing: "observing",
  verifying: "verifying",
  recovering: "recovering",
  waiting_confirmation: "waiting_confirmation",
});

function resolveComputerTask(status) {
  const s = COMPUTER_TASK_TO_STATE[String(status ?? "").toLowerCase()];
  return s ? canonicalState(s) : null;
}

const VegaState = {
  VEGA_STATES,
  STATE_ALIASES,
  COMPUTER_EVENT_TO_STATE,
  COMPUTER_TASK_TO_STATE,
  canonicalState,
  stateMeta,
  resolvePresence,
  resolveComputerEvent,
  resolveComputerTask,
};

if (typeof window !== "undefined") window.VegaState = VegaState;
if (typeof module !== "undefined" && module.exports) module.exports = VegaState;