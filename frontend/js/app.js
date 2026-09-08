/* =============================================================
   JARVIS — Frontend Vanilla JS (chat + sessões + status + SSE)
   Sem frameworks; PWA-friendly.
   ============================================================= */
"use strict";

const els = {
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  sessionsPanel: document.getElementById("sessions-panel"),
  sessionsList: document.getElementById("sessions-list"),
  sessionsBackdrop: document.getElementById("sessions-backdrop"),
  menuToggle: document.getElementById("menu-toggle"),
  newSession: document.getElementById("new-session"),
  chatWindow: document.getElementById("chat-window"),
  emptyState: document.getElementById("empty-state"),
  orb: document.getElementById("orb"),
  messages: document.getElementById("messages"),
  inputForm: document.getElementById("input-form"),
  input: document.getElementById("input"),
  sendBtn: document.getElementById("send-btn"),
  voiceBtn: document.getElementById("voice-btn"),
  ttsToggle: document.getElementById("tts-toggle"),
  wakeBtn: document.getElementById("wake-btn"),
  composerHint: document.getElementById("composer-hint"),
  voiceStatus: document.getElementById("voice-status"),
  agentState: document.getElementById("agent-state"),
  dbState: document.getElementById("db-state"),
  aiState: document.getElementById("ai-state"),
  installBtn: document.getElementById("install-btn"),
};

let deferredInstallPrompt = null;

let currentSessionId = null;
let streaming = false;

/* ---------- drawer de sessões (mobile/tablet, UI-only) ---------- */
const isDrawerLayout = () => window.matchMedia("(max-width: 900px)").matches;

function toggleDrawer(open) {
  const will = open !== undefined ? open : !els.sessionsPanel.classList.contains("is-open");
  els.sessionsPanel.classList.toggle("is-open", will);
  els.sessionsBackdrop.hidden = !will;
  els.menuToggle.setAttribute("aria-expanded", String(will));
}

function closeDrawerOnMobile() {
  if (isDrawerLayout()) toggleDrawer(false);
}

