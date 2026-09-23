package app.vega.client.model

import org.json.JSONObject

/**
 * Fase 27 — ciclo de vida de turno de chat (Kotlin PURO, testável sem Android).
 *
 * Centraliza em um único ponto as transições de estado das mensagens e a
 * correlação request↔mensagem de cada turno, sem depender de ViewModel/Flow.
 * O `ChatViewModel` delega as decisões aqui; os testes de regressão do
 * lifecycle exercitam EXATAMENTE o código de produção.
 *
 * Propriedades garantidas:
 *  - todo turno encerra em DONE ou ERROR (mensagem nunca fica "pensando…");
 *  - `message_result` e `done` são terminações idempotentes (primeira vence);
 *  - nova conversa (epoch) descarta frames/correlações de conversas antigas;
 *  - retomada de aprovação WAN correlaciona o NOVO request_id do resume.
 *
 * Thread-safety: todos os métodos correlacionais são `@Synchronized` porque o
 * dispatch WAN roda na thread do socket (OkHttp) e o restante na main.
 */
class TurnLifecycle(
    private val idGenerator: () -> Long,
) {
    private var epoch = 0L
    private val correlations = HashMap<String, Long>()
    private val byAssistant = HashMap<Long, String>()
    private val finishedRequests = HashSet<String>()

    /** A chat está disponível para um novo envio quando não há turno em voo. */
    companion object {
        fun isTurnInFlight(messages: List<ChatMessage>): Boolean =
            messages.any { it.status == MessageStatus.SENDING || it.status == MessageStatus.STREAMING }
    }

    // ---- correlação request ↔ mensagem -------------------------------------

    /** Nova conversa: incrementa a época e descarta correlações/frames antigos. */
    @Synchronized
    fun beginConversation(): Long {
        epoch += 1
        correlations.clear()
        byAssistant.clear()
        finishedRequests.clear()
        return epoch
    }

    @Synchronized
    fun currentEpoch(): Long = epoch

    @Synchronized
    fun registerTurn(requestId: String, assistantId: Long) {
        correlations[requestId] = assistantId
        byAssistant[assistantId] = requestId
    }

    /**
     * Retomada de aprovação WAN: após `approval_respond` os eventos chegam sob
     * um NOVO request_id. Liga esse request_id à MESMA mensagem que aguardava a
     * decisão (a correlação antiga do turno original é descartada).
     */
    @Synchronized
    fun bindResume(requestId: String, assistantId: Long) {
        byAssistant.remove(assistantId)?.let { correlations.remove(it) }
        registerTurnLocked(requestId, assistantId)
    }

    @Synchronized
    fun findAssistant(requestId: String): Long? = correlations[requestId]

    @Synchronized
    fun forgetAssistant(assistantId: Long) {
        byAssistant.remove(assistantId)?.let { correlations.remove(it) }
    }

    /**
     * Fase 29 — encerra idempotentemente um turno EM VOO após falha de
     * transporte (ex.: socket WAN caiu no meio da resposta). Remove a
     * correlação e marca o request como terminado, de modo que um frame tardio
     * do MESMO request é descartado e a UI (input/enviar) é liberada.
     * Devolve `true` apenas se existia um turno correlacionado a este assistant.
     */
    @Synchronized
    fun failTurn(assistantId: Long): Boolean {
        val requestId = byAssistant.remove(assistantId) ?: return false
        correlations.remove(requestId)
        finishedRequests.add(requestId)
        return true
    }

    /**
     * Encerra o turno e devolve o assistantId correlacionado.
     * Idempotente: request já encerrado (ou desconhecido) devolve null, então
     * `done`/`message_result` duplicados ou tardios nunca reabrem a mensagem.
     */
    @Synchronized
    fun markFinished(requestId: String): Long? {
        val assistantId = correlations.remove(requestId) ?: return null
        byAssistant.remove(assistantId)
        finishedRequests.add(requestId)
        return assistantId
    }

    @Synchronized
    fun isFinished(requestId: String): Boolean = finishedRequests.contains(requestId)

    // ---- dispatch de frames WAN (produção testável) -------------------------

    /**
     * Interpreta um frame WAN (`message_result` | `agent_event`) e decide a
     * ação do turno. É a mesma lógica que o ChatViewModel executava inline,
     * agora centralizada para os testes de regressão.
     */
    @Synchronized
    fun onWanFrame(type: String, payload: JSONObject, envelopeRequestId: String): WanTurnAction {
        when (type) {
            "agent_event" -> {
                val inner = payload.optJSONObject("event") ?: return WanTurnAction.Ignore
                val ev = TurnEvent.parse(inner) ?: return WanTurnAction.Ignore
                val requestId = payload.optString("request_id").ifBlank { envelopeRequestId }
                val assistantId = findAssistant(requestId) ?: return WanTurnAction.Ignore
                if (ev is TurnEvent.Done || ev is TurnEvent.ErrorEvent) {
                    markFinished(requestId)
                }
                return WanTurnAction.AgentEvent(assistantId, requestId, ev)
            }
            "message_result" -> {
                val requestId = payload.optString("request_id").ifBlank { envelopeRequestId }
                val assistantId = markFinished(requestId) ?: return WanTurnAction.Ignore
                return if (payload.optBoolean("ok", false)) {
                    WanTurnAction.Complete(assistantId, payload.optString("content"))
                } else {
                    WanTurnAction.Fail(assistantId, payload.optString("error").ifBlank { "Core falhou ao processar" })
                }
            }
        }
        return WanTurnAction.Ignore
    }

    private fun registerTurnLocked(requestId: String, assistantId: Long) {
        correlations[requestId] = assistantId
        byAssistant[assistantId] = requestId
    }

    // ---- transições de estado da lista de mensagens ------------------------

    object MessageState {
        fun appendUser(messages: List<ChatMessage>, id: Long, text: String): List<ChatMessage> =
            messages + ChatMessage(id, Role.USER, text, MessageStatus.DONE)

        fun appendAssistant(messages: List<ChatMessage>, id: Long): List<ChatMessage> =
            messages + ChatMessage(id, Role.ASSISTANT, "", MessageStatus.SENDING)

        fun chunk(messages: List<ChatMessage>, id: Long, text: String): List<ChatMessage> =
            messages.map {
                if (it.id == id) it.copy(content = it.content + text, status = MessageStatus.STREAMING, error = null) else it
            }

        fun toolStart(messages: List<ChatMessage>, id: Long, names: List<String>): List<ChatMessage> =
            messages.map { msg ->
                if (msg.id != id) return@map msg
                val keep = msg.tools.filterNot { t -> names.contains(t.name) && t.running }
                msg.copy(status = MessageStatus.STREAMING, tools = keep + names.map { ToolItem(it) })
            }

        fun toolDone(
            messages: List<ChatMessage>,
            id: Long,
            name: String,
            ok: Boolean,
            structured: JSONObject?,
        ): List<ChatMessage> =
            messages.map { msg ->
                if (msg.id != id) return@map msg
                val card = structured?.let { MobileCommandCard.fromJson(it) }
                val opCard = structured?.let { RemoteOperationCard.fromJson(it) }
                msg.copy(
                    tools = msg.tools.map { t -> if (t.name == name) t.copy(ok = ok, running = false) else t },
                    mobileCard = card ?: msg.mobileCard,
                    operationCard = opCard ?: msg.operationCard,
                )
            }

        /** Transição terminal DONE. `content` final vazio mantém o acumulado. */
        fun completeDone(
            messages: List<ChatMessage>,
            id: Long,
            content: String,
        ): List<ChatMessage> =
            messages.map { msg ->
                if (msg.id != id) return@map msg
                val finalContent = if (content.isNotBlank()) content else msg.content
                msg.copy(
                    content = finalContent,
                    status = MessageStatus.DONE,
                    tools = msg.tools.map { t -> t.copy(running = false) },
                )
            }

        /** Transição terminal ERROR (nunca deixa a mensagem presa em STREAMING). */
        fun completeError(
            messages: List<ChatMessage>,
            id: Long,
            error: String,
        ): List<ChatMessage> =
            messages.map { msg ->
                if (msg.id != id) return@map msg
                msg.copy(
                    content = msg.content.takeIf { it.isNotBlank() } ?: "",
                    status = MessageStatus.ERROR,
                    error = error,
                    tools = msg.tools.map { t -> t.copy(running = false) },
                )
            }
    }
}

/** Decisão de um frame WAN interpretado por [TurnLifecycle.onWanFrame]. */
sealed class WanTurnAction {
    object Ignore : WanTurnAction()
    data class AgentEvent(val assistantId: Long, val requestId: String, val event: TurnEvent) : WanTurnAction()
    data class Complete(val assistantId: Long, val content: String) : WanTurnAction()
    data class Fail(val assistantId: Long, val error: String) : WanTurnAction()
}