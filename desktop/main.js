/* =============================================================
   VEGA Desktop — cliente fino do JARVIS Core (Fase 21).

   Princípios (regra-mestra "UMA IA, MÚLTIPLOS CLIENTES"):
   - NÃO existe IA aqui: a janela aponta para o frontend servido pelo
     Core (mesma origem, /api/* relativo) em http://127.0.0.1:8100.
   - Cliente fino: REGISTER -> PAIRING (ancora o PENDING) -> HEARTBEAT.
     O token jamais sai do dispositivo; device_id é emitido pelo servidor.
   - Reconexão automática respeitando `heartbeat_seconds`/`reconnect_enabled`
     devolvidos pelo Core; disconnect ao fechar.
   - O protocolo (DeviceBridge) vive em bridge.js — testável em Node puro.

   Windows: Node 24 + Electron (networks verificadas em auditoria F21).
   ============================================================= */
"use strict";

const { app, BrowserWindow, Tray, Menu, shell, nativeImage } = require("electron");
const path = require("path");
const fs = require("fs");
const zlib = require("zlib");

const { DeviceBridge } = require("./bridge");
const { version: VERSION } = require("./package.json");

const CORE_URL = process.env.VEGA_CORE_URL || "http://127.0.0.1:8100";
const DEVICE_NAME = "VEGA Desktop";
const DEVICE_PLATFORM = "windows";
const CAPABILITIES = ["chat", "tts", "notifications"];

let mainWindow = null;
let tray = null;
let bridge = null;
let skipDisconnect = false;

/* --------------------------------------------------------------------------
   Credenciais locais em app.getPath("userData") (modo padrão do Electron).
   -------------------------------------------------------------------------- */
function credsPath() {
  return path.join(app.getPath("userData"), "device.json");
}

function creds() {
  try {
    return JSON.parse(fs.readFileSync(credsPath(), "utf8"));
  } catch {
    return null;
  }
}

function saveCreds(value) {
  try {
    fs.writeFileSync(credsPath(), JSON.stringify(value), { mode: 0o600 });
  } catch (err) {
    console.error("[vega] falha ao salvar credenciais:", err.message);
  }
}

/* --------------------------------------------------------------------------
   Ícone PNG 16x16 gerado em memória (verde VEGA) — sem asset binário no repo.
   -------------------------------------------------------------------------- */
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([Buffer.from(type), data])), 0);
  return Buffer.concat([len, Buffer.from(type), data, crc]);
}

function makePngIcon() {
  const W = 16;
  const H = 16;
  const px = Buffer.alloc((W * 3 + 1) * H);
  let i = 0;
  for (let y = 0; y < H; y++) {
    px[i++] = 0;
    for (let x = 0; x < W; x++) {
      const border = x === 0 || y === 0 || x === W - 1 || y === H - 1;
      px[i++] = border ? 0x0b : 0x10;
      px[i++] = border ? 0x12 : 0xb9;
      px[i++] = border ? 0x20 : 0x81;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(W, 0);
  ihdr.writeUInt32BE(H, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", zlib.deflateSync(px)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

const ICON_PNG = makePngIcon();

function deviceIcon() {
  return nativeImage.createFromBuffer(ICON_PNG);
}

/* --------------------------------------------------------------------------
   Janela / Tray / estado
   -------------------------------------------------------------------------- */
function bridgeTrafficLabel() {
  if (!bridge) return "iniciando…";
  const dot = bridge.state === "connected" ? "●" : bridge.state === "error" ? "◆" : "◌";
  const names = {
    registering: "registrando",
    pairing: "pareamento",
    connecting: "conectando",
    connected: "ligado",
    error: "core off-line",
  };
  return `${dot} ${names[bridge.state] || bridge.state}`;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: "VEGA Desktop",
    icon: deviceIcon(),
    autoHideMenuBar: true,
    backgroundColor: "#0b1220",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.loadURL(CORE_URL);
  mainWindow.on("page-title-updated", (e) => e.preventDefault());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(CORE_URL)) return { action: "allow" };
    shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.on("close", async (e) => {
    if (skipDisconnect) return;
    e.preventDefault();
    await bridge.leave();
    skipDisconnect = true;
    app.quit();
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function trayMenuTemplate() {
  return Menu.buildFromTemplate([
    { label: "Abrir VEGA", click: () => (mainWindow ? mainWindow.show() : createWindow()) },
    { type: "separator" },
    { label: bridgeTrafficLabel(), enabled: false },
    { label: "Abrir Central (navegador)", click: () => shell.openExternal(CORE_URL) },
    { label: "Sair", click: async () => { await bridge.leave(); skipDisconnect = true; app.quit(); } },
  ]);
}

function updateTray() {
  if (!tray) return;
  tray.setTitle(bridgeTrafficLabel());
  tray.setToolTip(`VEGA Desktop — ${bridgeTrafficLabel()}`);
  tray.setContextMenu(trayMenuTemplate());
}

function createTray() {
  tray = new Tray(deviceIcon());
  updateTray();
  tray.on("click", () => {
    if (mainWindow) mainWindow.show();
    else createWindow();
  });
}

function startBridge() {
  bridge = new DeviceBridge(CORE_URL, {
    version: VERSION,
    platform: DEVICE_PLATFORM,
    deviceName: DEVICE_NAME,
    capabilities: CAPABILITIES,
    io: { load: creds, save: saveCreds },
    onState: () => updateTray(),
  });
  bridge.boot();
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    createWindow();
    createTray();
    startBridge();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("before-quit", async (e) => {
    if (bridge && bridge.token && !skipDisconnect) {
      e.preventDefault();
      skipDisconnect = true;
      await bridge.leave();
      app.quit();
    }
  });
}