/* ---------- helpers ---------- */
async function apiJSON(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = `Erro ${res.status}`;
    try {
      const data = await res.json();
      if (data && data.detail) detail = data.detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function appendMessageDOM(role, content, meta = "") {
  els.emptyState.hidden = true;
  if (els.messages.hidden) els.messages.hidden = false;

  const msg = document.createElement("div");
  msg.className = `msg msg-${role}`;
  if (role === "assistant") {
    const body = document.createElement("div");
    body.className = "msg-body";
    if (window.Markdown) {
      window.Markdown.render(body, content);
    } else {
      body.textContent = content;
    }
    msg.appendChild(body);
  } else {
    msg.textContent = content;
  }
  if (meta) {
    const time = document.createElement("span");
    time.className = "msg-meta";
    time.textContent = meta;
    msg.appendChild(time);
  }
  els.messages.appendChild(msg);
  scrollChatToBottom();
  return msg;
}

function lastBubble() {
  return els.messages.lastElementChild;
}

function setTyping(on) {
  const el = lastBubble();
  if (on) {
    el.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    el.classList.remove("msg-error");
    streaming = true;
    els.sendBtn.disabled = true;
    els.input.disabled = true;
  } else {
    const node = document.querySelector(".typing");
    if (node) node.remove();
    streaming = false;
    els.sendBtn.disabled = false;
    els.input.disabled = false;
    els.input.focus();
  }
}

function scrollChatToBottom() {
  if (els.chatWindow) els.chatWindow.scrollTop = els.chatWindow.scrollHeight;
}

function formatTime(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

/* ---------- Orb central (Fase 4) — estados visuais ----------
   Estados: idle · listening · thinking · executing · speaking · error.
   Comunica o estado atual do JARVIS visualmente (aria-hidden, decorativo). */
let orbErrorTimer = null;

/* Erro pontual de envio — borda danger temporária no composer-field (§13) */
let composerErrTimer = null;

function flashComposerError() {
  const field = els.inputForm && els.inputForm.querySelector(".composer-field");
  if (!field) return;
  field.classList.add("is-error");
  clearTimer(composerErrTimer);
  composerErrTimer = setTimeout(() => {
    composerErrTimer = null;
    field.classList.remove("is-error");
  }, 2600);
}

function setOrbState(state) {
  const el = els.orb;
  if (!el || el.dataset.state === state) return;
    el.dataset.state = state;
    if (window.OpsView && window.OpsView.notifyOrb) window.OpsView.notifyOrb(state);
    if (state === "error") {
    clearTimer(orbErrorTimer);
    orbErrorTimer = setTimeout(() => {
      orbErrorTimer = null;
      if (el.dataset.state === "error") setOrbState("idle");
    }, 2600);
  }
}

/* ---------- voz (Fase 6/6b) — Web Speech API, 100% no navegador ----------
   Modo manual: botão de microfone preenche o campo (envio manual).
   Mãos-livres: escuta contínua por "Jarvis" como palavra-chave; ao ouvir,
   captura o comando seguinte e envia sozinho. */
const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
const voiceSupported = Boolean(SpeechRecognitionAPI);
const ttsSupported = typeof window.speechSynthesis !== "undefined";
const WAKE_REARM_DELAY = 450;
const CAPTURE_PAUSE = 300;
const SESSION_SWITCH_GUARD = 350;

let recognition = null;
let recActive = false;
let pendingStart = null;
let listening = false;
let manualDictation = false;
let handsFree = false;
let commanding = false;
let sessionStartTs = 0;
let wakeTimer = null;
let submitTimer = null;
let micHintTimer = null;
let voiceEnabled = localStorage.getItem("jarvis.tts") === "1";

const hintDefault = els.composerHint ? els.composerHint.textContent : "";

function showMicHint(msg) {
  if (!els.composerHint) return;
  els.composerHint.classList.add("is-err");
  els.composerHint.textContent = msg;
  clearTimer(micHintTimer);
  micHintTimer = setTimeout(() => {
    micHintTimer = null;
    els.composerHint.classList.remove("is-err");
    els.composerHint.textContent = hintDefault;
  }, 9000);
}

function handleMicBlocked() {
  recActive = false;
  pendingStart = null;
  handsFree = false;
  commanding = false;
  micDenied = true;
  setWakeVisual("off");
  setListeningVisual(false);
  showMicHint(
    "Microfone bloqueado: habilitar em Privacidade > Microfone (Windows) e no site do navegador."
  );
  console.warn("[voz] permissão de microfone negada");
  updateVoiceIndicators();
}

/* Fase 11.3 (#11) — indicadores de estado de voz no HUD:
   VOICE READY / VOICE UNAVAILABLE / VOICE MIC DENIED / SPEECH SYNTH UNAVAILABLE. */
function updateVoiceIndicators() {
  if (!els.voiceStatus) return;
  if (!voiceSupported) {
    els.voiceStatus.hidden = false;
    els.voiceStatus.className = "voice-badge is-err";
    els.voiceStatus.textContent = "VOZ INDISPONÍVEL";
    return;
  }
  const ttsAvailable =
    ttsSupported || Boolean(speech && speech.ttsSupported);
  const parts = [];
  let level = "is-ok";
  parts.push("VOZ OK");
  if (!ttsAvailable) {
    parts.push("SINT FALA INDISPONÍVEL");
    level = "is-warn";
  }
  if (micDenied) {
    parts.push("MIC NEGADO");
    level = "is-err";
  }
  els.voiceStatus.hidden = false;
  els.voiceStatus.className = "voice-badge " + level;
  els.voiceStatus.textContent = parts.join(" · ");
}

let micDenied = false;

function clearTimer(t) {
  if (t) clearTimeout(t);
  return null;
}

function wakeKeyword(text) {
  const words = text.trim().split(/\s+/);
  const idx = words.findIndex((w) => w.toLowerCase().replace(/[.,!;?]+$/g, "") === "jarvis");
  return idx === -1 ? null : idx;
}

function setListeningVisual(on) {
  listening = on;
  if (!els.voiceBtn) return;
  els.voiceBtn.classList.toggle("is-listening", on);
  els.voiceBtn.setAttribute("aria-pressed", String(on));
  els.voiceBtn.setAttribute("aria-label", on ? "Parar de falar" : "Falar com o Jarvis");
  els.voiceBtn.title = on ? "Parar" : "Falar";
  els.inputForm.classList.toggle("is-listening", on);
  setOrbState(on ? "listening" : "idle");
}

function setWakeVisual(mode) {
  if (!els.wakeBtn) return;
  const on = mode !== "off";
  els.wakeBtn.classList.toggle("is-on", on);
  els.wakeBtn.classList.toggle("is-speaking", mode === "speaking");
  els.wakeBtn.setAttribute("aria-pressed", String(on));
  els.wakeBtn.title =
    mode === "speaking"
      ? "Falando ao Jarvis..."
      : mode === "armed"
        ? "Ouvindo: diga olá Jarvis..."
        : "Mãos-livres (diga olá Jarvis)";
  els.inputForm.classList.toggle("is-listening", mode === "speaking");
  if (els.composerHint) {
    clearTimer(micHintTimer);
    els.composerHint.classList.remove("is-err");
    els.composerHint.classList.toggle("is-armed", mode === "armed");
    els.composerHint.classList.toggle("is-speaking-v", mode === "speaking");
    els.composerHint.textContent =
      mode === "armed"
        ? "Ouvindo: diga olá Jarvis..."
        : mode === "speaking"
          ? "Fale seu comando..."
          : hintDefault;
  }
}

function buildRecognizer() {
  const rec = new SpeechRecognitionAPI();
  const navLang = (navigator.language || "pt-BR").toLowerCase();
  rec.lang = navLang.startsWith("pt") || navLang.startsWith("en") ? navigator.language : "pt-BR";
  rec.interimResults = true;
  rec.continuous = true;

  rec.onresult = (e) => {
    let final = "";
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i += 1) {
      const r = e.results[i];
      if (r.isFinal) final += r[0].transcript;
      else interim += r[0].transcript;
    }
    const text = (final || interim).trim();
    if (!text) return;

    if (manualDictation) {
      els.input.value = text;
      autosizeArea();
      return;
    }
    if (!handsFree) return;

    if (commanding) {
      els.input.value = text;
      autosizeArea();
      scheduleCommandSubmit();
      return;
    }

    const k = wakeKeyword(final);
    if (k === null) return;
    const pieces = final.trim().split(/\s+/);
    const command = pieces.slice(k + 1).join(" ").trim();
    commanding = true;
    setWakeVisual("speaking");
    if (command) {
      els.input.value = command;
      autosizeArea();
      scheduleCommandSubmit();
    } else {
      startCaptureSession();
    }
  };

  rec.onend = () => {
    recActive = false;
    if (pendingStart) {
      const cfg = pendingStart;
      pendingStart = null;
      beginSession(cfg);
      return;
    }
    if (Date.now() - sessionStartTs < SESSION_SWITCH_GUARD) return;
    if (manualDictation) {
      manualDictation = false;
      setListeningVisual(false);
      if (handsFree) scheduleWakeRearm();
      return;
    }
    if (commanding) {
      commanding = false;
      scheduleCommandSubmit();
      return;
    }
    if (handsFree) scheduleWakeRearm();
  };

  rec.onerror = (e) => {
    const err = e.error || "";
    recActive = false;
    if (err === "not-allowed" || err === "service-not-allowed") {
      handleMicBlocked();
      return;
    }
    if (err === "no-speech") {
      if (commanding) {
        commanding = false;
        scheduleCommandSubmit();
        return;
      }
      if (handsFree) scheduleWakeRearm();
      return;
    }
    if (err === "language-not-supported") {
      showMicHint("Reconhecimento de voz indisponível para o idioma atual neste navegador.");
      return;
    }
    if (err === "aborted") return; // cancelamento interno (troca de sessão)
    console.warn("[voz] erro de reconhecimento:", err);
    if (handsFree && !commanding) scheduleWakeRearm();
  };

  return rec;
}

function beginSession(cfg) {
  recognition = buildRecognizer();
  recognition.continuous = !!cfg.continuous;
  recognition.interimResults = !!cfg.interim;
  sessionStartTs = Date.now();
  try {
    recognition.start();
    recActive = true;
  } catch (_) {
    recActive = false;
    handleMicBlocked();
  }
}

function startSession(cfg) {
  if (recActive) {
    // Chromium só permite um reconhecimento ativo por vez: encerra o atual e
    // inicia o novo no onend (evita falha em cascata de stop→start imediato).
    pendingStart = cfg;
    try {
      if (recognition) recognition.stop();
    } catch (_) {}
    return;
  }
  beginSession(cfg);
}

function scheduleCommandSubmit() {
  submitTimer = clearTimer(submitTimer);
  submitTimer = setTimeout(() => {
    submitTimer = null;
    const text = els.input.value.trim();
    if (text) {
      if (streaming) return;
      sendMessage();
      return;
    }
    if (handsFree) scheduleWakeRearm();
  }, CAPTURE_PAUSE);
}

function scheduleWakeRearm() {
  wakeTimer = clearTimer(wakeTimer);
  wakeTimer = setTimeout(() => {
    wakeTimer = null;
    if (!handsFree || manualDictation || commanding) return;
    if (streaming) {
      scheduleWakeRearm();
      return;
    }
    setWakeVisual("armed");
    startSession({ continuous: true, interim: false });
  }, WAKE_REARM_DELAY);
}

function startCaptureSession() {
  startSession({ continuous: false, interim: true });
}

function setHandsFreePersist() {
  localStorage.setItem("jarvis.handsfree", handsFree ? "1" : "0");
}

function disableHandsFree() {
  handsFree = false;
  commanding = false;
  pendingStart = null;
  wakeTimer = clearTimer(wakeTimer);
  submitTimer = clearTimer(submitTimer);
  try {
    if (recognition && recActive) recognition.stop();
  } catch (_) {}
  setWakeVisual("off");
  setListeningVisual(false);
  setHandsFreePersist();
}

function toggleHandsFree() {
  if (!voiceSupported) return;
  if (handsFree) {
    disableHandsFree();
    return;
  }
  if (streaming) return;
  handsFree = true;
  manualDictation = false;
  commanding = false;
  pendingStart = null;
  wakeTimer = clearTimer(wakeTimer);
  submitTimer = clearTimer(submitTimer);
  els.input.value = "";
  setListeningVisual(false);
  setWakeVisual("armed");
  setHandsFreePersist();
  startSession({ continuous: true, interim: false });
}

function toggleVoice() {
  if (!voiceSupported) return;
  if (manualDictation) {
    manualDictation = false;
    pendingStart = null;
    try {
      if (recognition && recActive) recognition.stop();
    } catch (_) {}
    setListeningVisual(false);
    if (handsFree) scheduleWakeRearm();
    return;
  }
  if (streaming) return;
  els.input.value = "";
  manualDictation = true;
  pendingStart = null;
  wakeTimer = clearTimer(wakeTimer);
  submitTimer = clearTimer(submitTimer);
  commanding = false;
  setListeningVisual(true);
  startSession({ continuous: true, interim: true });
}

function stopVoiceTransients() {
  manualDictation = false;
  commanding = false;
  pendingStart = null;
  wakeTimer = clearTimer(wakeTimer);
  submitTimer = clearTimer(submitTimer);
  try {
    if (recognition && recActive) recognition.stop();
  } catch (_) {}
  setListeningVisual(false);
}

/* ---------- Voz neural (Fase 6b): SpeechManager ----------
   Fila de fala + interrupção + estado SPEAKING via módulo speech.js.
   `voiceEnabled` é mantido como fonte de verdade para o toggle. */
const speech = window.SpeechManager ? new window.SpeechManager() : null;

function getVoiceEnabled() {
  return speech ? speech.enabled : voiceEnabled;
}

function syncTtsToggle() {
  const on = getVoiceEnabled();
  els.ttsToggle.classList.toggle("is-on", on);
  els.ttsToggle.setAttribute("aria-pressed", String(on));
  els.ttsToggle.title = on ? "Silenciar respostas" : "Ler respostas em voz alta";
}

function toggleTts() {
  if (speech) {
    speech.setEnabled(!speech.enabled);
  } else {
    voiceEnabled = !voiceEnabled;
    localStorage.setItem("jarvis.tts", voiceEnabled ? "1" : "0");
    if (!voiceEnabled) stopSpeech();
  }
  syncTtsToggle();
}

function stopSpeech() {
  if (speech) speech.cancel();
}

async function speak(text) {
  if (!getVoiceEnabled() || !text || !speech) return;
  await speech.speak(text);
}

/* Indica visualmente quando o JARVIS está falando (estado SPEAKING). */
function bindSpeakingState() {
  if (!speech) return;
  speech.onStateChange = (state) => {
    els.statusDot.classList.toggle("is-speaking", state === "speaking");
    setOrbState(state === "speaking" ? "speaking" : listening ? "listening" : "idle");
    if (els.composerHint) {
      els.composerHint.classList.toggle("is-speaking-v", state === "speaking");
      if (state === "speaking" && !manualDictation && !handsFree) {
        els.composerHint.textContent = "VEGA falando...";
      } else if (state === "idle") {
        els.composerHint.classList.remove("is-speaking-v");
        els.composerHint.textContent = hintDefault;
      }
    }
  };
}

/* ---------- status (health) ---------- */
async function loadStatus() {
  try {
    const [health, ai] = await Promise.all([apiJSON("/health"), apiJSON("/health/ai")]);
    els.dbState.textContent = health.database === "ok" ? "OK" : "ERRO";
    els.statusDot.dataset.state = "online";
    if (ai.status === "ok") {
      els.aiState.textContent = ai.provider.toUpperCase();
      els.statusText.textContent = "ONLINE · IA PRONTA";
    } else if (ai.status === "unconfigured") {
      els.aiState.textContent = "—";
      els.statusText.textContent = "ONLINE · SEM CHAVE IA";
    } else if (ai.code === "quota") {
      els.aiState.textContent = "QUOTA";
      els.statusText.textContent = "ONLINE · QUOTA IA EXCEDIDA";
    } else {
      els.aiState.textContent = "ERRO";
      els.statusText.textContent = "ONLINE · IA INDISPONÍVEL";
    }
  } catch (_) {
    els.statusDot.dataset.state = "offline";
    els.statusText.textContent = "OFFLINE";
    els.dbState.textContent = "—";
    els.aiState.textContent = "—";
  }
}

/* ---------- presença (Fase 19.5) — estados REAIS da VEGA ----------
   Consome /api/vega/state (derivado de sinais reais no backend). Sem redes,
   sem states fabricados: falha/ausência => mantém "offline" sem enganar. */
async function loadPresence() {
  try {
    const res = await fetch("/api/vega/state");
    if (!res.ok) {
      els.agentState.textContent = "off-line";
      return;
    }
    const p = await res.json();
    els.agentState.textContent = p && (p.label || p.state) ? p.label : "off-line";
  } catch (_) {
    els.agentState.textContent = "off-line";
  }
}

function startPresencePolling(intervalMs = 5000) {
  loadPresence();
  setInterval(loadPresence, intervalMs);
}

/* ---------- sessões ---------- */
async function loadSessions() {
  try {
    const sessions = await apiJSON("/api/sessions");
    els.sessionsList.innerHTML = "";
    if (sessions.length === 0) {
      const empty = document.createElement("li");
      empty.className = "sessions-empty";
      empty.textContent = "Nenhuma sessão";
      els.sessionsList.appendChild(empty);
    }
    for (const s of sessions) {
      const li = document.createElement("li");
      li.className = "session-item" + (s.id === currentSessionId ? " active" : "");
      li.dataset.id = s.id;

      const title = document.createElement("span");
      title.className = "session-title";
      title.textContent = s.title;
      li.appendChild(title);

      const del = document.createElement("button");
      del.className = "session-del";
      del.textContent = "×";
      del.title = "Excluir sessão";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteSession(s.id);
      });
      li.appendChild(del);

      li.addEventListener("click", () => selectSession(s.id));
      els.sessionsList.appendChild(li);
    }
  } catch (err) {
    console.error("Falha ao listar sessões:", err);
  }
}

