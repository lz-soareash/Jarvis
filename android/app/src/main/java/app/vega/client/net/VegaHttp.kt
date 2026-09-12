package app.vega.client.net

import app.vega.client.BuildConfig
import app.vega.client.model.ChatMessage
import app.vega.client.model.MessageStatus
import app.vega.client.model.Role
import app.vega.client.model.SessionInfo
import app.vega.client.model.TurnEvent
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException
import java.net.URLEncoder
import java.time.Instant
import java.util.UUID
import java.util.concurrent.TimeUnit

class ApiException(val status: Int, override val message: String) : Exception(message)

class VegaHttp {
    private val json = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    private val sse = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private val jsonType = "application/json; charset=utf-8".toMediaType()

    private fun baseUrl(url: String) = url.trimEnd('/')

    private fun headers(): okhttp3.Headers = okhttp3.Headers.Builder()
        .add("Content-Type", "application/json")
        .add("User-Agent", "jarvis/${BuildConfig.VERSION_NAME} (android; device-bridge)")
        .add("X-Request-ID", UUID.randomUUID().toString())
        .build()

    fun postJson(coreUrl: String, path: String, body: JSONObject?): JSONObject {
        val req = Request.Builder()
            .url(baseUrl(coreUrl) + path)
            .headers(headers())
            .post(body?.toString()?.toRequestBody(jsonType) ?: "{}".toRequestBody(jsonType))
            .build()
        return executeJson(json.newCall(req))
    }

    fun getJson(coreUrl: String, path: String): JSONObject {
        val req = Request.Builder()
            .url(baseUrl(coreUrl) + path)
            .headers(headers())
            .get()
            .build()
        return executeJson(json.newCall(req))
    }

    fun getJsonArray(coreUrl: String, path: String): List<JSONObject> {
        val req = Request.Builder()
            .url(baseUrl(coreUrl) + path)
            .headers(headers())
            .get()
            .build()
        val res = json.newCall(req).execute()
        res.use { r ->
            if (!r.isSuccessful) throw ApiException(r.code, readError(r))
            val arr = r.body?.string()?.let { org.json.JSONArray(it) } ?: org.json.JSONArray()
            return (0 until arr.length()).map { arr.getJSONObject(it) }
        }
    }

    private fun executeJson(call: Call): JSONObject {
        val res = call.execute()
        res.use { r ->
            if (!r.isSuccessful) throw ApiException(r.code, readError(r))
            return JSONObject(r.body?.string().orEmpty().ifBlank { "{}" })
        }
    }

    private fun readError(r: Response): String {
        val text = r.body?.string().orEmpty()
        return try {
            val j = JSONObject(text)
            j.optString("message").ifBlank { j.optString("detail") }.ifBlank { "HTTP ${r.code}" }
        } catch (_: Exception) {
            if (text.isBlank()) "HTTP ${r.code}" else text
        }
    }

    fun sendMessageSse(coreUrl: String, token: String, sessionId: String?, content: String): Flow<TurnEvent> =
        callbackFlow {
            val body = JSONObject().apply {
                put("token", token)
                put("content", content)
                put("stream", true)
                put("tools", true)
                sessionId?.let { put("session_id", it) }
            }
            val request = Request.Builder()
                .url(baseUrl(coreUrl) + "/api/remote/message")
                .headers(headers())
                .post(body.toString().toRequestBody(jsonType))
                .build()
            val call = sse.newCall(request)
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    trySend(TurnEvent.ErrorEvent("sem contato com o Core (${e.message})"))
                    close()
                }

                override fun onResponse(call: Call, response: Response) {
                    response.use { r ->
                        if (!r.isSuccessful) {
                            trySend(TurnEvent.ErrorEvent(readError(r)))
                            close()
                            return
                        }
                        val source = r.body?.source()
                        if (source == null) {
                            trySend(TurnEvent.ErrorEvent("resposta vazia do Core"))
                            close()
                            return
                        }
                        while (!isClosedForSend) {
                            val line = source.readUtf8Line() ?: break
                            if (!line.startsWith("data: ")) continue
                            val json = line.removePrefix("data: ").trim()
                            val ev = try {
                                JSONObject(json)
                            } catch (_: Exception) {
                                continue
                            }
                            val parsed = TurnEvent.parse(ev, sessionId) ?: continue
                            if (!trySend(parsed).isSuccess) break
                            if (parsed is TurnEvent.Done) break
                        }
                        close()
                    }
                }
            })
            awaitClose { call.cancel() }
        }

    fun listSessions(coreUrl: String): List<SessionInfo> =
        getJsonArray(coreUrl, "/api/sessions").map { j ->
            SessionInfo(
                id = j.optString("id"),
                title = j.optString("title").ifBlank { "Conversa ${j.optString("id").take(8)}" },
                createdAt = parseTime(j.optString("created_at")),
                updatedAt = parseTime(j.optString("updated_at")),
                messageCount = j.optInt("message_count", 0),
            )
        }

    fun createSession(coreUrl: String): SessionInfo {
        val j = postJson(coreUrl, "/api/sessions", null)
        return SessionInfo(
            id = j.optString("id"),
            title = j.optString("title").ifBlank { "Nova conversa" },
            createdAt = parseTime(j.optString("created_at")),
            updatedAt = parseTime(j.optString("updated_at")),
            messageCount = j.optInt("message_count", 0),
        )
    }

    fun fetchHistory(coreUrl: String, sessionId: String): List<ChatMessage> {
        val rows = getJsonArray(coreUrl, "/api/sessions/$sessionId/messages")
        var seq = 0L
        return rows.mapNotNull { j ->
            val role = if (j.optString("role") == "user") Role.USER else Role.ASSISTANT
            ChatMessage(
                id = seq++,
                role = role,
                content = j.optString("content"),
                status = MessageStatus.DONE,
                createdAt = parseTime(j.optString("created_at")),
            )
        }
    }

    fun opsOverview(coreUrl: String): JSONObject = getJson(coreUrl, "/api/ops/overview")

    fun respondApproval(coreUrl: String, approvalId: String, approved: Boolean) {
        postJson(
            coreUrl,
            "/api/approvals/$approvalId/respond",
            JSONObject().put("approved", approved),
        )
    }

    fun ttsUrl(coreUrl: String, text: String): String =
        baseUrl(coreUrl) + "/api/tts?text=" + URLEncoder.encode(text, "UTF-8")

    private fun parseTime(raw: String): Long =
        try {
            Instant.parse(raw).toEpochMilli()
        } catch (_: Exception) {
            System.currentTimeMillis()
        }
}