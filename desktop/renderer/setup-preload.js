/* VEGA Desktop — ponte segura para a janela de config (Fase 22). */
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("vegaSetup", {
  getConfig: () => ipcRenderer.invoke("vega:get-config"),
  setUrl: (url) => ipcRenderer.invoke("vega:set-url", url),
  connect: () => ipcRenderer.invoke("vega:connect"),
  disconnect: () => ipcRenderer.invoke("vega:disconnect"),
  checkUpdate: () => ipcRenderer.invoke("vega:check-update"),
  openCore: () => ipcRenderer.invoke("vega:open-core"),
  onStatus: (cb) => ipcRenderer.on("vega:status", (_evt, payload) => cb(payload)),
  onUpdate: (cb) => ipcRenderer.on("vega:update", (_evt, payload) => cb(payload)),
});