async function createSession() {
  try {
    // Fase 11.3 (#11) — troca de sessão limpa transientes de voz.
    stopVoiceTransients();
    stopSpeech();
    const s = await apiJSON("/api/sessions", { method: "POST", body: JSON.stringify({}) });
    currentSessionId = s.id;
    els.input.value = "";
    autosizeArea();
    if (els.sendBtn) els.sendBtn.classList.remove("has-text");
    await openSessionView(s.id);
    await loadSessions();
    closeDrawerOnMobile();
  } catch (err) {
    console.error("Falha ao criar sessão:", err);
  }
}

async function deleteSession(id) {
  if (id === currentSessionId && streaming) return;
  try {
    // Fase 11.3 (#11) — limpa voz antes de excluir a sessão ativa.
    if (id === currentSessionId) {
      stopVoiceTransients();
      stopSpeech();
    }
    await apiJSON(`/api/sessions/${id}`, { method: "DELETE" });
    if (id === currentSessionId) {
      currentSessionId = null;
      els.input.value = "";
      autosizeArea();
      if (els.sendBtn) els.sendBtn.classList.remove("has-text");
      els.messages.innerHTML = "";
      els.messages.hidden = true;
      els.emptyState.hidden = false;
    }
  } catch (err) {
    console.error("Falha ao excluir sessão:", err);
  }
  loadSessions();
}

