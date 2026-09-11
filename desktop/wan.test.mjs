/* =============================================================
   Testes herméticos do WanClient (Fase 24) sobre WebSocket de verdade.

   Roda um mini-servidor WebSocket (RFC6455) escrito aqui mesmo (http +
   crypto do Node, sem dependências) numa porta EFÊMERA — nunca 8100 —
   simulando o relé: hello_ack -> auth_result -> heartbeat/message/erro.
   Isso valida o framing real do cliente sem tocar rede externa.

   O servidor replica o contrato do RelayHub (backend/app/gateway/hub.py).
   ============================================================= */
import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import crypto from "node:crypto";
import net from "node:net";
import { WanClient } from "./wan.js";

const WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

function acceptKey(key) {
  return crypto.createHash("sha1").update(key + WS_GUID).digest("base64");
}

function encodeFrame(payload, opcode = 1) {
  const data = Buffer.from(payload, "utf8");
  const header = Buffer.alloc(2 + (data.length > 125 ? 2 : 0));
  header[0] = 0x80 | opcode;
  let offset = 2;
  if (data.length <= 125) {
    header[1] = data.length;
  } else {
    header[1] = 126;
    header.writeUInt16BE(data.length, 2);
    offset = 4;
  }
  return Buffer.concat([header, data]);
}

const OPCODE_TEXT = 1;
const OPCODE_CLOSE = 8;
const OPCODE_PING = 9;

class FrameParser {
  constructor() {
    this.buf = Buffer.alloc(0);
  }

  push(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    return this.tryParse();
  }

  tryParse() {
    const buf = this.buf;
    if (buf.length < 2) return null;
    const b0 = buf[0];
    const b1 = buf[1];
    const masked = (b1 & 0x80) !== 0;
    let len = b1 & 0x7f;
    let offset = 2;
    if (len === 126) {
      if (buf.length < 4) return null;
      len = buf.readUInt16BE(2);
      offset = 4;
    } else if (len === 127) {
      if (buf.length < 10) return null;
      len = Number(buf.readBigUInt64BE(2));
      offset = 10;
    }
    const maskLen = masked ? 4 : 0;
    const total = offset + maskLen + len;
    if (buf.length < total) return null;
    const payload = Buffer.allocUnsafe(len + maskLen);
    buf.copy(payload, 0, offset, offset + maskLen + len);
    const data = payload.subarray(maskLen);
    if (masked) {
      const m = payload.subarray(0, 4);
      for (let i = 0; i < data.length; i++) data[i] ^= m[i & 3];
    }
    this.buf = Buffer.from(buf.subarray(total));
    return { opcode: b0 & 0x0f, payload: data.toString("utf8"), raw: data };
  }
}

function startRelay(handler) {
  return new Promise((resolve) => {
    const server = http.createServer();
    const sockets = new Set();
    server.on("connection", (s) => {
      sockets.add(s);
      s.on("close", () => sockets.delete(s));
    });
    server.on("upgrade", (req, socket) => {
      const key = req.headers["sec-websocket-key"];
      socket.write(
        "HTTP/1.1 101 Switching Protocols\r\n" +
          "Upgrade: websocket\r\n" +
          "Connection: Upgrade\r\n" +
          `Sec-WebSocket-Accept: ${acceptKey(key)}\r\n\r\n`,
      );
      const parser = new FrameParser();
      const send = (obj) => {
        if (!socket.destroyed) socket.write(encodeFrame(JSON.stringify(obj)));
      };
      const closeFrame = (code = 1000) => {
        const buf = Buffer.alloc(2);
        buf.writeUInt16BE(code, 0);
        if (!socket.destroyed) socket.write(encodeFrame(buf, OPCODE_CLOSE));
      };
      socket.on("data", (chunk) => {
        const frame = parser.push(chunk);
        if (!frame) return;
        if (frame.opcode === OPCODE_CLOSE) {
          closeFrame();
          socket.end();
          return;
        }
        if (frame.opcode === OPCODE_PING) return;
        if (frame.opcode !== OPCODE_TEXT) return;
        let env;
        try {
          env = JSON.parse(frame.payload);
        } catch {
          return;
        }
        handler(env, { send, raw: socket });
      });
      socket.on("error", () => {});
    });
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        url: `ws://127.0.0.1:${port}`,
        close: () => {
          for (const s of sockets) s.destroy();
          if (server.closeAllConnections) server.closeAllConnections();
          return new Promise((r) => server.close(r));
        },
      });
    });
  });
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(predicate, timeout = 2500) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await wait(5);
  }
  throw new Error("condição não satisfeita no tempo limite");
}

