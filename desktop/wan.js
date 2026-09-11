/* =============================================================
   WAN Gateway Client (Fase 24) — conectividade remota do cliente fino.

   Uma UMA IA, MÚLTIPLOS CLIENTES: o Desktop usa a MESMA identidade
   pareada no Core (device_id + token de device.json) e também se
   conecta fora da LAN pelo relé WebSocket — o relé só transporta;
   TODA autoridade continua no Core (auth, turno, permissões).

   Módulo NODE PURO (sem Electron): usa o WebSocket nativo do Node
   (>= 21; sem dependências). O app injeta `io` (persistência). Isso
   torna o protocolo testável hermeticamente (wan.test.mjs roda um
   servidor WS mínimo na própria porta efêmera).

   Nunca envia token em URL. Nunca loga secrets.
   ============================================================= */
"use strict";

const crypto = require("crypto");

const DEFAULT_MAX_BACKOFF_MS = 60000;
const DEFAULT_CONNECT_TIMEOUT_MS = 10000;
const DEFAULT_HEARTBEAT_MS = 30000;
const DEFAULT_MESSAGE_TIMEOUT_MS = 60000;

function fakeStore() {
  let snap = null;
  return {
    load: () => snap,
    save: (value) => {
      snap = value;
    },
  };
}

class WanClient {
  constructor(opts = {}) {
    this.url = opts.url || "";
    this.io = opts.io || fakeStore();
    this.deviceName = opts.deviceName || "VEGA Desktop";
    this.platform = opts.platform || "windows";
    this.clientVersion = opts.clientVersion || "0.0.0";
    this.capabilities = opts.capabilities || ["chat", "tts", "notifications"];
    this.requestId = opts.requestId || (() => crypto.randomUUID());
    this.connectTimeoutMs = opts.connectTimeoutMs || DEFAULT_CONNECT_TIMEOUT_MS;
    this.maxBackoffMs = opts.maxBackoffMs || DEFAULT_MAX_BACKOFF_MS;

    this.onState = opts.onState || (() => {});
    this.onAuth = opts.onAuth || (() => {});
    this.onMessageResult = opts.onMessageResult || (() => {});
    this.onAgentEvent = opts.onAgentEvent || (() => {});
    this.onHeartbeat = opts.onHeartbeat || (() => {});
    this.onError = opts.onError || (() => {});
    this._log = opts.log || (() => {});

    this.deviceId = opts.deviceId || null;
    this.token = opts.token || null;
    this.pairingCode = opts.pairingCode || null;
    this.sessionId = null;
    this.conversationId = null;
    this.heartbeatMs = opts.heartbeatMs || DEFAULT_HEARTBEAT_MS;
    this.reconnectEnabled = opts.reconnectEnabled !== false;
    this.messageTimeoutMs = opts.messageTimeoutMs || DEFAULT_MESSAGE_TIMEOUT_MS;
    this.queueTtlMs = opts.queueTtlMs != null ? opts.queueTtlMs : 300000;

    this.state = "offline";
    this.detail = "";
    this.failures = 0;
    this.authenticated = false;
    this.ws = null;
    this.reauthAttempts = 0;
    this._handshakeTimer = null;
    this._pingTimer = null;
    this._reconnectTimer = null;
    this._intentionalClose = false;
    this._sawHelloAck = false;
  }

  setState(state, detail) {
    this.state = state;
    this.detail = detail || "";
    this.onState(state, this.detail);
  }

  configure(opts = {}) {
    if (opts.url != null) this.url = String(opts.url);
    if (opts.token != null) this.token = opts.token || null;
    if (opts.deviceId != null) this.deviceId = opts.deviceId || null;
    if (opts.pairingCode != null) this.pairingCode = opts.pairingCode || null;
    return this;
  }

  async connect() {
    this._clearTimers();
    this._intentionalClose = false;
    if (!this.url) {
      this.setState("offline", "URL do gateway WAN não configurada");
      return;
    }
    await this._openSocket();
  }

  _openSocket() {
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        resolve();
      };

      this.setState("connecting", `conectando ao relé (${this.url})…`);
      let ws;
      try {
        ws = new WebSocket(this.url);
      } catch (err) {
        this.failures += 1;
        this.setState("core_unavailable", `URL inválida (${err.message})`);
        this.onError(err);
        this._scheduleReconnect();
        finish();
        return;
      }
      this.ws = ws;

      this._handshakeTimer = setTimeout(() => {
        this._log("[wan] handshake timeout");
        try {
          ws.close(1002, "handshake timeout");
        } catch {
          /* noop */
        }
      }, this.connectTimeoutMs);

      ws.addEventListener("open", () => {
        this._sendEnvelope("hello", {
          role: "mobile",
          protocol_version: 1,
          client_version: this.clientVersion,
        });
        finish();
      });