async function selectSession(id) {
  if (streaming) return;
  // Fase 11.3 (#11) — troca de sessão limpa transientes de voz.
  stopVoiceTransients();
  stopSpeech();
  currentSessionId = id;
  els.input.value = "";
  autosizeArea();
  if (els.sendBtn) els.sendBtn.classList.remove("has-text");
  await openSessionView(id);
  await loadSessions();
  closeDrawerOnMobile();
}

async function openSessionView(id) {
  try {
    const messages = await apiJSON(`/api/sessions/${id}/messages`);
    els.messages.innerHTML = "";
    if (messages.length === 0) {
      els.emptyState.hidden = false;
      els.messages.hidden = true;
      return;
    }
    els.emptyState.hidden = true;
    els.messages.hidden = false;
    for (const m of messages) {
      appendMessageDOM(m.role === "user" ? "user" : "assistant", m.content, formatTime(m.created_at));
    }
    scrollChatToBottom();
  } catch (err) {
    console.error("Falha ao abrir sessão:", err);
  }
}

/* ---------- envio / SSE ---------- */

function ensureStreamContent(bubble) {
  let body = bubble.querySelector(".msg-body");
  if (!body) {
    body = document.createElement("div");
    body.className = "msg-body";
    bubble.insertBefore(body, bubble.firstChild);
  }
  let node = body.querySelector(".msg-stream");
  if (!node) {
    node = document.createElement("div");
    node.className = "msg-stream";
    body.appendChild(node);
  }
  return node;
}

