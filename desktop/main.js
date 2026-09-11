/* =============================================================
   VEGA Desktop — cliente fino do JARVIS Core (Fases 21/22).

   Princípios (regra-mestra "UMA IA, MÚLTIPLOS CLIENTES"):
   - NÃO existe IA aqui: a janela aponta para o frontend servido pelo
     Core (mesma origem, /api/* relativo) em http://<configurado>:8100.
   - Cliente fino: REGISTER -> PAIRING (ancora o PENDING) -> HEARTBEAT.
     O token jamais sai do dispositivo; device_id é emitido pelo servidor.
   - Fase 22 (Distribution & Client Experience):
     * Config central em userData/config.json (sincronizada, sem tokens).
     * Janela de CONFIGURAÇÃO na primeira execução + "Configurações" no tray:
       escolha do Core URL e conexão ao Device Bridge.
     * Estados honestos de conexão (● Conectado / ○ Reconectando… /
       ○ Servidor indisponível) no tray e na janela de config.
     * Ícone VEGA (identidade Diamond Core) no .exe, janela e tray.
     * Fundação de Auto-Update: checagem de metadata (updates.js) que NUNCA
       baixa nem executa binários; instalação real fica para fase futura.
   - Reconexão automática no bridge.js (backoff), respeitando o Core.
   - single-instance.

   Windows: Node 24 + Electron (verificado em Fase 21).
   ============================================================= */
"use strict";

const { app, BrowserWindow, Tray, Menu, shell, nativeImage, ipcMain } = require("electron");
const path = require("path");
const fs = require("fs");
const zlib = require("zlib");

const { DeviceBridge } = require("./bridge");
const { WanClient } = require("./wan");
const config = require("./config");
const updates = require("./updates");
const { version: VERSION } = require("./package.json");

const DEVICE_NAME = "VEGA Desktop";
const DEVICE_PLATFORM = "windows";
const CAPABILITIES = ["chat", "tts", "notifications"];

let mainWindow = null;
let setupWindow = null;
let tray = null;
let bridge = null;
let wan = null;
let skipDisconnect = false;
let lastConfig = config.validateConfig({});

/* --------------------------------------------------------------------------
   Store de config (config.json) + credenciais (device.json) em userData.
   Credenciais ficam SEMPRE separadas e em modo 0600 (bridge.js).
   -------------------------------------------------------------------------- */