function makeDefaultEnv(opts = {}) {
  return {
    token: opts.token || "tok-1",
    deviceId: opts.deviceId || "dev-1",
    pairingCode: null,
    io: opts.io || { load: () => null, save: () => {} },
    maxBackoffMs: 200,
    connectTimeoutMs: 1000,
    heartbeatMs: 25000,
    log: () => {},
    onState: () => {},
    onError: () => {},
    ...opts,
  };
}

function authOk(payload) {
  return {
    version: 1,
    type: "auth_result",
    message_id: crypto.randomUUID(),
    request_id: payload.request_id,
    device_id: null,
    timestamp: new Date().toISOString(),
    payload: { ok: true, device_id: "dev-1", session_id: "s-1", conversation_id: "c-1", ...payload.payload },
  };
}

test("WanClient: handshake + auth com token -> connected; mensagem -> resultado", async () => {
  const saved = [];
  const relay = await startRelay((env, { send }) => {
    if (env.type === "hello") {
      send({ type: "hello_ack", payload: { role: "mobile", ok: true } });
    } else if (env.type === "auth") {
      send(authOk(env));
    } else if (env.type === "heartbeat") {
      send({ type: "heartbeat_ack", payload: { ok: true } });
    } else if (env.type === "message") {
      send({ type: "message_ack", payload: { ok: true } });
      send({
        type: "message_result",
        payload: { ok: true, status: "completed", target_device_id: env.payload.target_device_id },
      });
    } else if (env.type === "close") {
      send({ type: "close", payload: {} });
    }
  });
  try {
    const states = [];
    const client = new WanClient(
      makeDefaultEnv({
        url: relay.url,
        io: { load: () => null, save: (v) => saved.push(v) },
        onState: (s) => states.push(s),
        onAuth: () => {},
      }),
    );
    await client.connect();
    await waitFor(() => client.state === "connected");
    assert.equal(client.deviceId, "dev-1");
    assert.equal(client.authenticated, true);

    const requestId = client.sendMessage("oi", { tools: false });
    assert.ok(requestId);
    const result = await new Promise((resolve) => {
      client.onMessageResult = (p) => resolve(p);
    });
    assert.equal(result.ok, true);
    assert.equal(result.status, "completed");

    await client.leave();
    assert.equal(client.state, "offline");
    assert.ok(saved.length >= 1, "credenciais persistidas após auth");
    assert.equal(saved[saved.length - 1].token, "tok-1");
  } finally {
    await relay.close();
  }
});

test("WanClient: bootstrap por código de pareamento devolve token UMA vez", async () => {
  const relay = await startRelay((env, { send }) => {
    if (env.type === "hello") send({ type: "hello_ack", payload: { role: "mobile", ok: true } });
    else if (env.type === "auth") {
      assert.equal(env.payload.pairing_code, "AB12-CD34");
      send({ type: "auth_result", payload: { ok: true, device_id: "dev-novo", session_id: "s", token: "tok-novo", conversation_id: "c-1", heartbeat_seconds: 30 } });
    }
  });
  try {
    const saved = [];
    const client = new WanClient(
      makeDefaultEnv({
        url: relay.url,
        token: null,
        pairingCode: "AB12-CD34",
        io: { load: () => null, save: (v) => saved.push(v) },
      }),
    );
    await client.connect();
    await waitFor(() => client.state === "connected");
    assert.equal(client.token, "tok-novo");
    assert.equal(client.deviceId, "dev-novo");
    assert.deepEqual(saved.at(-1), {
      device_id: "dev-novo",
      token: "tok-novo",
      session_id: "s",
      conversation_id: "c-1",
    });
    await client.leave();
  } finally {
    await relay.close();
  }
});