const RISK_LABEL = { low: "Risco baixo", medium: "Risco médio", high: "Risco alto" };

function renderApprovalCard(approval, bubble) {
  const card = document.createElement("div");
  card.className = "approval-card";

  const head = document.createElement("div");
  head.className = "approval-head";
  const icon = document.createElement("span");
  icon.className = "approval-icon";
  icon.textContent = "◎";
  const title = document.createElement("span");
  title.className = "approval-title";
  title.textContent = `Permissão: ${approval.tool_name}`;
  head.append(icon, title);
  card.appendChild(head);

  const risk = document.createElement("div");
  risk.className = "approval-risk";
  risk.textContent = RISK_LABEL[approval.risk] || approval.risk;
  card.appendChild(risk);

  const keys = Object.keys(approval.arguments || {});
  if (keys.length) {
    const args = document.createElement("div");
    args.className = "approval-args";
    args.textContent = keys
      .map((k) => `${k}: ${String(approval.arguments[k])}`)
      .join(" · ");
    card.appendChild(args);
  }

  const actions = document.createElement("div");
  actions.className = "approval-actions";
  const okBtn = document.createElement("button");
  okBtn.className = "approval-ok";
  okBtn.textContent = "Aprovar";
  const noBtn = document.createElement("button");
  noBtn.className = "approval-no";
  noBtn.textContent = "Negar";
  actions.append(okBtn, noBtn);
  card.appendChild(actions);

  okBtn.addEventListener("click", () => respondApproval(approval, card, bubble, true));
  noBtn.addEventListener("click", () => respondApproval(approval, card, bubble, false));

  bubble.appendChild(card);
  return card;
}

