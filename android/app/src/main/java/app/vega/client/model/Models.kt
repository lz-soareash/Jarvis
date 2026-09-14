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
    var mobileCard: MobileCommandCard? = null,
    var operationCard: RemoteOperationCard? = null,
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

object MobileCardLabels {
    private val MAP = mapOf(
        "DEVICE_INFO" to "Dispositivo",
        "BATTERY_STATUS" to "Bateria",
        "NETWORK_STATUS" to "Rede",
        "MEDIA_STATUS" to "Mídia",
        "OPEN_URL" to "Abrir URL",
        "VIBRATE" to "Vibração",
        "SET_VOLUME" to "Volume",
        "SET_BRIGHTNESS" to "Brilho",
        "OPEN_APP" to "Abrir app",
    )

    fun title(capability: String): String = MAP[capability] ?: capability
}

object MobileStatusLabels {
    private val LABELS = mapOf(
        "pending" to "pendente",
        "running" to "executando",
        "success" to "sucesso",
        "failed" to "falhou",
        "denied" to "negado",
        "unsupported" to "não suportado",
        "timeout" to "tempo esgotado",
        "cancelled" to "cancelado",
    )

    private val GLYPHS = mapOf(
        "success" to "\u2713",
        "failed" to "\u2717",
        "unsupported" to "\u2717",
        "denied" to "\u2715",
        "cancelled" to "\u2715",
        "timeout" to "\u25D2",
        "pending" to "\u2026",
        "running" to "\u25CF",
    )

    fun label(status: String): String = LABELS[status] ?: status

    fun glyph(status: String): String = GLYPHS[status] ?: "\u00B7"
}

/**
 * Continuidade multi-turn no cliente: o último cartão de dispositivo com
 * status "success" na conversa indica o dispositivo em que o Core seguirá
 * uma intenção de continuidade ("Agora pesquisa…"). É informação real do
 * próprio histórico local — nunca fabricada.
 */
data class ContinuityHint(
    val device: String,
    val capability: String,
)

fun continuityFrom(messages: List<ChatMessage>?): ContinuityHint? {
    if (messages == null) return null
    for (m in messages.asReversed()) {
        val card = m.mobileCard ?: continue
        val device = card.device?.takeIf { it.isNotBlank() } ?: continue
        if (card.status == "success") return ContinuityHint(device, card.capability)
    }
    return null
}

data class MobileCommandCard(
    val capability: String,
    val status: String,
    val device: String?,
    val transport: String?,
    val result: JSONObject?,
    val error: String?,
    val summary: String?,
    val ok: Boolean,
) {
    val title: String get() = MobileCardLabels.title(capability)
    val statusGlyph: String get() = MobileStatusLabels.glyph(status)
    val statusLabel: String get() = MobileStatusLabels.label(status)

    fun a11yDescription(): String = buildString {
        append(title).append(", ").append(status)
        if (summary?.isNotBlank() == true) {
            append(", ").append(summary)
        } else if (result != null) {
            val keys = result.keys().asSequence().toList().take(3)
            if (keys.isNotEmpty()) {
                append(": ").append(keys.joinToString(", ") { "$it ${result.optString(it)}" })
            }
        }
    }

    companion object {
        fun fromJson(obj: JSONObject?): MobileCommandCard? {
            if (obj == null || obj.optString("type") != "mobile_command_result") return null
            val status = obj.optString("status").ifBlank { return null }
            return MobileCommandCard(
                capability = obj.optString("capability").ifBlank { "DESCONHECIDA" },
                status = status,
                device = obj.optString("device").ifBlank { null },
                transport = obj.optString("transport").ifBlank { null },
                result = obj.optJSONObject("result"),
                error = obj.optString("error").ifBlank { null },
                summary = obj.optString("summary").ifBlank { null },
                ok = status == "success",
            )
        }
    }
}

/**
 * Fase 28 — Remote Operation: cartão de operação coordenada entre dispositivos.
 * Espelha o payload estruturado do Core (`{"type":"remote.operation.result", ...}`
 * com o objeto `operation` contendo id/status/requested_action/steps). A operação
 * NÃO expõe tokens/segredos — apenas rótulo, status agregado e passos resumidos.
 */
data class RemoteOperationCard(
    val operationId: String,
    val status: String,
    val requestedAction: String?,
    val succeededSteps: Int,
    val failedSteps: Int,
    val error: String?,
    val steps: List<RemoteOperationStep>,
    val requiresConfirmation: Boolean,
) {
    val statusGlyph: String get() = MobileStatusLabels.glyph(status)
    val statusLabel: String get() = MobileStatusLabels.label(status)
    val title: String get() = requestedAction?.ifBlank { null } ?: "Operação remota"

    fun a11yDescription(): String = buildString {
        append(title).append(", ").append(statusLabel)
        append(", passos: ").append(succeededSteps).append(" ok, ").append(failedSteps).append(" falho(s)")
    }

    companion object {
        fun fromJson(obj: JSONObject?): RemoteOperationCard? {
            if (obj == null || obj.optString("type") != "remote.operation.result") return null
            val operation = obj.optJSONObject("operation")
            val status = (operation ?: obj).optString("status").ifBlank { return null }
            val steps = mutableListOf<RemoteOperationStep>()
            val arr = operation?.optJSONArray("steps")
            if (arr != null) {
                for (i in 0 until arr.length()) {
                    val s = arr.optJSONObject(i) ?: continue
                    steps.add(
                        RemoteOperationStep(
                            action = s.optString("action").ifBlank { "?" },
                            target = s.optString("target").ifBlank { null },
                            status = s.optString("status").ifBlank { "pending" },
                            message = s.optString("message").ifBlank { null },
                            device = s.optString("device").ifBlank { null },
                        )
                    )
                }
            }
            return RemoteOperationCard(
                operationId = obj.optString("operation_id").ifBlank { operation?.optString("id").orEmpty() },
                status = status,
                requestedAction = operation?.optString("requested_action")?.ifBlank { null },
                succeededSteps = obj.optInt("succeeded_steps", 0),
                failedSteps = obj.optInt("failed_steps", 0),
                error = obj.optString("error").ifBlank { null },
                steps = steps,
                requiresConfirmation = obj.optBoolean("requires_confirmation", false),
            )
        }
    }
}

data class RemoteOperationStep(
    val action: String,
    val target: String?,
    val status: String,
    val message: String?,
    val device: String?,
)

sealed interface TurnEvent {
    data class Start(val requestId: String? = null, val sessionId: String? = null) : TurnEvent
    data class Chunk(val text: String, val sessionId: String? = null) : TurnEvent
    data class ToolStart(val round: Int, val names: List<String>) : TurnEvent
    data class ToolDone(val name: String, val ok: Boolean, val output: String?, val structured: JSONObject? = null) : TurnEvent
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
                "tool_done" -> {
                    val raw = obj.optString("output").ifBlank { obj.optString("detail").ifBlank { null } }
                    val structured = raw?.let { text ->
                        try {
                            JSONObject(text)
                        } catch (_: Exception) {
                            null
                        }
                    }
                    ToolDone(obj.optString("name"), obj.optBoolean("ok"), raw, structured)
                }
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