      ws.addEventListener("message", (evt) => {
        this._onFrame(evt.data);
      });

      ws.addEventListener("error", (err) => {
        this._log("[wan] ws error:", (err && err.message) || err);
        this.onError(err instanceof Error ? err : new Error("WebSocket error"));
      });

      ws.addEventListener("close", (evt) => {
        this._clearHandshake();
        if (this._intentionalClose) {
          this.ws = null;
          this.setState("offline", "desconectado pelo usuário");
          return;
        }
        this.ws = null;
        if (this._sawHelloAck || this.authenticated) {
          this.authenticated = false;
          this.setState("reconnecting", `conexão caiu (código ${evt.code})`);
        } else {
          this.setState("core_unavailable", `relé indisponível (código ${evt.code})`);
        }
        this._scheduleReconnect();
      });
    });
  }

  _onFrame(raw) {
    let env;
    try {
      env = JSON.parse(String(raw));
    } catch {
      this._log("[wan] frame não-JSON ignorado");
      return;
    }
    switch (env.type) {
      case "hello_ack":
        this._onHelloAck(env);
        break;
      case "auth_result":
        this._onAuthResult(env);
        break;
      case "heartbeat_ack":
        this.failures = 0;
        this.onHeartbeat((env.payload || {}).ok === true, env);
        break;
      case "message_result":
        this.onMessageResult(env.payload || {}, env);
        break;
      case "agent_event":
        this.onAgentEvent(env.payload || {}, env);
        break;
      case "error":
        this._onErrorFrame(env);
        break;
      default:
        this._log(`[wan] tipo não tratado: ${env.type}`);
    }
  }

  _onHelloAck(env) {
    this._clearHandshake();
    const payload = env.payload || {};
    if (!payload.ok) {
      this._log("[wan] hello rejeitado");
      this.onError(new Error("relé rejeitou o hello"));
      this._scheduleReconnect();
      return;
    }
    this._sawHelloAck = true;
    this._authenticate();
  }

  _authenticate() {
    let payload;
    if (this.pairingCode) {
      payload = {
        pairing_code: String(this.pairingCode).trim(),
        device_name: this.deviceName,
        device_type: "desktop",
        platform: this.platform,
        client_version: this.clientVersion,
        capabilities: this.capabilities,
        transport_meta: { app: "desktop", transport: "wss" },
      };
    } else if (this.token) {
      payload = {
        token: this.token,
        claimed_device_id: this.deviceId,
        platform: this.platform,
        client_version: this.clientVersion,
        capabilities: this.capabilities,
        transport_meta: { app: "desktop", transport: "wss" },
      };
    } else {
      this.setState("authentication_error", "sem token nem código de pareamento");
      return;
    }
    this._sendEnvelope("auth", payload, { request_id: this.requestId() });
  }

  _onAuthResult(env) {
    const p = env.payload || {};
    if (!p.ok) {
      this.authenticated = false;
      this.setState("authentication_error", p.reason || "autenticação negada pelo Core");
      this.onError(new Error(this.detail));
      return;
    }
    if (p.token) this.token = p.token; // bootstrap: emitido uma única vez aqui
    this.deviceId = p.device_id || this.deviceId;
    this.sessionId = p.session_id || null;
    this.conversationId = p.conversation_id || null;
    const hb = Number(p.heartbeat_seconds || 0);
    if (hb > 0) this.heartbeatMs = hb * 1000;
    if (typeof p.reconnect_enabled === "boolean") this.reconnectEnabled = p.reconnect_enabled;
    if (typeof p.message_timeout === "number") this.messageTimeoutMs = p.message_timeout * 1000;
    if (typeof p.queue_ttl === "number") this.queueTtlMs = p.queue_ttl * 1000;

    this.authenticated = true;
    this.reauthAttempts = 0;
    this.failures = 0;
    this.setState("connected", `WAN ativo (${String(this.deviceId).slice(0, 8)}…)`);
    this.onAuth({
      device_id: this.deviceId,
      session_id: this.sessionId,
      conversation_id: this.conversationId,
      heartbeat_ms: this.heartbeatMs,
      reconnect_enabled: this.reconnectEnabled,
    });
    if (this.token) this._persist();
    this._scheduleHeartbeat();
  }

  _onErrorFrame(env) {
    const p = env.payload || {};
    const code = (p.error || "").toLowerCase();
    if (code === "core_offline") {
      this.authenticated = false;
      this.setState("core_unavailable", "Core offline pelo relé");
      this._scheduleReconnect("core_unavailable");
    } else if (code === "not_authenticated") {
      // Relé perdeu o atrelamento: tenta re-autorizar UMA vez nesta conexão.
      if (this.token && this.reauthAttempts < 3) {
        this.reauthAttempts += 1;
        this._authenticate();
      } else {
        this.setState("authentication_error", "sessão WAN não reconhecida");
      }
    } else if (code === "rate_limited") {
      this.onError(new Error("muitas mensagens; aguarde"));
    } else {
      this.onError(new Error(p.message || p.detail || "erro do relé"));
    }
  }

  _scheduleReconnect(stateDuringBackoff = "reconnecting") {
    if (this._reconnectTimer) return;
    if (!this.reconnectEnabled) {
      this.setState("offline", "reconexão desabilitada pelo Core");
      return;
    }
    this.failures += 1;
    const base = Math.min(this.maxBackoffMs, 1000 * 2 ** Math.min(this.failures - 1, 6));
    const delay = Math.round(base * (0.75 + Math.random() * 0.5));
    this.setState(stateDuringBackoff, `reconectando em ${Math.round(delay / 1000)}s`);
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      this._openSocket();
    }, delay);
  }

  _scheduleHeartbeat() {
    if (this._pingTimer) clearTimeout(this._pingTimer);
    this._pingTimer = setTimeout(() => {
      this._pingTimer = null;
      if (this.ws && this.authenticated) {
        this._sendEnvelope("heartbeat", { sent_at: Date.now() });
        this._scheduleHeartbeat();
      }
    }, this.heartbeatMs);
  }

  _sendEnvelope(type, payload, opts = {}) {
    if (!this.ws || this.ws.readyState !== 1) return false;
    const env = {
      version: 1,
      type,
      message_id: this.requestId(),
      request_id: opts.request_id || null,
      device_id: this.deviceId,
      timestamp: new Date().toISOString(),
      payload: payload || {},
    };
    try {
      this.ws.send(JSON.stringify(env));
      return true;
    } catch (err) {
      this._log("[wan] falha ao enviar:", err.message);
      return false;
    }
  }

  sendMessage(content, opts = {}) {
    if (!this.authenticated) throw new Error("sem conexão WAN autenticada");
    const requestId = opts.request_id || this.requestId();
    const payload = {
      target_device_id: this.deviceId,
      content: String(content),
      stream: !!opts.stream,
      tools: opts.tools !== false,
    };
    if (opts.session_id) payload.session_id = opts.session_id;
    this._sendEnvelope("message", payload, { request_id: requestId });
    return requestId;
  }

  sendComputerTask(content, opts = {}) {
    if (!this.authenticated) throw new Error("sem conexão WAN autenticada");
    const requestId = opts.request_id || this.requestId();
    this._sendEnvelope(
      "computer_task",
      { target_device_id: this.deviceId, content: String(content), autonomy: opts.autonomy },
      { request_id: requestId },
    );
    return requestId;
  }

  sendApproval(approvalId, approved, opts = {}) {
    if (!this.authenticated) throw new Error("sem conexão WAN autenticada");
    const requestId = opts.request_id || this.requestId();
    this._sendEnvelope(
      "approval_respond",
      { target_device_id: this.deviceId, approval_id: approvalId, approved: !!approved },
      { request_id: requestId },
    );
    return requestId;
  }

  _persist() {
    try {
      this.io.save({
        device_id: this.deviceId,
        token: this.token,
        session_id: this.sessionId,
        conversation_id: this.conversationId,
      });
    } catch (err) {
      this._log("[wan] falha ao persistir credenciais:", err.message);
    }
  }

  async leave() {
    this._clearTimers();
    this._intentionalClose = true;
    if (this.ws) {
      try {
        if (this.ws.readyState === 1) {
          this._sendEnvelope("close", {});
          await new Promise((resolve) => {
            const onClose = () => {
              this.ws = null;
              resolve();
            };
            this.ws.addEventListener("close", onClose, { once: true });
            setTimeout(() => {
              this.ws.removeEventListener("close", onClose);
              this.ws = null;
              resolve();
            }, 500);
          });
        }
      } catch {
        /* noop */
      }
      this.ws = null;
    }
    this.setState("offline", "desconectado pelo usuário");
  }

  stop() {
    this._clearTimers();
    this._intentionalClose = true;
    if (this.ws) {
      const ws = this.ws;
      this.ws = null;
      try {
        ws.close(1000, "stop");
      } catch {
        /* noop */
      }
    }
  }

  _clearHandshake() {
    if (this._handshakeTimer) clearTimeout(this._handshakeTimer);
    this._handshakeTimer = null;
  }

  _clearTimers() {
    if (this._handshakeTimer) clearTimeout(this._handshakeTimer);
    if (this._pingTimer) clearTimeout(this._pingTimer);
    if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
    this._handshakeTimer = null;
    this._pingTimer = null;
    this._reconnectTimer = null;
  }
}

module.exports = { WanClient, DEFAULT_MAX_BACKOFF_MS, DEFAULT_CONNECT_TIMEOUT_MS };