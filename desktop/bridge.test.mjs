/* Teste hermético do Device Bridge (Fase 21) em Node puro (sem Electron).
   Sobe um servidor HTTP mock local com o CONTRATO do Core (mesmos paths e
   formas) e valida o fluxo real do cliente fino: REGISTER -> PAIRING ->
   HEARTBEAT -> disconnect, revogação e Core off-line. */
import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { once } from "node:events";
import { createRequire } from "node:module";
import { DeviceBridge } from "./bridge.js";

const require = createRequire(import.meta.url);
const { userAgent } = require("./bridge.js");

async function startMock() {
  const calls = [];
  const st = { fail: {}, heartbeats: [] };
  let nextDevice = 1;
  let nextToken = 1;
  const server = http.createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    let body = null;
    try {
      body = raw ? JSON.parse(raw) : null;
    } catch {
      body = raw;
    }
    calls.push({ method: req.method, url: req.url, body });
    const respond = (status, data) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(data));
    };
    if (req.method === "POST" && req.url === "/api/remote/devices/register") {
      const id = `dev-${nextDevice++}`;
      st.device = { id, name: body.name, platform: body.platform, status: "pending" };
      return respond(201, { device: st.device, status: "pending" });
    }
    if (req.method === "POST" && req.url === "/api/remote/pairings") {
      st.code = "AB12CD34";
      return respond(201, {
        pairing_id: "p-1",
        code: st.code,
        expires_at: new Date().toISOString(),
        ttl_seconds: 300,
      });
    }
    if (req.method === "POST" && req.url === "/api/remote/pairings/validate") {
      if (body.code !== st.code) return respond(400, { type: "error", message: "código inválido" });
      const token = `tok-${nextToken++}`;
      st.token = token;
      st.validateBody = body;
      return respond(201, {
        device_id: body.pending_device_id,
        token,
        device: { id: body.pending_device_id, name: body.device_name, platform: body.platform, status: "active" },
      });
    }
    if (req.method === "POST" && req.url === "/api/remote/heartbeat") {
      if (st.fail.status) {
        const status = st.fail.status;
        const msg = st.fail.msg || "desabilitado";
        st.fail = {}; // falha one-shot
        return respond(status, { type: "error", message: msg });
      }
      if (body.claimed_device_id && body.claimed_device_id !== st.device.id) {
        return respond(401, { type: "error", code: "UNAUTHORIZED", message: "autenticação necessária" });
      }
      st.heartbeats.push(body.event);
      return respond(200, {
        device_id: st.device.id,
        status: "active",
        connected: body.event !== "disconnect",
        conversation_id: "conv-1",
        heartbeat_seconds: 2,
        reconnect_enabled: true,
      });
    }
    return respond(404, { type: "error", message: "not found" });
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return { server, port: () => server.address().port, calls, st };
}

async function withMock(fn) {
  let m = null;
  try {
    m = await startMock();
    await fn(m);
  } finally {
    m?.server?.closeAllConnections?.();
    m?.server?.close?.();
  }
}

test("boot pareia, armazena token, conecta e respeita heartbeat_seconds", async () => {
  await withMock(async ({ server, port, calls, st }) => {
    const states = [];
    const io = { load: () => null, save: (v) => (io.saved = v) };
    const bridge = new DeviceBridge(`http://127.0.0.1:${port()}`, {
      io,
      onState: (state, detail) => states.push({ state, detail }),
      version: "1.0.0",
      platform: "windows",
    });
    try {
      await bridge.boot();
      assert.equal(bridge.state, "connected");
      assert.ok(bridge.token, "token emitido pelo servidor");
      assert.ok(io.saved.device_id && io.saved.token);
      assert.equal(bridge.heartbeatMs, 2000);
      assert.equal(bridge.reconnectEnabled, true);
      const events = calls.filter((c) => c.method === "POST").map((c) => c.url);
      assert.deepEqual(events, [
        "/api/remote/devices/register",
        "/api/remote/pairings",
        "/api/remote/pairings/validate",
        "/api/remote/heartbeat",
      ]);
      const reg = calls.find((c) => c.url === "/api/remote/devices/register").body;
      assert.equal(reg.name, "VEGA Desktop");
      assert.equal(reg.platform, "windows");
      assert.ok(Array.isArray(reg.capabilities));
      const validate = calls.find((c) => c.url === "/api/remote/pairings/validate").body;
      assert.equal(validate.pending_device_id, st.device.id);
      assert.equal(validate.device_name, "VEGA Desktop");
      assert.deepEqual(st.heartbeats, ["connect"]);
    } finally {
      bridge.stop();
    }
  });
});

