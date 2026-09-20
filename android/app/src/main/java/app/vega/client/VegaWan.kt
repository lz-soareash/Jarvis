package app.vega.client

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import app.vega.client.security.WanCiphers
import app.vega.client.security.WanCredentialStore
import app.vega.client.security.WanSecretStore
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.math.min
import kotlin.random.Random

/**
 * WAN Gateway Client (Fase 24) — conectividade remota do VEGA Mobile.
 *
 * Espelho de desktop/wan.js: HELLO -> hello_ack -> AUTH (token OU código de
 * pareamento) -> auth_result -> connected. Heartbeat, reconnect com backoff,
 * estados offline/connecting/connected/reconnecting/authentication_error/
 * core_unavailable. O relé só transporta; TODA autoridade continua no Core.
 *
 * O token NUNCA vai em URL e NUNCA é logado. Credenciais (token/device id)
 * ficam criptografadas com AES-256-GCM + Android Keystore no SharedPreferences
 * ("vega_wan") — Fases 27.2/27.2.1 (F1 + fail-closed: sem plaintext nem como
 * fallback). Implementação: OkHttp WebSocket.
 */
class VegaWan(
    private val context: Context,
    private val scope: CoroutineScope,
) {
    /**
     * Fase 27.2.1 (fail-closed) — credenciais WAN NUNCA ficam em claro no
     * SharedPreferences ("vega_wan"). O token e o device id são gravados sob as
     * chaves `enc.wan_token`/`enc.wan_device_id` como blobs AES-256-GCM
     * ([WanCiphers]) com a chave do Android Keystore ([WanSecretStore]).
     *
     * Um valor legado em claro é MIGRADO na primeira leitura (lê → criptografa
     * → apaga o plaintext) SOMENTE com o Keystore disponível. Se a migração
     * falhar ou o Keystore estiver indisponível, o plaintext é REMOVIDO e a
     * leitura devolve nulo (fail-closed): nenhum segredo é preservado em
     * armazenamento inseguro — o usuário re-pareia.
     *
     * Falha de decriptação → nulo (nunca decripta parcial / nunca usa segredo
     * corrompido). A política é decidida em [WanCredentialStore] (pura, testada
     * em JVM).
     */
    private fun readCredential(prefKey: String): String? {
        val encryptedKey = "enc.$prefKey"
        return WanCredentialStore.read(
            keystoreAvailable = WanSecretStore.isAvailable(),
            encryptedBlob = { prefs.getString(encryptedKey, null) },
            legacyPlain = { prefs.getString(prefKey, null) },
            decrypt = { blob -> WanCiphers.decrypt(WanSecretStore.key(context), blob) },
            migrateToEncrypted = { plain -> writeSecretBlob(encryptedKey, plain) },
            removeLegacy = { prefs.edit().remove(prefKey).apply() },
            onLegacyRemovedFailClosed = {
                Log.w(logTag, "$prefKey removido (fail-closed): plaintext legado não migrado — re-pareamento necessário")
            },
        )
    }

    /**
     * Fase 27.2.1 (fail-closed) — retorna TRUE apenas quando a credencial foi
     * gravada como blob AES-256-GCM via Keystore. Sem Keystore / falha: NADA é
     * gravado em claro (plaintext legado é removido) e retorna FALSE — o
     * chamador registra o erro controlado.
     */
    private fun writeCredential(prefKey: String, value: String?): Boolean {
        val encryptedKey = "enc.$prefKey"
        val ok = WanCredentialStore.write(
            keystoreAvailable = WanSecretStore.isAvailable(),
            value = value,
            writeEncrypted = { plain -> writeSecretBlob(encryptedKey, plain) },
            removeAll = { prefs.edit().remove(prefKey).remove(encryptedKey).apply() },
            removeLegacy = { prefs.edit().remove(prefKey).apply() },
        )
        if (!ok) {
            // Fail-closed (Fase 27.2.1): credencial não protegida pelo Keystore
            // não é persistida — nunca cai para SharedPreferences em claro.
            Log.e(logTag, "credencial $prefKey NÃO persistida: Android Keystore indisponível/falha — nada em claro no disco (fail-closed)")
        }
        return ok
    }

    private fun writeSecretBlob(encryptedKey: String, value: String) {
        val blob = WanCiphers.encrypt(WanSecretStore.key(context), value)
        prefs.edit().putString(encryptedKey, blob).remove(encryptedKey.removePrefix("enc.")).apply()
    }

    private val prefs = context.getSharedPreferences("vega_wan", Context.MODE_PRIVATE)
    private val logTag = "vega-wan"

    var state: String = "offline" // offline | connecting | connected | reconnecting | authentication_error | core_unavailable
        private set
    var detail: String = ""
        private set
    var heartbeatsTotal: Int = 0
        private set

    val deviceId: String?
        get() = readCredential("wan_device_id")
    val hasToken: Boolean
        get() = !readCredential("wan_token").isNullOrBlank()

    /**
     * Fase 27.2.1 — TRUE quando a credencial do `auth_result` NÃO pôde ser
     * persistida com segurança (Android Keystore indisponível/falha). A sessão
     * atual segue em memória; após reiniciar o app não há token em disco e o
     * usuário re-pareia (nunca gravamos plaintext como fallback).
     */
    var storageDegraded: Boolean = false
        private set

    var onTurnFrame: ((type: String, payload: JSONObject, requestId: String) -> Unit)? = null
    var onMobileCommand: ((payload: JSONObject) -> Unit)? = null

    /** Fase 28.1 — credencial atual (em memória) para os endpoints HTTP remotos. */
    val authToken: String?
        get() = token

    private var ws: WebSocket? = null
    private var heartbeatJob: Job? = null
    private var reconnectJob: Job? = null
    private var failures = 0
    private var intentionalClose = false
    private var sawHelloAck = false
    private var reauthAttempts = 0
    private var heartbeatsNoAck = 0
    private var revokedTerminal = false
    private var lastUrl: String = ""
    private var heartbeatMs: Long = 30_000
    private var token: String? = null
    private var pairingCode: String? = null
    var sessionId: String? = null
        private set
    var conversationId: String? = null
        private set

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private fun setState(newState: String, newDetail: String) {
        state = newState
        detail = newDetail
        Log.i(logTag, "state=$newState $newDetail")
    }

    /** Pré-configura credenciais antes de conectar (ex.: tela de pareamento). */
    fun configure(token: String?, pairingCode: String?, deviceId: String?) {
        this.token = token ?: readCredential("wan_token")
        this.pairingCode = pairingCode?.takeIf { it.isNotBlank() }
        if (deviceId != null) this.lastDeviceId = deviceId
    }

    private var lastDeviceId: String? = null

    fun connect(wanUrl: String) {
        // Bugfix LAN/WAN: o endpoint WAN é validado de forma centralizada antes
        // de qualquer socket — rejeita IP privado/localhost e (em produção)
        // qualquer esquema que não seja wss://. NUNCA usa a LAN como fallback.
        val v = WanEndpoint.validate(wanUrl, requireTls = !BuildConfig.DEBUG)
        if (!v.valid) {
            setState("offline", "WAN inválida — ${v.reason}")
            return
        }
        val target = v.normalized
        cancelTimers()
        lastUrl = target
        token = token ?: readCredential("wan_token")
        if (lastDeviceId == null) lastDeviceId = readCredential("wan_device_id")
        intentionalClose = false
        revokedTerminal = false
        reauthAttempts = 0
        heartbeatsNoAck = 0
        openSocket()
    }

    fun disconnect() {
        cancelTimers()
        intentionalClose = true
        ws?.close(1000, "leave") ?: run {
            setState("offline", "desconectado pelo usuário")
        }
    }

    fun stop() {
        cancelTimers()
        intentionalClose = true
        ws?.cancel()
        ws = null
        setState("offline", "desconectado pelo usuário")
    }

    /** Revoga credenciais WAN locais (token/sessão) — paridade com o Core. */
    fun revoke() {
        prefs.edit().clear().apply()
        token = null
        sessionId = null
        conversationId = null
        storageDegraded = false
        stop()
        setState("offline", "credenciais revogadas no dispositivo")
    }

    private fun openSocket() {
        setState("connecting", "conectando ao relé…")
        val wsRef = http.newWebSocket(
            Request.Builder().url(lastUrl).build(),
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    sawHelloAck = false
                    heartbeatsNoAck = 0
                    sendEnvelope(
                        "hello",
                        JSONObject().apply {
                            put("role", "mobile")
                            put("protocol_version", 1)
                            put("client_version", BuildConfig.VERSION_NAME)
                            put("device_type", "mobile")
put("capabilities", deviceCapabilities())
                        },
                    )
                    armHandshakeTimeout(webSocket)
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    onFrame(text)
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(1000, null)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    cancelHandshakeTimeout()
                    ws = null
                    if (intentionalClose) {
                        setState("offline", "desconectado pelo usuário")
                        return
                    }
                    if (revokedTerminal) return  // terminal — estado já informado
                    if (sawHelloAck) {
                        setState("reconnecting", "conexão caiu (código $code)")
                    } else {
                        setState("core_unavailable", "relé indisponível (código $code)")
                    }
                    scheduleReconnect(if (sawHelloAck) "reconnecting" else "core_unavailable")
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    cancelHandshakeTimeout()
                    if (intentionalClose) {
                        setState("offline", "desconectado pelo usuário")
                        return
                    }
                    if (revokedTerminal) return  // terminal — estado já informado
                    if (sawHelloAck) {
                        setState("reconnecting", "conexão caiu (${t.message})")
                    } else {
                        setState("core_unavailable", "relé indisponível (${t.message})")
                    }
                    scheduleReconnect(if (sawHelloAck) "reconnecting" else "core_unavailable")
                }
            },
        )
        ws = wsRef
    }

    private fun armHandshakeTimeout(socket: WebSocket) {
        reconnectJob = scope.launch {
            delay(10_000)
            if (!intentionalClose) socket.close(1002, "handshake timeout")
        }
    }

    private fun cancelHandshakeTimeout() {
        reconnectJob?.cancel()
        reconnectJob = null
    }

    private fun scheduleReconnect(stateDuringBackoff: String) {
        if (reconnectJob != null) return
        failures += 1
        val base = min(60_000.0, 1000.0 * (1 shl min(failures - 1, 6))).toLong()
        val delayMs = (base * (0.75 + Random.nextDouble() * 0.5)).toLong()
        setState(stateDuringBackoff, "reconectando em ${(delayMs / 1000)}s")
        reconnectJob = scope.launch {
            delay(delayMs)
            reconnectJob = null
            if (!intentionalClose) openSocket()
        }
    }

    private fun onFrame(text: String) {
        val env = try {
            JSONObject(text)
        } catch (e: Exception) {
            Log.w(logTag, "frame não-JSON ignorado")
            return
        }
        when (env.optString("type")) {
            "hello_ack" -> onHelloAck(env)
            "auth_result" -> onAuthResult(env)
            "heartbeat_ack" -> {
                val p = env.optJSONObject("payload")
                val ok = p?.optBoolean("ok", false) ?: false
                if (ok) {
                    heartbeatsNoAck = 0
                    failures = 0
                    Log.i(logTag, "heartbeat_ack ok")
                } else {
                    // Fase 24.1 — sinalização honesta do Core: revogação é
                    // terminal; sessão não reconhecida tenta re-autenticar.
                    heartbeatsNoAck = 0
                    val code = (p?.optString("error") ?: "not_authenticated").lowercase()
                    if (code == "revoked") {
                        onRevokedFrame("credenciais WAN revogadas pelo Core")
                    } else {
                        onNotAuthenticatedFrame()
                    }
                }
            }
            "message_result", "agent_event" -> {
                val p = env.optJSONObject("payload")
                if (p != null) {
                    onTurnFrame?.invoke(env.optString("type"), p, env.optString("request_id"))
                }
            }
            "mobile_command" -> {
                val p = env.optJSONObject("payload")
                if (p != null) onMobileCommand?.invoke(p)
            }
            "error" -> onErrorFrame(env)
            else -> Log.d(logTag, "tipo não tratado: ${env.optString("type")}")
        }
    }

    private fun onHelloAck(env: JSONObject) {
        cancelHandshakeTimeout()
        val p = env.optJSONObject("payload")
        if (p == null || !p.optBoolean("ok", false)) {
            onError("relé rejeitou o hello")
            scheduleReconnect(if (sawHelloAck) "reconnecting" else "core_unavailable")
            return
        }
        sawHelloAck = true
        reauthAttempts = 0
        heartbeatsNoAck = 0
        authenticate()
    }

    private fun authenticate() {
        val payload = JSONObject()
        val code = pairingCode?.trim().orEmpty()
        if (code.isNotEmpty()) {
            payload.put("pairing_code", code)
            payload.put("device_name", "VEGA Mobile")
            payload.put("device_type", "mobile")
            payload.put("platform", "android")
            payload.put("client_version", BuildConfig.VERSION_NAME)
            payload.put("capabilities", deviceCapabilities())
            payload.put("transport_meta", transportMeta())
            sendEnvelope("auth", payload, requestId = UUID.randomUUID().toString())
        } else {
            val tok = token.orEmpty()
            if (tok.isEmpty()) {
                setState("authentication_error", "sem token nem código de pareamento")
                return
            }
            payload.put("token", tok)
            lastDeviceId?.let { payload.put("claimed_device_id", it) }
            payload.put("platform", "android")
            payload.put("client_version", BuildConfig.VERSION_NAME)
            payload.put("capabilities", deviceCapabilities())
            payload.put("transport_meta", transportMeta())
            sendEnvelope("auth", payload, requestId = UUID.randomUUID().toString())
        }
    }

    private fun onAuthResult(env: JSONObject) {
        val p = env.optJSONObject("payload")
        if (p == null || !p.optBoolean("ok", false)) {
            setState("authentication_error", p?.optString("reason", "autenticação negada pelo Core").orEmpty())
            Log.w(logTag, "auth negada: $detail")
            return
        }
        if (p.has("token")) token = p.optString("token")
        p.optString("device_id").takeIf { it.isNotBlank() }?.let { lastDeviceId = it }
        sessionId = p.optString("session_id").ifBlank { null }
        conversationId = p.optString("conversation_id").ifBlank { null }
        val hb = p.optInt("heartbeat_seconds", 0)
        if (hb > 0) heartbeatMs = hb * 1000L
        token?.let { persist(it) }
        failures = 0
        reauthAttempts = 0
        heartbeatsNoAck = 0
        setState("connected", "WAN ativo (${lastDeviceId?.take(8)}…)")
        startHeartbeat()
    }

    private fun onErrorFrame(env: JSONObject) {
        val p = env.optJSONObject("payload")
        val code = p?.optString("error", "").orEmpty().lowercase()
        when {
            code == "core_offline" -> {
                setState("core_unavailable", "Core offline pelo relé")
                scheduleReconnect("core_unavailable")
            }
            code == "not_authenticated" -> {
                onNotAuthenticatedFrame()
            }
            code == "rate_limited" -> {
                Log.w(logTag, "muitas mensagens; aguarde")
            }
            else -> onError(p?.optString("message") ?: "erro do relé")
        }
    }

    private fun onError(message: String) {
        Log.w(logTag, "wan error: $message")
    }

    private fun onNotAuthenticatedFrame() {
        // Paridade com o Desktop: re-autoriza até 3x com o token salvo.
        val tok = token.orEmpty()
        if (tok.isNotEmpty() && reauthAttempts < 3) {
            reauthAttempts += 1
            authenticate()
            return
        }
        setState("authentication_error", "sessão WAN não reconhecida")
    }

    private fun onRevokedFrame(reason: String) {
        // Terminal nesta sessão do processo: informa e encerra, sem reconectar.
        cancelTimers()
        revokedTerminal = true
        ws?.close(1000, "revoked")
        setState("authentication_error", reason)
    }

    private fun transportMeta(): JSONObject =
        JSONObject()
            .put("app", "android")
            .put("transport", if (lastUrl.startsWith("ws://")) "ws" else "wss")

    /** Fase 25 — capabilities declaradas no WAN (unificadas com o executor). */
    private fun deviceCapabilities(): org.json.JSONArray {
        val caps = mobileCapabilityNames().toMutableList()
        caps.addAll(listOf("chat", "tts", "notifications"))
        return org.json.JSONArray(caps)
    }

    fun sendMessage(content: String, opts: JSONObject = JSONObject(), requestId: String? = null): String {
        if (state != "connected") throw IllegalStateException("sem conexão WAN autenticada")
        val rid = requestId ?: UUID.randomUUID().toString()
        val payload = JSONObject()
            .put("target_device_id", lastDeviceId)
            .put("content", content)
            .put("stream", opts.optBoolean("stream", false))
            .put("tools", !opts.optBoolean("tools", false))
        // Fase 27.2 (F3) — falha real de envio propaga: `send()` do ChatViewModel
        // encerra a mensagem com erro visível e o requestId nunca vira órfão
        // (o turno não é correlacionado sem envio confirmado). Fase 28.1: o
        // `requestId` pode vir de fora — o view model registra a correlação ANTES
        // do envio, matando a corrida "frame chega antes do registerTurn".
        val sent = sendEnvelope("message", payload, rid)
        if (!sent) throw IllegalStateException("WAN não transmitiu a mensagem (socket fechado)")
        return rid
    }

    fun sendComputerTask(content: String, autonomy: String? = null): String {
        if (state != "connected") throw IllegalStateException("sem conexão WAN autenticada")
        val requestId = UUID.randomUUID().toString()
        val sent = sendEnvelope(
            "computer_task",
            JSONObject()
                .put("target_device_id", lastDeviceId)
                .put("content", content)
                .apply { if (autonomy != null) put("autonomy", autonomy) },
            requestId,
        )
        if (!sent) throw IllegalStateException("WAN não transmitiu a tarefa (socket fechado)")
        return requestId
    }

    fun sendApproval(approvalId: String, approved: Boolean, requestId: String? = null): String {
        if (state != "connected") throw IllegalStateException("sem conexão WAN autenticada")
        val rid = requestId ?: UUID.randomUUID().toString()
        val sent = sendEnvelope(
            "approval_respond",
            JSONObject().put("target_device_id", lastDeviceId).put("approval_id", approvalId).put("approved", approved),
            rid,
        )
        if (!sent) throw IllegalStateException("WAN não transmitiu a decisão (socket fechado)")
        return rid
    }

    /** Gera um request_id novo (usado correlacionar ANTES do envio WAN). */
    fun newRequestId(): String = UUID.randomUUID().toString()

    /** Fase 25 — emite COMMAND_RESULT do executor ao Core (status canônico). */
    fun sendCommandResult(
        commandId: String,
        status: String,
        result: JSONObject? = null,
        error: String? = null,
        startedAt: String? = null,
        finishedAt: String? = null,
    ) {
        if (state != "connected") {
            Log.w(logTag, "command_result descartado: sem conexão WAN (command=$commandId)")
            return
        }
        val payload = JSONObject()
            .put("command_id", commandId)
            .put("status", status)
            .apply {
                if (result != null) put("result", result)
                if (error != null) put("error", error)
                if (startedAt != null) put("started_at", startedAt)
                if (finishedAt != null) put("finished_at", finishedAt)
            }
        sendEnvelope("command_result", payload, requestId = commandId)
    }

    private fun startHeartbeat() {
        heartbeatJob?.cancel()
        heartbeatJob = scope.launch {
            while (state == "connected" && !intentionalClose && !revokedTerminal) {
                delay(heartbeatMs)
                if (state == "connected" && !intentionalClose && !revokedTerminal) {
                    // Fase 24.1 — heartbeat sem ACK (half-open) fecha e reconecta.
                    heartbeatsNoAck += 1
                    if (heartbeatsNoAck >= 3) {
                        ws?.close(1000, "heartbeat timeout")
                        break
                    }
                    sendEnvelope("heartbeat", JSONObject().put("sent_at", System.currentTimeMillis()))
                    heartbeatsTotal++
                }
            }
        }
    }

    private fun persist(tok: String) {
        // F1 — token e device id via Keystore; sessão/conversa são ids de sessão
        // (não credenciais) e seguem no SharedPreferences como antes.
        val tokenSaved = writeCredential("wan_token", tok)
        val deviceSaved = writeCredential("wan_device_id", lastDeviceId)
        prefs.edit()
            .putString("wan_session_id", sessionId)
            .putString("wan_conversation_id", conversationId)
            .apply()
        if (!tokenSaved || !deviceSaved) {
            // Fail-closed (Fase 27.2.1): a sessão atual continua (autenticada
            // em memória), mas a credencial NÃO foi gravada — nunca em claro.
            // Após reiniciar o app o usuário precisará re-parear.
            storageDegraded = true
            Log.e(logTag, "WAN ativo em memória, mas credencial NÃO protegida pelo Keystore — re-pareamento necessário após reinício (nada em claro)")
        } else {
            storageDegraded = false
        }
    }

    /**
     * Envia um envelope WAN. Fase 27.2 (F3): devolve TRUE apenas quando o
     * OkHttp `WebSocket.send` realmente enfileirou a mensagem (retorna false
     * em socket fechado/falho, sem lançar). Missões críticas (message/
     * computer_task/approval) checam o retorno e propagam; heartbeat/auth/
     * command_result seguem best-effort (o protocolo já cobre retry/timeout).
     */
    private fun sendEnvelope(type: String, payload: JSONObject, requestId: String? = null): Boolean {
        val socket = ws ?: return false
        val env = JSONObject()
            .put("version", 1)
            .put("type", type)
            .put("message_id", UUID.randomUUID().toString())
            .put("request_id", requestId ?: JSONObject.NULL)
            .put("device_id", lastDeviceId ?: JSONObject.NULL)
            .put("timestamp", java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", java.util.Locale.US).format(java.util.Date()))
            .put("payload", payload)
        return try {
            socket.send(env.toString())
        } catch (e: Exception) {
            Log.w(logTag, "falha ao enviar: ${e.message}")
            false
        }
    }

    private fun cancelTimers() {
        reconnectJob?.cancel()
        reconnectJob = null
        heartbeatJob?.cancel()
        heartbeatJob = null
    }

    companion object {
        /**
         * Usado apenas para lembrar a URL pré-configurada (ex.: tela de
         * pareamento). Delegado ao validador central: retorna "" se inválida.
         */
        fun normalizeWanUrl(raw: String): String = WanEndpoint.normalizePreset(raw, requireTls = !BuildConfig.DEBUG)

        /** Fase 25 — nomes canônicos de capabilities de dispositivo (Registry). */
        fun mobileCapabilityNames(): List<String> =
            app.vega.client.model.MobileCapabilities.ALL.map { it.name }
    }
}