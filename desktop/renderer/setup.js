/* VEGA Desktop — lógica da janela de config (Fase 22). */
"use strict";

const $ = (id) => document.getElementById(id);

function setStatus(payload) {
  const dot = $("status-dot");
  dot.className = "dot";
  const label = $("status-label");
  const detail = $("status-detail");

  const state = payload && payload.state ? payload.state : "idle";
  const maps = {
    registering: ["busy", "Registrando…"],
    pairing: ["busy", "Pareando…"],
    connecting: ["busy", "Conectando…"],
    connected: ["on", "● Conectado"],
    error: ["off", "○ Servidor indisponível"],
    reconnecting: ["busy", "○ Reconectando…"],
  };
  let css = maps[state] ? maps[state][0] : "off";
  let text = maps[state] ? maps[state][1] : "—";
  if (state !== "connected") {
    css = "busy";
    text = state === "error" ? "○ Servidor indisponível" : "○ Reconectando…";
  }
  dot.className = `dot ${css}`;
  label.textContent = text;
  detail.textContent = payload && payload.detail ? payload.detail : "";

  const connected = state === "connected";
  $("connect").disabled = connected;
  $("disconnect").disabled = !connected;
  $("core-url").disabled = connected;
}

function setUpdate(payload) {
  if (!payload) return;
  if (payload.update_available) {
    $("updates").textContent = `Atualização disponível: ${payload.latest_version}`;
  } else if (payload.error) {
    $("updates").textContent = "Verificação indisponível no momento";
  } else {
    $("updates").textContent = `Você está na versão mais recente`;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const cfg = await window.vegaSetup.getConfig();
  $("core-url").value = cfg.coreUrl || "";
  $("app-version").textContent = `VEGA ${cfg.version || ""}`;

  $("connect").addEventListener("click", async () => {
    await window.vegaSetup.setUrl($("core-url").value.trim());
    $("status-label").textContent = "○ Reconectando…";
    $("status-detail").textContent = "conectando ao Core…";
    await window.vegaSetup.connect();
  });

  $("disconnect").addEventListener("click", async () => {
    await window.vegaSetup.disconnect();
    $("core-url").disabled = false;
  });

  $("check-update").addEventListener("click", () => window.vegaSetup.checkUpdate());
  $("open-core").addEventListener("click", () => window.vegaSetup.openCore());

  window.vegaSetup.onStatus((payload) => setStatus(payload));
  window.vegaSetup.onUpdate((payload) => setUpdate(payload));
});