/* =============================================================
   Device Bridge do cliente fino (Fase 21) — Núcleo agnóstico de UI.

   Não importa Electron: só Node `fetch` padrão + `crypto`. O app de desktop
   injeta `io` (persistência + estado) e lança o fluxo em `boot()`. Isso torna
   o protocolo testável de forma herméctica em Node puro (ver bridge.test.mjs).
   ============================================================= */
"use strict";

const crypto = require("crypto");

const DEFAULT_CAPABILITIES = ["chat", "tts", "notifications"];

function userAgent(version, platform) {
  return `jarvis/${version} (${platform}; device-bridge)`;
}

function fakeStore(initial) {
  let snap = initial || null;
  return {
    load: () => snap,
    save: (value) => {
      snap = value;
    },
  };
}

class DeviceBridge {
  constructor(baseUrl, opts = {}) {
    this.baseUrl = (baseUrl || "http://127.0.0.1:8100").replace(/\/+$/, "");
    this.version = opts.version || "0.0.0";
    this.platform = opts.platform || "win32";
    this.deviceName = opts.deviceName || "VEGA Desktop";
    this.capabilities = opts.capabilities || DEFAULT_CAPABILITIES;
    this.io = opts.io || fakeStore();
    this.requestId = () => crypto.randomUUID();
    this.state = "registering";
    this.token = null;
    this.heartbeatMs = 30000;
    this.reconnectEnabled = true;
    this.failures = 0;
    this.timer = null;
    this.onState = opts.onState || (() => {});
    this.onToken = opts.onToken || (() => {});
  }

  setState(state, detail) {
    this.state = state;
    this.onState(state, detail);
  }

  async httpJSON(method, pathname, body) {
    const res = await fetch(`${this.baseUrl}${pathname}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "User-Agent": userAgent(this.version, this.platform),
        "X-Request-ID": this.requestId(),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(`HTTP ${res.status}`);
      err.status = res.status;
      err.detail = data.message || data.detail || "";
      throw err;
    }
    return data;
  }

  async boot() {
    this.setState("registering", "registrando identidade no Core…");
    try {
      const stored = this.io.load();
      if (stored && stored.token) {
        this.token = stored.token;
        try {
          await this.heartbeat("reconnect", stored.device_id);
          this.setState("connected", `reconectado (${stored.device_id.slice(0, 8)}…)`);
          this.schedule();
          return;
        } catch (err) {
          if (err.status === 401) this.token = null; // revogado → re-pareia
          else throw err;
        }
      }
      await this.pair();
      await this.heartbeat("connect", this.io.load()?.device_id);
      this.setState("connected", "pareado e conectado ao Core");
      this.schedule();
    } catch (err) {
      this.failures += 1;
      const detail = err.status === 503
        ? "Remote/Device Bridge desabilitado no Core (REMOTE_ENABLED/DEVICE_ENABLED)"
        : `sem contato com o Core (${err.message})`;
      this.setState("error", detail);
      this.retry();
    }
  }

  async pair() {
    const registered = await this.httpJSON("POST", "/api/remote/devices/register", {
      name: this.deviceName,
      device_type: "desktop",
      platform: this.platform === "darwin" ? "macos" : this.platform,
      client_version: this.version,
      capabilities: this.capabilities,
    });
    const deviceId = registered.device.id;
    this.setState("pairing", "gerando código de pareamento…");
    const p = await this.httpJSON("POST", "/api/remote/pairings");
    const paired = await this.httpJSON("POST", "/api/remote/pairings/validate", {
      code: p.code,
      device_name: this.deviceName,
      device_type: "desktop",
      platform: this.platform === "darwin" ? "macos" : this.platform,
      client_version: this.version,
      capabilities: this.capabilities,
      pending_device_id: deviceId,
    });
    this.token = paired.token;
    this.io.save({ device_id: paired.device_id || deviceId, token: paired.token });
    this.onToken(this.token);
  }

  async heartbeat(event, claimedDeviceId) {
    const out = await this.httpJSON("POST", "/api/remote/heartbeat", {
      token: this.token,
      event,
      platform: this.platform === "darwin" ? "macos" : this.platform,
      client_version: this.version,
      capabilities: this.capabilities,
      claimed_device_id: claimedDeviceId,
    });
    this.heartbeatMs = (out.heartbeat_seconds || 30) * 1000;
    this.reconnectEnabled = !!out.reconnect_enabled;
    this.failures = 0;
    return out;
  }

  schedule() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.tick(), this.heartbeatMs);
  }

  async tick() {
    try {
      await this.heartbeat("heartbeat");
      this.setState("connected");
      this.schedule();
    } catch (err) {
      this.failures += 1;
      this.setState("error", `heartbeat falhou (${err.message})`);
      this.retry();
    }
  }

  retry() {
    if (this.timer) clearTimeout(this.timer);
    const backoff = Math.min(15000, 2000 * Math.min(this.failures, 5));
    this.timer = setTimeout(() => this.boot(), backoff);
  }

  async leave() {
    if (this.timer) clearTimeout(this.timer);
    if (this.token) {
      try {
        await this.httpJSON("POST", "/api/remote/heartbeat", {
          token: this.token,
          event: "disconnect",
        });
      } catch {
        /* best-effort no shutdown */
      }
    }
  }

  stop() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}

module.exports = { DeviceBridge, userAgent, fakeStore, DEFAULT_CAPABILITIES };