function configStore() {
  const file = path.join(app.getPath("userData"), "config.json");
  return {
    load: () => {
      try {
        return JSON.parse(fs.readFileSync(file, "utf8"));
      } catch {
        return null;
      }
    },
    save: (value) => {
      try {
        fs.writeFileSync(file, JSON.stringify(value, null, 2));
      } catch (err) {
        console.error("[vega] falha ao salvar config:", err.message);
      }
    },
  };
}

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
   Ícone VEGA — preferência pelos resources reais (resources/icon.png,
   commitado); fallback em PNG 16x16 gerado em memória (identidade Diamond).
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
  // diamond 16px sobre #05080f: 4 linhas centrais ciano->azul, pontos de acento.
  const bg = [0x05, 0x08, 0x0f];
  const topC = [0x7c, 0xe8, 0xff];
  const botC = [0x2f, 0x6b, 0xff];
  const rows = ["..XX......XX..", ".XXXXX..XXXXX.", ".XXXXXX.XXXXX.", "XXXXXXXXXXXXXXXX"];
  let i = 0;
  for (let y = 0; y < H; y++) {
    px[i++] = 0;
    for (let x = 0; x < W; x++) {
      let c = bg;
      if (y >= 4 && y <= 7) {
        const row = rows[y - 4];
        if (row[x] === "X") {
          c = topC;
          if (y >= 6) {
            c = botC;
          }
        } else if (row[x] === "." && x >= 7 && y === 6) {
          c = [0x2f, 0xe6, 0xa5]; // acento verde
        }
      } else if (y === 10 && x === 8) {
        c = [0x2f, 0xe6, 0xa5];
      }
      px[i++] = c[0];
      px[i++] = c[1];
      px[i++] = c[2];
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

function deviceIcon() {
  const file = path.join(__dirname, "resources", "icon.png");
  try {
    const img = nativeImage.createFromPath(file);
    if (!img.isEmpty()) return img;
  } catch {
    /* fallback */
  }
  return nativeImage.createFromBuffer(makePngIcon());
}

/* --------------------------------------------------------------------------
   WAN Gateway (Fase 24) — conectividade remota fora da LAN.
   Reusa a MESMA identidade pareada (device.json); o relé só transporta.
   -------------------------------------------------------------------------- */
function wanStatus() {
  if (!wan) return { state: "offline", detail: "WAN desabilitado", url: "", configured: false };
  return {
    state: wan.state,
    detail: wan.detail,
    url: wan.url,
    configured: !!wan.url,
    device_id: wan.deviceId,
    conversation_id: wan.conversationId,
  };
}

function startWan(opts = {}) {
  const url = opts.url != null ? opts.url : lastConfig.wanUrl;
  if (!url) {
    if (wan) {
      wan.stop();
      wan = null;
    }
    return wanStatus();
  }
  if (wan) wan.stop();
  wan = new WanClient({
    url,
    io: { load: creds, save: saveCreds },
    deviceId: creds()?.device_id || null,
    token: creds()?.token || null,
    pairingCode: opts.pairingCode || null,
    deviceName: DEVICE_NAME,
    platform: DEVICE_PLATFORM,
    clientVersion: VERSION,
    capabilities: CAPABILITIES,
    onState: () => {
      broadcastStatus();
      updateTray();
    },
    onAuth: () => {
      if (wan && wan.deviceId !== creds()?.device_id) {
        saveCreds({ ...(creds() || {}), device_id: wan.deviceId });
      }
      broadcastStatus();
      updateTray();
    },
  });
  wan.connect();
  return wanStatus();
}

function stopWan() {
  if (wan) {
    wan.leave();
    wan = null;
  }
  return wanStatus();
}

function safeWanLeave() {
  if (wan) return wan.leave();
  return Promise.resolve();
}

function wanLabel() {
  if (!wan || !wan.url) return "";
  const states = {
    connecting: "↻ wan conectando",
    connected: "● wan ativo",
    reconnecting: "↻ wan reconectando",
    core_unavailable: "○ wan: core indisponível",
    authentication_error: "○ wan: par necessário",
    offline: "○ wan desconectado",
  };
  return states[wan.state] || wan.state;
}

/* --------------------------------------------------------------------------
   Estado de conexão (rótulos honestos Fase 22).
   -------------------------------------------------------------------------- */
function statusPayload() {
  if (!bridge) {
    return { state: "idle", detail: "aguardando…", version: VERSION, wan: wanStatus() };
  }
  const detail =
    bridge.state === "connected"
      ? `device_id: ${bridge.token ? "pareado" : "…"}`
      : bridge.detail || "";
  return { state: bridge.state, detail, version: VERSION, wan: wanStatus() };
}

function broadcastStatus() {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send("vega:status", statusPayload());
  }
}

function broadcastUpdate(result) {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.webContents.send("vega:update", result);
  }
}

/* --------------------------------------------------------------------------
   Janelas
   -------------------------------------------------------------------------- */

