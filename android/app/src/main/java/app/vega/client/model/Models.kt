package app.vega.client.model

import org.json.JSONArray
import org.json.JSONObject

enum class ConnectionState(val label: String) {
    INITIALIZING("INICIALIZANDO"),
    CONNECTING("CONECTANDO"),
    ONLINE("ONLINE"),
    RECONNECTING("RECONEXÃO"),
    OFFLINE("OFFLINE"),
    ERROR("ERRO"),
}

object VegaPresence {
    val STATES = mapOf(
        "offline" to "off-line",
        "error" to "erro",
        "idle" to "ociosa",
        "listening" to "ouvindo",
        "thinking" to "pensando",
        "working" to "operando",
        "perceiving" to "percebendo",
        "planning" to "planejando",
        "observing" to "observando",
        "executing" to "executando",
        "verifying" to "verificando",
        "recovering" to "recuperando",
        "waiting_confirmation" to "aguardando confirmação",
        "speaking" to "falando",
        "success" to "concluída",
        "warning" to "atenção",
    )

    private val ALIASES = mapOf(
        "online" to "idle",
        "connecting" to "idle",
        "ready" to "idle",
        "listening_to_voice" to "listening",
        "error_occurred" to "error",
    )

    fun canonical(input: String?): String {
        val s = input?.lowercase()?.trim().orEmpty()
        val resolved = ALIASES[s] ?: s
        return if (STATES.containsKey(resolved)) resolved else "idle"
    }

    fun label(input: String?): String = STATES[canonical(input)].orEmpty()
}

enum class Role { USER, ASSISTANT }

enum class MessageStatus { SENDING, STREAMING, DONE, ERROR }

data class ToolItem(
    val name: String,
    var ok: Boolean? = null,
    var running: Boolean = true,
)

data class ChatMessage(
    val id: Long,
    val role: Role,
    val content: String,
    var status: MessageStatus,
    var tools: List<ToolItem> = emptyList(),
    var error: String? = null,
    var createdAt: Long = System.currentTimeMillis(),
)

data class SessionInfo(
    val id: String,
    val title: String,
    val createdAt: Long,
    val updatedAt: Long,
    val messageCount: Int,
)

data class ApprovalInfo(
    val id: String,
    val sessionId: String,
    val toolName: String,
    val risk: String,
    val permissionLevel: Int,
)

data class MobileResultUi(
    val capability: String,
    val commandId: String,
    val status: String,
    val error: String?,
    val timestamp: Long = System.currentTimeMillis(),
)

sealed interface TurnEvent {
    data class Start(val requestId: String? = null, val sessionId: String? = null) : TurnEvent
    data class Chunk(val text: String, val sessionId: String? = null) : TurnEvent
    data class ToolStart(val round: Int, val names: List<String>) : TurnEvent
    data class ToolDone(val name: String, val ok: Boolean, val output: String?) : TurnEvent
    data class ApprovalRequest(val approval: ApprovalInfo) : TurnEvent
    data class AgentEvent(val payload: JSONObject) : TurnEvent
    data class Done(val content: String, val sessionId: String? = null) : TurnEvent
    data class ErrorEvent(val detail: String) : TurnEvent

    companion object {
        fun parse(obj: JSONObject, defaultSessionId: String? = null): TurnEvent? {
            val type = obj.optString("type")
            val sessionId = obj.optString("session_id").ifBlank { defaultSessionId }
            return when (type) {
                "start" -> Start(obj.optString("request_id").ifBlank { null }, sessionId)
                "chunk" -> Chunk(obj.optString("text"), sessionId)
                "tool_start" -> {
                    val names = mutableListOf<String>()
                    val arr = obj.optJSONArray("names")
                    if (arr != null) for (i in 0 until arr.length()) names.add(arr.optString(i))
                    ToolStart(obj.optInt("round"), names)
                }
                "tool_done" -> ToolDone(obj.optString("name"), obj.optBoolean("ok"), obj.optString("output").ifBlank { obj.optString("detail").ifBlank { null } })
                "approval_request" -> {
                    val a = obj.optJSONObject("approval")
                    if (a == null) null else ApprovalRequest(
                        ApprovalInfo(
                            id = a.optString("id"),
                            sessionId = a.optString("session_id"),
                            toolName = a.optString("tool_name"),
                            risk = a.optString("risk"),
                            permissionLevel = a.optInt("permission_level", 0),
                        )
                    )
                }
                "agent_event" -> AgentEvent(obj.optJSONObject("payload") ?: JSONObject())
                "done" -> {
                    val msg = obj.optJSONObject("message")
                    Done(msg?.optString("content").orEmpty(), sessionId)
                }
                "error" -> ErrorEvent(obj.optString("detail"))
                else -> if (type.isBlank() || type == "approval_pending") null else null
            }
        }

        fun approvalFromJsonArray(arr: JSONArray?): List<ApprovalInfo> {
            if (arr == null) return emptyList()
            val out = mutableListOf<ApprovalInfo>()
            for (i in 0 until arr.length()) {
                val a = arr.optJSONObject(i) ?: continue
                out.add(
                    ApprovalInfo(
                        id = a.optString("id"),
                        sessionId = a.optString("session_id"),
                        toolName = a.optString("tool_name"),
                        risk = a.optString("risk"),
                        permissionLevel = a.optInt("permission_level", 0),
                    )
                )
            }
            return out
        }
    }
}