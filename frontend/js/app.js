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
  messages: document.getElementById("messages"),
  inputForm: document.getElementById("input-form"),
  input: document.getElementById("input"),
  sendBtn: document.getElementById("send-btn"),
  voiceBtn: document.getElementById("voice-btn"),
  ttsToggle: document.getElementById("tts-toggle"),
  wakeBtn: document.getElementById("wake-btn"),
  composerHint: document.getElementById("composer-hint"),
  agentState: document.getElementById("agent-state"),
  dbState: document.getElementById("db-state"),
  aiState: document.getElementById("ai-state"),
};

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
  msg.textContent = content;
  if (meta) {
    const time = document.createElement("span");
    time.className = "msg-meta";
    time.textContent = meta;
    msg.appendChild(time);
  }
  els.messages.appendChild(msg);
  els.messages.scrollTop = els.messages.scrollHeight;
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

function formatTime(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
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
let listening = false;
let manualDictation = false;
let handsFree = false;
let commanding = false;
let sessionStartTs = 0;
let wakeTimer = null;
let submitTimer = null;
let voiceEnabled = localStorage.getItem("jarvis.tts") === "1";

const hintDefault = els.composerHint ? els.composerHint.textContent : "";

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
  rec.lang = navigator.language || "pt-BR";
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
      els.input.scrollLeft = els.input.scrollWidth;
      return;
    }
    if (!handsFree) return;

    if (commanding) {
      els.input.value = text;
      els.input.scrollLeft = els.input.scrollWidth;
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
      els.input.scrollLeft = els.input.scrollWidth;
      scheduleCommandSubmit();
    } else {
      startCaptureSession();
    }
  };

  rec.onend = () => {
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
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      disableHandsFree(true);
      return;
    }
    if (e.error === "no-speech") {
      if (commanding) {
        commanding = false;
        scheduleCommandSubmit();
        return;
      }
      if (handsFree) scheduleWakeRearm();
      return;
    }
    if (handsFree && !commanding) scheduleWakeRearm();
  };

  return rec;
}

function startSession(cfg) {
  try {
    if (recognition) recognition.stop();
  } catch (_) {}
  recognition = buildRecognizer();
  recognition.continuous = !!cfg.continuous;
  recognition.interimResults = !!cfg.interim;
  sessionStartTs = Date.now();
  try {
    recognition.start();
  } catch (_) {
    disableHandsFree(true);
  }
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

function disableHandsFree(permissionDenied) {
  handsFree = false;
  commanding = false;
  wakeTimer = clearTimer(wakeTimer);
  submitTimer = clearTimer(submitTimer);
  try {
    if (recognition) recognition.stop();
  } catch (_) {}
  setWakeVisual("off");
  setListeningVisual(false);
  if (!permissionDenied) setHandsFreePersist();
  else console.warn("Microfone sem permissão — modo mãos-livres desativado.");
}

function toggleHandsFree() {
  if (!voiceSupported) return;
  if (handsFree) {
    disableHandsFree(false);
    return;
  }
  if (streaming) return;
  handsFree = true;
  manualDictation = false;
  commanding = false;
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
    try {
      if (recognition) recognition.stop();
    } catch (_) {}
    setListeningVisual(false);
    if (handsFree) scheduleWakeRearm();
    return;
  }
  if (streaming) return;
  els.input.value = "";
  manualDictation = true;
  wakeTimer = clearTimer(wakeTimer);
  submitTimer = clearTimer(submitTimer);
  commanding = false;
  setListeningVisual(true);
  startSession({ continuous: true, interim: true });
}

function stopVoiceTransients() {
  manualDictation = false;
  commanding = false;
  wakeTimer = clearTimer(wakeTimer);
  submitTimer = clearTimer(submitTimer);
  try {
    if (recognition) recognition.stop();
  } catch (_) {}
  setListeningVisual(false);
}

function speak(text) {
  if (!ttsSupported || !voiceEnabled || !text) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = navigator.language || "pt-BR";
  const voice = window.speechSynthesis
    .getVoices()
    .find((v) => v.lang && v.lang.toLowerCase().startsWith("pt"));
  if (voice) utter.voice = voice;
  utter.rate = 1.04;
  utter.pitch = 0.95;
  window.speechSynthesis.speak(utter);
}

function syncTtsToggle() {
  els.ttsToggle.classList.toggle("is-on", voiceEnabled);
  els.ttsToggle.setAttribute("aria-pressed", String(voiceEnabled));
  els.ttsToggle.title = voiceEnabled ? "Silenciar respostas" : "Ler respostas em voz alta";
}

