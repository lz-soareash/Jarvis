/* =============================================================
   JARVIS — Painel Remote (Fase 12.5)
   Aba do SPA que consome o SSE de eventos remotos (`/api/remote/events`)
   e o status observável (`/api/remote/status`). Mobile-friendly.
   Vanilla JS, sem dependências. Eventos sempre sanitizados (sem secrets).
   ============================================================= */
"use strict";

const RemoteView = (() => {
  const els = {
    view: document.getElementById("remote-view"),
    tabChat: document.getElementById("tab-chat"),
    tabOps: document.getElementById("tab-ops"),
    tabRemote: document.getElementById("tab-remote"),
    badge: document.getElementById("remote-badge"),
    mode: document.getElementById("remote-mode"),
    ovBadge: document.getElementById("remote-ov-badge"),
    device: document.getElementById("remote-device"),
    conn: document.getElementById("remote-conn"),
    count: document.getElementById("remote-count"),
    feed: document.getElementById("remote-feed"),
    empty: document.getElementById("remote-empty"),
  };

  const CHAT_ROOT = document.querySelector(".shell");
  const COMPOSER = document.getElementById("input-form");

  let open = false;
  let sse = null;
  let eventCount = 0;
  const MAX_ITEMS = 80;

  function setTab(active) {
    if (els.tabChat) els.tabChat.classList.toggle("is-active", active === "chat");
    if (els.tabOps) els.tabOps.classList.toggle("is-active", active === "ops");
    if (els.tabRemote) els.tabRemote.classList.toggle("is-active", active === "remote");
  }

  function setView(showRemote) {
    open = showRemote;
    if (els.view) els.view.hidden = !showRemote;
    if (CHAT_ROOT) CHAT_ROOT.hidden = showRemote;
    if (COMPOSER) COMPOSER.hidden = showRemote;
    setTab(showRemote ? "remote" : "chat");
    // Garante que a aba Central não fique ativa junto com a Remote.
    if (window.OpsView && showRemote && window.OpsView.isOpen && window.OpsView.isOpen()) {
      window.OpsView.setView(false);
    }
    if (showRemote) {
      loadStatus();
      connect();
    } else {
      // mantém o stream; apenas esconde a UI (reuso de conexão)
    }
  }

  function close() {
    if (open) setView(false);
  }
  function isOpen() {
    return open;
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function time(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return "";
    }
  }

  const EVENT_LABEL = {
    connected: "Conexão estabelecida",
    reconnecting: "Reconectando",
    disconnected: "Desconectado",
    revoked: "Credencial revogada",
  };

  function classify(type) {
    if (type.startsWith("remote.connection.")) {
      const kind = type.split(".").pop();
      return {
        label: EVENT_LABEL[kind] || type,
        state: kind === "revoked" ? "err" : kind === "connected" ? "ok" : "warn",
      };
    }
    if (type.startsWith("remote.command.")) {
      return { label: type.replace("remote.command.", "Comando "), state: "cmd" };
    }
    if (type.startsWith("remote.identity.")) {
      return { label: type.replace("remote.identity.", "Identidade: "), state: "ident" };
    }
    if (type === "remote.connected") {
      return { label: "Stream de eventos pronto", state: "ok" };
    }
    return { label: type, state: "misc" };
  }

  function setBadge(state, text) {
    if (els.badge) {
      els.badge.dataset.state = state;
      els.badge.textContent = text;
    }
    if (els.ovBadge) {
      els.ovBadge.textContent = text.toUpperCase();
      els.ovBadge.className =
        "ops-ov-badge " +
        (state === "ok" ? "is-ok" : state === "warn" ? "is-warn" : state === "err" ? "is-err" : "");
    }
  }

  function appendEvent(entry) {
    const type = entry?.type || "event";
    const data = entry?.data || {};
    const cls = classify(type);
    const meta = data.device_id ? ` · ${esc(data.device_id)}` : "";
    const cmd = data.command_id ? ` #${esc(data.command_id)}` : "";
    const li = document.createElement("li");
    li.className = "remote-evt remote-evt--" + cls.state;
    li.innerHTML =
      `<span class="ops-time">${time(entry.at || entry.created_at)}</span>` +
      `<span class="remote-evt-tag">${esc(cls.label)}</span>` +
      `<span class="ops-muted">${esc(data.status || "")}${esc(cmd)}${meta}</span>`;
    els.feed.appendChild(li);
    while (els.feed.children.length > MAX_ITEMS) els.feed.removeChild(els.feed.firstChild);
  }

  function connect() {
    if (sse) return; // já conectado
    if (els.empty) els.empty.hidden = true;
    setBadge("warn", "conectando");

    try {
      sse = new EventSource("/api/remote/events");
    } catch (err) {
      setBadge("err", "sem suporte SSE");
      console.error("remote: EventSource indisponível", err);
      return;
    }

    sse.onopen = () => setBadge("ok", "online");
    sse.onmessage = (e) => {
      try {
        const entry = JSON.parse(e.data);
        eventCount += 1;
        if (els.count) els.count.textContent = String(eventCount);
        appendEvent(entry);
        if (entry.type === "remote.connection.connected") setBadge("ok", "online");
        else if (entry.type === "remote.connection.revoked") setBadge("err", "revogado");
        else if (entry.type === "remote.connection.reconnecting") setBadge("warn", "reconectando");
      } catch (_) {
        // ignora linhas não-JSON (heartbeat etc.)
      }
    };
    sse.onerror = () => setBadge("warn", "reconectando");
  }

  async function loadStatus() {
    try {
      const res = await fetch("/api/remote/status");
      if (!res.ok) {
        setBadge("err", "offline");
        if (els.empty)
          els.empty.textContent =
            "Remote desabilitado — a VEGA está somente local. Configure um device para habilitar o acesso remoto.";
        return;
      }
      const st = await res.json();
      els.device.textContent = st.device_id ? esc(st.device_id.slice(0, 8)) : "—";
      const connLabel = {
        connected: "conectado",
        connecting: "conectando",
        disconnected: "desconectado",
      }[st.connection_state] || st.connection_state || "—";
      els.conn.textContent = connLabel;
      els.mode.textContent = st.enabled
        ? st.connection_state === "connected"
          ? "VEGA REMOTA"
          : "VEGA REMOTA (conectando)"
        : "VEGA SOMENTE LOCAL";
    } catch (_) {
      setBadge("err", "erro");
    }
  }

  function init() {
    if (els.tabRemote) els.tabRemote.addEventListener("click", () => setView(true));
    if (els.tabChat) els.tabChat.addEventListener("click", () => setView(false));
  }

  return { init, setView, close, isOpen, loadStatus, connect };
})();

if (typeof window !== "undefined") {
  window.addEventListener("DOMContentLoaded", RemoteView.init);
  window.RemoteView = RemoteView;
}