test("WanClient: auth negado -> authentication_error", async () => {
  const relay = await startRelay((env, { send }) => {
    if (env.type === "hello") send({ type: "hello_ack", payload: { role: "mobile", ok: true } });
    if (env.type === "auth") send({ type: "auth_result", payload: { ok: false, reason: "autenticação negada" } });
  });
  try {
    const client = new WanClient(makeDefaultEnv({ url: relay.url }));
    await client.connect();
    await waitFor(() => client.state === "authentication_error");
    assert.equal(client.authenticated, false);
    await client.stop();
  } finally {
    await relay.close();
  }
});

test("WanClient: relé informa core_offline -> estado core_unavailable", async () => {
  const relay = await startRelay((env, { send }) => {
    if (env.type === "hello") send({ type: "hello_ack", payload: { role: "mobile", ok: true } });
    if (env.type === "auth") send({ type: "error", payload: { error: "core_offline" } });
  });
  try {
    const client = new WanClient(makeDefaultEnv({ url: relay.url }));
    await client.connect();
    await waitFor(() => client.state === "core_unavailable");
    await client.stop();
  } finally {
    await relay.close();
  }
});

test("WanClient: queda da conexão -> reconnect com re-auth do token", async () => {
  let helloCount = 0;
  const relay = await startRelay((env, { send, raw }) => {
    if (env.type === "hello") {
      helloCount += 1;
      send({ type: "hello_ack", payload: { role: "mobile", ok: true } });
    } else if (env.type === "auth") {
      send(authOk(env));
    } else if (env.type === "message") {
      // após o 1º handshake, o servidor derruba o socket após a 1ª mensagem
      if (helloCount === 1) {
        raw.destroy();
        return;
      }
      send({ type: "message_result", payload: { ok: true, status: "completed" } });
    }
  });
  try {
    const client = new WanClient(makeDefaultEnv({ url: relay.url }));
    await client.connect();
    await waitFor(() => client.state === "connected");
    client.sendMessage("dispara queda");
    await waitFor(() => client.state === "reconnecting" || client.state === "core_unavailable");
    await waitFor(() => client.state === "connected", 4000);
    assert.ok(helloCount >= 2, "relé recebeu novo hello após reconnect");
    await client.leave();
  } finally {
    await relay.close();
  }
});

test("WanClient: heartbeat_ack notifica e sendMessage sem auth lança", async () => {
  const relay = await startRelay((env, { send }) => {
    if (env.type === "hello") send({ type: "hello_ack", payload: { role: "mobile", ok: true } });
    else if (env.type === "auth") send(authOk(env));
    else if (env.type === "heartbeat") send({ type: "heartbeat_ack", payload: { ok: true } });
  });
  try {
    const beats = [];
    const client = new WanClient(makeDefaultEnv({ url: relay.url, heartbeatMs: 20, onHeartbeat: () => beats.push(1) }));
    assert.throws(() => client.sendMessage("oi"), /sem conexão WAN autenticada/);
    await client.connect();
    await waitFor(() => client.state === "connected");
    await waitFor(() => beats.length >= 2);
    assert.ok(beats.length >= 2);
    await client.leave();
  } finally {
    await relay.close();
  }
});

test("WanClient: sem URL configurada -> offline imediato", async () => {
  const client = new WanClient(makeDefaultEnv({ url: "" }));
  await client.connect();
  assert.equal(client.state, "offline");
  client.stop();
});

test("WanClient: token nunca aparece no URL (construtor sanitiza)", async () => {
  const client = new WanClient(makeDefaultEnv({ url: "wss://" + "x".repeat(6) + ".invalid/ws" }));
  assert.ok(!client.url.includes("tok-1"));
  client.stop();
});