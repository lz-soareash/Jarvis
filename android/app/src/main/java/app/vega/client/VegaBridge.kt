package app.vega.client

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import kotlin.math.min

/**
 * Device Bridge mobile â€” UMA IA, MÃšLTIPLOS CLIENTES (Fase 21).
 *
 * Cliente fino espelhando desktop/bridge.js: REGISTER (device PENDING com id do
 * servidor) â†’ PAIRING (ancora o registro pendente com o cÃ³digo criado aqui em
 * POST /api/remote/pairings) â†’ HEARTBEAT (connect/reconnect/heartbeat/disconnect).
 * O token fica em SharedPreferences; device_id Ã© sempre emitido pelo Core.
 * Nenhum segundo AI Core/MemÃ³ria/PermissÃµes vive neste APK.
 */
class VegaBridge(
    private val context: Context,
    var coreUrl: String,
) {
    private val prefs = context.getSharedPreferences("vega_device", Context.MODE_PRIVATE)
    private val logTag = "vega"

    var state: String = "idle"        // register | pairing | connecting | connected | error
        private set
    var detail: String = ""
        private set
    var heartbeatsTotal: Int = 0
        private set
    var heartbeatMs: Long = 30_000
    private set

    private var heartbeatJob: Job? = null

    val deviceId: String?
        get() = prefs.getString("device_id", null)
    val token: String?
        get() = prefs.getString("token", null)
    val hasToken: Boolean
        get() = !prefs.getString("token", null).isNullOrBlank()

    var conversationId: String? = null
        private set

    var onLanCommands: ((command: JSONObject) -> Unit)? = null

    private var commandPollJob: Job? = null

    private fun setState(newState: String, newDetail: String = "") {
        state = newState
        detail = newDetail
        Log.i(logTag, "state=$newState $newDetail")
    }

    private suspend fun httpJSON(method: String, pathname: String, body: JSONObject?): JSONObject? =
        withContext(Dispatchers.IO) {
            val base = coreUrl.trimEnd('/')
            val conn = URL("$base$pathname").openConnection() as HttpURLConnection
            try {
                conn.requestMethod = method
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("User-Agent", "jarvis/${BuildConfig.VERSION_NAME} (android; device-bridge)")
                conn.setRequestProperty("X-Request-ID", UUID.randomUUID().toString())
                conn.connectTimeout = 5000
                conn.readTimeout = 8000
                conn.doInput = true
                if (body != null) {
                    conn.doOutput = true
                    conn.outputStream.use { it.write(body.toString().toByteArray()) }
                }
                val status = conn.responseCode
                val stream = if (status in 200..299) conn.inputStream else conn.errorStream
                val text = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
                val data = if (text.isBlank()) JSONObject() else JSONObject(text)
                if (status !in 200..299) {
                    throw HttpException(status, data.optString("message"))
                }
                data
            } finally {
                conn.disconnect()
            }
        }

    private class HttpException(val status: Int, msg: String) : Exception(msg)

    suspend fun boot() {
        if (heartbeatJob != null) return
        setState("registering", "registrando identidade no Coreâ€¦")
        try {
            val storedToken = prefs.getString("token", null)
            val storedId = prefs.getString("device_id", null)
            if (!storedToken.isNullOrBlank()) {
                try {
                    heartbeat("reconnect", storedId)
                    setState("connected", "reconectado (${storedId?.take(8)}â€¦)")
                    startLoop()
                    return
                } catch (e: HttpException) {
                    if (e.status != 401) throw e
                    prefs.edit().clear().apply() // token revogado â†’ re-pareia
                }
            }
            pair(storedId)
            heartbeat("connect", prefs.getString("device_id", null))
            setState("connected", "pareado e conectado ao Core")
            startLoop()
        } catch (e: Exception) {
            val detail = if (e is HttpException && e.status == 503)
                "Remote/Device Bridge desabilitado no Core (REMOTE_ENABLED/DEVICE_ENABLED)"
            else "sem contato com o Core (${e.message})"
            setState("error", detail)
        }
    }

    private fun graphCapabilities(): List<String> {
        val caps = app.vega.client.model.MobileCapabilities.ALL.map { it.name }.toMutableList()
        caps.addAll(listOf("chat", "tts", "notifications"))
        return caps
    }

    private suspend fun pair(storedId: String?) {
        setState("pairing", "gerando cÃ³digo de pareamentoâ€¦")
        val register = httpJSON(
            "POST", "/api/remote/devices/register",
            JSONObject().apply {
                put("name", "VEGA Mobile")
                put("device_type", "mobile")
                put("platform", "android")
                put("client_version", BuildConfig.VERSION_NAME)
                put("capabilities", org.json.JSONArray(graphCapabilities()))
            }
        )
        val deviceId = register!!.getJSONObject("device").getString("id")
        val p = httpJSON("POST", "/api/remote/pairings", null)!!
        val paired = httpJSON(
            "POST", "/api/remote/pairings/validate",
            JSONObject().apply {
                put("code", p.getString("code"))
                put("device_name", "VEGA Mobile")
                put("device_type", "mobile")
                put("platform", "android")
                put("client_version", BuildConfig.VERSION_NAME)
                put("capabilities", org.json.JSONArray(graphCapabilities()))
                put("pending_device_id", storedId ?: deviceId)
            }
        )!!
        prefs.edit()
            .putString("device_id", paired.optString("device_id", deviceId))
            .putString("token", paired.getString("token"))
            .apply()
    }

    private suspend fun heartbeat(event: String, claimedDeviceId: String?): JSONObject? {
        val out = httpJSON(
            "POST", "/api/remote/heartbeat",
            JSONObject().apply {
                put("token", prefs.getString("token", ""))
                put("event", event)
                put("platform", "android")
                put("client_version", BuildConfig.VERSION_NAME)
                put("capabilities", org.json.JSONArray(graphCapabilities()))
                claimedDeviceId?.let { put("claimed_device_id", it) }
            }
        )
        heartbeatsTotal++
        heartbeatMs = (out?.optInt("heartbeat_seconds", 30) ?: 30) * 1000L
        conversationId = out?.optString("conversation_id", "").orEmpty().ifBlank { conversationId }
        return out
    }

    private suspend fun reauthenticate(): Boolean {
        val tok = prefs.getString("token", null).orEmpty()
        if (tok.isBlank()) return false
        val out = httpJSON(
            "POST", "/api/remote/auth",
            JSONObject().apply {
                put("token", tok)
                deviceId?.let { put("claimed_device_id", it) }
            }
        )
        conversationId = out?.optString("conversation_id", "").orEmpty().ifBlank { conversationId }
        return out?.optBoolean("authenticated", false) == true
    }

    /** Fase 26 — busca comandos enfileirados no Core para este dispositivo (LAN). */
    suspend fun pollCommands(): List<JSONObject> {
        if (state != "connected") return emptyList()
        val id = deviceId ?: return emptyList()
        val tok = prefs.getString("token", null) ?: return emptyList()
        val out = httpJSON(
            "POST", "/api/remote/devices/$id/commands/poll",
            JSONObject().apply {
                put("token", tok)
                put("claimed_device_id", id)
            },
        ) ?: return emptyList()
        val arr = out.optJSONArray("commands") ?: org.json.JSONArray()
        val cmds = mutableListOf<JSONObject>()
        for (i in 0 until arr.length()) arr.optJSONObject(i)?.let { cmds.add(it) }
        return cmds
    }

    /** Fase 26 — publica o resultado canônico de um comando no Core (LAN). */
    suspend fun reportCommandResult(
        commandId: String,
        status: String,
        result: JSONObject? = null,
        error: String? = null,
        startedAt: String? = null,
        finishedAt: String? = null,
    ) {
        val id = deviceId ?: return
        val tok = prefs.getString("token", null) ?: return
        httpJSON(
            "POST", "/api/remote/devices/$id/commands/result",
            JSONObject().apply {
                put("token", tok)
                put("claimed_device_id", id)
                put("command_id", commandId)
                put("status", status)
                if (result != null) put("result", result)
                if (error != null) put("error", error)
                if (startedAt != null) put("started_at", startedAt)
                if (finishedAt != null) put("finished_at", finishedAt)
            },
        )
    }

    private fun startLoop() {
        heartbeatJob = CoroutineScope(Dispatchers.IO).launch {
            var failures = 0
            while (true) {
                try {
                    heartbeat("heartbeat", prefs.getString("device_id", null))
                    failures = 0
                    if (state != "connected") setState("connected", "conectado ao Core")
                } catch (e: Exception) {
                    val status = (e as? HttpException)?.status ?: 0
                    if (status == 401) {
                        val ok = try { reauthenticate() } catch (_: Exception) { false }
                        if (ok) {
                            if (state != "connected") setState("connected", "sessão renovada")
                        } else {
                            prefs.edit().clear().apply()
                            setState("error", "credenciais revogadas — reconecte")
                            break
                        }
                    } else if (status == 503) {
                        setState("error", "Remote/Device Bridge desabilitado no Core")
                        break
                    } else {
                        failures += 1
                        setState("reconnecting", "sem contato com o Core (tentativa $failures)")
                    }
                }
                val backoff = if (failures == 0) heartbeatMs
                else min(30_000L, 2_000L * (1L shl min(failures - 1, 4)))
                delay(backoff)
                if (failures > 0 && prefs.getString("token", null).isNullOrBlank()) break
            }
        }
        commandPollJob = CoroutineScope(Dispatchers.IO).launch {
            while (true) {
                delay(2500)
                if (state != "connected") continue
                try {
                    for (c in pollCommands()) {
                        if (c.optString("command_id").isNotBlank()) onLanCommands?.invoke(c)
                    }
                } catch (_: Exception) {
                    // best-effort: o executor deduplica command_id em re-poll
                }
            }
        }
    }

    suspend fun leave() {
        heartbeatJob?.cancel()
        heartbeatJob = null
        commandPollJob?.cancel()
        commandPollJob = null
        val token = prefs.getString("token", null)
        if (!token.isNullOrBlank()) {
            try {
                httpJSON(
                    "POST", "/api/remote/heartbeat",
                    JSONObject().apply {
                        put("token", token)
                        put("event", "disconnect")
                        put("platform", "android")
                    }
                )
            } catch (_: Exception) {
                // best-effort no encerramento
            }
        }
    }

    companion object {
        const val DEFAULT_CORE_URL = "http://10.0.2.2:8100" // emulador â†’ host
    }
}