function setCardStatus(card, text, cls = "") {
  const actions = card.querySelector(".approval-actions");
  if (actions) actions.remove();
  const status = document.createElement("div");
  status.className = "approval-status" + (cls ? ` ${cls}` : "");
  status.textContent = text;
  card.appendChild(status);
}

async function respondApproval(approval, card, bubble, approved) {
  if (card.dataset.responded) return;
  card.dataset.responded = "1";
  setCardStatus(card, approved ? "Aprovando... aguarde" : "Negando... aguarde");

  try {
    const res = await fetch(`/api/approvals/${approval.id}/respond`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
    });
    if (!res.ok || !res.body) {
      let detail = `Erro ${res.status}`;
      try {
        const data = await res.json();
        if (data.detail) detail = data.detail;
      } catch (_) {}
      card.dataset.responded = "";
      setCardStatus(card, detail, "approval-status-err");
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of block.split("\n")) handleSSELine(line, bubble);
      }
    }
    setCardStatus(
      card,
      approved ? "Aprovado · execução concluída" : "Negado",
      approved ? "approval-status-ok" : "approval-status-no"
    );
  } catch (err) {
    card.dataset.responded = "";
    setCardStatus(card, `Falha: ${err.message}`, "approval-status-err");
  }
}

function handleSSELine(line, bubbleEl) {
  if (!line.startsWith("data: ")) return;
  let event;
  try {
    event = JSON.parse(line.slice(6));
  } catch (_) {
    return;
  }
  const content = ensureStreamContent(bubbleEl);
  if (event.type === "chunk") {
    content.textContent += event.text;
    scrollChatToBottom();
  } else if (event.type === "done") {
    let meta = formatTime(event.message?.created_at || new Date().toISOString());
    const span = document.createElement("span");
    span.className = "msg-meta";
    span.textContent = meta;
    // Re-renderiza apenas o corpo (markdown), preservando tool-feed/meta.
    const body = bubbleEl.querySelector(".msg-body");
    const fullText = content.textContent;
    content.remove();
    if (window.Markdown && body && fullText) {
      window.Markdown.render(body, fullText);
    }
    bubbleEl.appendChild(span);
    completeToolFeed(bubbleEl);
    if (event.message?.content) speak(event.message.content);
    setOrbState("idle");
  } else if (event.type === "error") {
    content.textContent = event.detail || "Erro ao gerar resposta.";
    bubbleEl.classList.add("msg-error");
    setOrbState("error");
  } else if (event.type === "approval_request") {
    renderApprovalCard(event.approval, bubbleEl);
    scrollChatToBottom();
  } else if (event.type === "tool_start") {
    setOrbState("executing");
    showToolRunning(bubbleEl, event.names || []);
  } else if (event.type === "tool_done") {
    setOrbState("thinking");
    markToolDone(bubbleEl, event.name, event);
  }
}