function toggleTts() {
  if (!ttsSupported) return;
  voiceEnabled = !voiceEnabled;
  localStorage.setItem("jarvis.tts", voiceEnabled ? "1" : "0");
  if (!voiceEnabled) window.speechSynthesis.cancel();
  syncTtsToggle();
}

/* ---------- status (health) ---------- */
async function loadStatus() {
  try {
    const [health, ai] = await Promise.all([apiJSON("/health"), apiJSON("/health/ai")]);
    els.dbState.textContent = health.database === "ok" ? "OK" : "ERRO";
    els.aiState.textContent =
      ai.status === "ok" ? ai.provider.toUpperCase() : ai.status.toUpperCase();
    if (ai.status === "ok") {
      els.statusDot.dataset.state = "online";
      els.statusText.textContent = "ONLINE · IA PRONTA";
    } else {
      els.statusDot.dataset.state = "online";
      els.statusText.textContent = "ONLINE · SEM CHAVE IA";
    }
  } catch (_) {
    els.statusDot.dataset.state = "offline";
    els.statusText.textContent = "OFFLINE";
    els.dbState.textContent = "—";
    els.aiState.textContent = "—";
  }
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
    const s = await apiJSON("/api/sessions", { method: "POST", body: JSON.stringify({}) });
    currentSessionId = s.id;
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
    await apiJSON(`/api/sessions/${id}`, { method: "DELETE" });
    if (id === currentSessionId) {
      currentSessionId = null;
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
  currentSessionId = id;
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
    els.messages.scrollTop = els.messages.scrollHeight;
  } catch (err) {
    console.error("Falha ao abrir sessão:", err);
  }
}

/* ---------- envio / SSE ---------- */

function ensureStreamContent(bubble) {
  let node = bubble.querySelector(".msg-stream");
  if (!node) {
    node = document.createElement("div");
    node.className = "msg-stream";
    bubble.insertBefore(node, bubble.firstChild);
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
    els.messages.scrollTop = els.messages.scrollHeight;
  } else if (event.type === "done") {
    let meta = formatTime(event.message?.created_at || new Date().toISOString());
    const span = document.createElement("span");
    span.className = "msg-meta";
    span.textContent = meta;
    bubbleEl.appendChild(span);
    if (event.message?.content) speak(event.message.content);
  } else if (event.type === "error") {
    content.textContent = event.detail || "Erro ao gerar resposta.";
    bubbleEl.classList.add("msg-error");
  } else if (event.type === "approval_request") {
    renderApprovalCard(event.approval, bubbleEl);
    els.messages.scrollTop = els.messages.scrollHeight;
  }
  /* tool_start / tool_done / approval_pending: progresso implícito no card */
}

async function sendMessage() {
  const content = els.input.value.trim();
  if (!content || streaming || !currentSessionId) return;

  stopVoiceTransients();
  if (ttsSupported && voiceEnabled) window.speechSynthesis.cancel();

  els.input.value = "";
  appendMessageDOM("user", content, formatTime(new Date().toISOString()));
  const assistant = appendMessageDOM("assistant", "");
  setTyping(true);

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
  } finally {
    setTyping(false);
    loadSessions();
    if (handsFree) scheduleWakeRearm();
  }
}

/* ---------- init ---------- */
function init() {
  els.inputForm.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage();
  });
  els.newSession.addEventListener("click", createSession);
  els.menuToggle.addEventListener("click", () => toggleDrawer());
  els.sessionsBackdrop.addEventListener("click", () => toggleDrawer(false));
  if (els.voiceBtn) els.voiceBtn.addEventListener("click", toggleVoice);
  if (els.ttsToggle) els.ttsToggle.addEventListener("click", toggleTts);
  if (els.wakeBtn) els.wakeBtn.addEventListener("click", toggleHandsFree);

  if (voiceSupported && els.voiceBtn) els.voiceBtn.hidden = false;
  if (ttsSupported && els.ttsToggle) {
    els.ttsToggle.hidden = false;
    syncTtsToggle();
  }
  if (voiceSupported && els.wakeBtn) els.wakeBtn.hidden = false;
  if (localStorage.getItem("jarvis.handsfree") === "1") toggleHandsFree();
  if ("speechSynthesis" in window) window.speechSynthesis.getVoices(); // pré-carrega vozes (Chrome)

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
  loadSessions().then(() => {
    if (currentSessionId) openSessionView(currentSessionId);
  });

  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    navigator.serviceWorker.register("./sw.js").catch((err) => console.warn("SW:", err));
  }
}

init();