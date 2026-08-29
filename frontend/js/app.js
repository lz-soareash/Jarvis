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
  newSession: document.getElementById("new-session"),
  chatWindow: document.getElementById("chat-window"),
  emptyState: document.getElementById("empty-state"),
  messages: document.getElementById("messages"),
  inputForm: document.getElementById("input-form"),
  input: document.getElementById("input"),
  sendBtn: document.getElementById("send-btn"),
  agentState: document.getElementById("agent-state"),
  dbState: document.getElementById("db-state"),
  aiState: document.getElementById("ai-state"),
};

let currentSessionId = null;
let streaming = false;

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
  }
}

/* ---------- init ---------- */
function init() {
  const scan = document.createElement("div");
  scan.className = "scanlines";
  document.body.appendChild(scan);

  els.inputForm.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage();
  });
  els.newSession.addEventListener("click", createSession);
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