/* ---------------- Tool Execution Feedback (Fase 7) ---------------- */

function toolFeedOf(bubbleEl) {
  let feed = bubbleEl.querySelector(".tool-feed");
  if (!feed) {
    feed = document.createElement("div");
    feed.className = "tool-feed";
    const body = bubbleEl.querySelector(".msg-body");
    if (body) {
      body.after(feed);
    } else {
      bubbleEl.appendChild(feed);
    }
  }
  return feed;
}

function toolItemKey(name) {
  return ((name || "").trim() || "tool").toLowerCase().replace(/[^a-z0-9]+/gi, "_");
}

function toolItemOf(feed, name) {
  const key = toolItemKey(name);
  const hasEscape = !!(window.CSS && window.CSS.escape);
  const q = hasEscape ? `[data-tool="${window.CSS.escape(key)}"]` : `[data-tool="${key}"]`;
  let item = feed.querySelector(q);
  if (!item) {
    item = document.createElement("div");
    item.className = "tool-feed-item tf-running";
    item.dataset.tool = key;
    const dot = document.createElement("span");
    dot.className = "tf-dot";
    const label = document.createElement("span");
    label.className = "tf-label";
    label.textContent = (name || "Ferramenta").replace(/_/g, " ");
    item.append(dot, label);
    feed.appendChild(item);
  }
  return item;
}

function showToolRunning(bubbleEl, names) {
  const feed = toolFeedOf(bubbleEl);
  const list = Array.isArray(names) && names.length ? names : [null];
  for (const n of list) {
    toolItemOf(feed, n).classList.remove("has-done", "tf-ok", "tf-fail");
  }
  scrollChatToBottom();
}

function markToolDone(bubbleEl, name, event) {
  const feed = toolFeedOf(bubbleEl);
  const item = toolItemOf(feed, name);
  item.classList.remove("tf-running");
  const ok = !!(event && event.ok);
  item.classList.add(ok ? "tf-ok" : "tf-fail");
  if (ok) {
    // feedback desaparece discretamente no fim do turno (Fase 7 §4)
    item.classList.add("has-done");
    setTimeout(() => {
      item.remove();
    }, 460);
  }
  scrollChatToBottom();
}

function completeToolFeed(bubbleEl) {
  const feed = bubbleEl.querySelector(".tool-feed");
  if (!feed) {
    // sem tools: nada a condensar
    return;
  }
  feed.querySelectorAll(".tool-feed-item.tf-running").forEach((item) => {
    item.classList.remove("tf-running");
    item.classList.add("tf-waiting");
  });
  // removes itens concluídos (ok) que já fizeram fade;
  // mantém durante a resposta e condensa ao final do turno.
}