function createSetupWindow() {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.show();
    setupWindow.focus();
    return setupWindow;
  }
  setupWindow = new BrowserWindow({
    width: 520,
    height: 640,
    resizable: false,
    title: "VEGA — Conexão",
    icon: deviceIcon(),
    backgroundColor: "#05080f",
    webPreferences: {
      preload: path.join(__dirname, "renderer", "setup-preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  setupWindow.loadFile(path.join(__dirname, "renderer", "setup.html"));
  setupWindow.on("closed", () => {
    setupWindow = null;
  });
  return setupWindow;
}

function createMainWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadURL(lastConfig.coreUrl);
    mainWindow.show();
    mainWindow.focus();
    return mainWindow;
  }
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
  mainWindow.loadURL(lastConfig.coreUrl);
  mainWindow.on("page-title-updated", (e) => e.preventDefault());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(lastConfig.coreUrl)) return { action: "allow" };
    shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.on("close", async (e) => {
    if (skipDisconnect) return;
    e.preventDefault();
    await Promise.all([safeLeave(), safeWanLeave()]);
    skipDisconnect = true;
    app.quit();
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  return mainWindow;
}

/* --------------------------------------------------------------------------
   Bridge
   -------------------------------------------------------------------------- */
function safeLeave() {
  if (bridge) return bridge.leave();
  return Promise.resolve();
}

function startBridge() {
  if (bridge) bridge.stop();
  bridge = new DeviceBridge(lastConfig.coreUrl, {
    version: VERSION,
    platform: DEVICE_PLATFORM,
    deviceName: DEVICE_NAME,
    capabilities: CAPABILITIES,
    io: { load: creds, save: saveCreds },
    onState: () => {
      if (bridge.state === "connected" && lastConfig.firstRun) {
        lastConfig = config.saveConfig(configStore(), { firstRun: false });
      }
      broadcastStatus();
      updateTray();
    },
  });
  bridge.boot();
}

function stopBridge() {
  if (bridge) {
    bridge.stop();
    bridge = null;
  }
}

/* --------------------------------------------------------------------------
   Tray / menu
   -------------------------------------------------------------------------- */
function bridgeLabel() {
  if (!bridge) return "iniciando…";
  const states = {
    registering: "↻ registrando",
    pairing: "↻ pareamento",
    connecting: "↻ reconectando",
    connected: "● conectado",
    error: "○ servidor indisponível",
  };
  const dot = bridge.state === "connected" ? "●" : bridge.state === "error" ? "○" : "↻";
  return `${dot} ${states[bridge.state] || bridge.state}`;
}

function trayLabel() {
  const parts = [bridgeLabel()];
  const wan = wanLabel();
  if (wan) parts.push(wan);
  return parts.join(" · ");
}

function trayMenuTemplate() {
  return Menu.buildFromTemplate([
    { label: "Abrir VEGA", click: () => createMainWindow() },
    { label: "Configurações", click: () => createSetupWindow() },
    { type: "separator" },
    { label: trayLabel(), enabled: false },
    { label: "Verificar atualizações", click: () => checkUpdateNow() },
    { label: "Abrir Central (navegador)", click: () => shell.openExternal(lastConfig.coreUrl) },
    { type: "separator" },
    { label: `VEGA ${VERSION}`, enabled: false },
    { label: "Sair", click: async () => {
      await Promise.all([safeLeave(), safeWanLeave()]);
      skipDisconnect = true;
      app.quit();
    } },
  ]);
}

function updateTray() {
  if (!tray) return;
  tray.setTitle(trayLabel());
  tray.setToolTip(`VEGA Desktop ${VERSION} — ${trayLabel()}`);
  tray.setContextMenu(trayMenuTemplate());
}

function createTray() {
  tray = new Tray(deviceIcon());
  updateTray();
  tray.on("click", () => createMainWindow());
}

/* --------------------------------------------------------------------------
   Auto-update (fundação) — apenas metadata, jamais download/execução.
   -------------------------------------------------------------------------- */
async function checkUpdateNow() {
  const result = await updates.checkForUpdate({
    currentVersion: VERSION,
    releaseFeedUrl: lastConfig.releaseFeedUrl,
  });
  broadcastUpdate(result);
  return result;
}

/* --------------------------------------------------------------------------
   IPC da janela de config
   -------------------------------------------------------------------------- */
function registerIpc() {
  ipcMain.handle("vega:get-config", () => ({
    coreUrl: lastConfig.coreUrl,
    releaseFeedUrl: lastConfig.releaseFeedUrl,
    wanUrl: lastConfig.wanUrl,
    version: VERSION,
  }));
  ipcMain.handle("vega:set-url", (_evt, url) => {
    lastConfig = config.saveConfig(configStore(), { coreUrl: url });
    stopBridge();
    updateTray();
    return { coreUrl: lastConfig.coreUrl };
  });
  ipcMain.handle("vega:set-wan-url", (_evt, url) => {
    lastConfig = config.saveConfig(configStore(), { wanUrl: url });
    startWan();
    updateTray();
    return { wanUrl: lastConfig.wanUrl, wan: wanStatus() };
  });
  ipcMain.handle("vega:wan-pair", (_evt, code) => {
    startWan({ pairingCode: code });
    updateTray();
    return wanStatus();
  });
  ipcMain.handle("vega:connect", async () => {
    startBridge();
    return statusPayload();
  });
  ipcMain.handle("vega:disconnect", async () => {
    await safeLeave();
    stopBridge();
    updateTray();
    return statusPayload();
  });
  ipcMain.handle("vega:check-update", () => checkUpdateNow());
  ipcMain.handle("vega:open-core", () => shell.openExternal(lastConfig.coreUrl));
}

/* --------------------------------------------------------------------------
   Bootstrap
   -------------------------------------------------------------------------- */
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) createMainWindow();
    else createSetupWindow();
  });

  registerIpc();

  app.whenReady().then(() => {
    lastConfig = config.loadConfig(configStore());
    createTray();
    // Primeira experiência: janela de configuração. Já configurado: abre o Core.
    if (lastConfig.firstRun && !creds()) {
      createSetupWindow();
    } else {
      createMainWindow();
      startBridge();
      if (lastConfig.wanUrl) startWan();
    }
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    });
  });

  app.on("before-quit", async (e) => {
    if ((bridge || wan) && !skipDisconnect) {
      e.preventDefault();
      skipDisconnect = true;
      await Promise.all([safeLeave(), safeWanLeave()]);
      app.quit();
    }
  });
}