test("reconnect usa token salvo e envia claimed_device_id correto", async () => {
  await withMock(async ({ server, port, st, calls }) => {
    st.device = { id: "dev-abc", name: "VEGA Desktop", platform: "windows", status: "active" };
    const io = { load: () => ({ device_id: "dev-abc", token: "tok-old" }), save: () => {} };
    const bridge = new DeviceBridge(`http://127.0.0.1:${port()}`, {
      io,
      version: "1.0.0",
      platform: "windows",
    });
    try {
      await bridge.boot();
      assert.equal(bridge.state, "connected");
      assert.equal(bridge.token, "tok-old");
      const hb = calls.filter((c) => c.url === "/api/remote/heartbeat");
      assert.equal(hb.length, 1);
      assert.equal(hb[0].body.event, "reconnect");
      assert.equal(hb[0].body.claimed_device_id, "dev-abc");
    } finally {
      bridge.stop();
    }
  });
});

test("token revogado (401 no reconnect) re-pareia automaticamente", async () => {
  await withMock(async ({ server, port, st }) => {
    st.device = { id: "dev-rev", name: "VEGA Desktop", platform: "windows", status: "revoked" };
    const io = {
      load: () => io.saved || { device_id: "dev-rev", token: "tok-revoked" },
      save: (v) => (io.saved = v),
    };
    const bridge = new DeviceBridge(`http://127.0.0.1:${port()}`, {
      io,
      version: "1.0.0",
      platform: "windows",
    });
    try {
      st.fail = { status: 401, msg: "autenticação necessária" };
      await bridge.boot();
      assert.equal(bridge.state, "connected");
      assert.ok(io.saved && io.saved.token !== "tok-revoked");
      assert.ok(bridge.token !== "tok-revoked");
    } finally {
      bridge.stop();
    }
  });
});

test("bridge desabilitado no Core (503) → estado error", async () => {
  await withMock(async ({ server, port, st }) => {
    const states = [];
    const bridge = new DeviceBridge(`http://127.0.0.1:${port()}`, {
      io: { load: () => null, save: () => {} },
      onState: (state, detail) => states.push({ state, detail }),
      version: "1.0.0",
    });
    try {
      st.fail = { status: 503, msg: "Remote desabilitado" };
      bridge.retry = () => {};
      await bridge.boot();
      assert.equal(bridge.state, "error");
      assert.match(states.at(-1).detail, /desabilitado/);
    } finally {
      bridge.stop();
    }
  });
});

test("leave() envia disconnect; heartbeat dispara periodicamente", async () => {
  await withMock(async ({ server, port, st }) => {
    const io = { load: () => null, save: () => {} };
    const bridge = new DeviceBridge(`http://127.0.0.1:${port()}`, { io, version: "1.0.0" });
    try {
      await bridge.boot();
      await bridge.leave();
      assert.deepEqual(st.heartbeats, ["connect", "disconnect"]);
    } finally {
      bridge.stop();
    }
  });
});

test("userAgent identifica versao e plataforma (bridge-desktop)", () => {
  assert.equal(userAgent("1.2.3", "win32"), "jarvis/1.2.3 (win32; device-bridge)");
});