async function sendMessage() {
  const content = els.input.value.trim();
  if (!content || streaming || !currentSessionId) return;

  stopVoiceTransients();
  if (getVoiceEnabled()) stopSpeech();

  els.input.value = "";
  autosizeArea();
  const field = els.inputForm.querySelector(".composer-field");
  if (field) field.classList.add("is-sending");
  appendMessageDOM("user", content, formatTime(new Date().toISOString()));
  const assistant = appendMessageDOM("assistant", "");
  setTyping(true);
  setOrbState("thinking");

  try {
    const res = await fetch(`/api/sessions/${currentSessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, stream: true, tools: true }),
    });

    if (!res.ok || !res.body) {
      let detail = `Erro ${res.status}`;
      try {
        const data = await res.json();
        if (data.detail) detail = data.detail;
      } catch (_) {}
      assistant.textContent = detail;
      assistant.classList.add("msg-error");
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of block.split("\n")) handleSSELine(line, assistant);
      }
    }
  } catch (err) {
    assistant.textContent = `Falha de conexão: ${err.message}`;
    assistant.classList.add("msg-error");
    flashComposerError();
  } finally {
    const field = els.inputForm.querySelector(".composer-field");
    if (field) field.classList.remove("is-sending");
    setTyping(false);
    setOrbState("idle");
    loadSessions();
    if (handsFree) scheduleWakeRearm();
  }
}

/* ---------- init ---------- */
function autosizeArea() {
  const el = els.input;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 152) + "px";
}

function init() {
  console.info("[voz] STT:", voiceSupported, "| TTS local:", ttsSupported);
  els.inputForm.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage();
  });
  // Enter envia; Shift+Enter quebra linha (textarea) — Fase 6.
  if (els.input && els.input.tagName === "TEXTAREA") {
    els.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        els.inputForm.requestSubmit();
      }
    });
    els.input.addEventListener("input", () => {
      autosizeArea();
      els.sendBtn.classList.toggle("has-text", els.input.value.trim().length > 0);
    });
    autosizeArea();
  }
  els.newSession.addEventListener("click", createSession);
  els.menuToggle.addEventListener("click", () => toggleDrawer());
  els.sessionsBackdrop.addEventListener("click", () => toggleDrawer(false));
  if (els.voiceBtn) els.voiceBtn.addEventListener("click", toggleVoice);
  if (els.ttsToggle) els.ttsToggle.addEventListener("click", toggleTts);
  if (els.wakeBtn) els.wakeBtn.addEventListener("click", toggleHandsFree);

  if (voiceSupported && els.voiceBtn) els.voiceBtn.hidden = false;
  if (els.ttsToggle) {
    els.ttsToggle.hidden = false;
    syncTtsToggle();
  }
  if (voiceSupported && els.wakeBtn) els.wakeBtn.hidden = false;
  if (localStorage.getItem("jarvis.handsfree") === "1") toggleHandsFree();
  if ("speechSynthesis" in window) window.speechSynthesis.getVoices(); // pré-carrega vozes (Chrome)
  bindSpeakingState();
  updateVoiceIndicators();
  if (speech) {
    speech.probeNeural().then(updateVoiceIndicators);
  }

  document.querySelectorAll(".suggestion").forEach((chip) => {
    chip.addEventListener("click", () => {
      els.input.value = chip.dataset.query || "";
      els.input.focus();
    });
  });

  if (isDrawerLayout()) toggleDrawer(false);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleDrawer(false);
  });
  els.input.focus();

  loadStatus();
  startPresencePolling();
  loadSessions().then(() => {
    if (currentSessionId) openSessionView(currentSessionId);
  });

  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    navigator.serviceWorker.register("./sw.js").catch((err) => console.warn("SW:", err));
  }

  /* PWA — botão de instalação (beforeinstallprompt). */
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
    if (els.installBtn) els.installBtn.hidden = false;
  });
  if (els.installBtn) {
    els.installBtn.addEventListener("click", async () => {
      if (!deferredInstallPrompt) return;
      deferredInstallPrompt.prompt();
      await deferredInstallPrompt.userChoice;
      deferredInstallPrompt = null;
      els.installBtn.hidden = true;
    });
  }
  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    if (els.installBtn) els.installBtn.hidden = true;
  });
}

init();