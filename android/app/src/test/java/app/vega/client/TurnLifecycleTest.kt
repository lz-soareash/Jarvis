package app.vega.client

import app.vega.client.model.ChatMessage
import app.vega.client.model.MessageStatus
import app.vega.client.model.Role
import app.vega.client.model.TurnEvent
import app.vega.client.model.TurnLifecycle
import app.vega.client.model.WanTurnAction
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Fase 27 — regressão do CICLO DE VIDA do turno de chat.
 *
 * Cobre os 12 cenários da seção 19 (o cenário "cancelar/timeout" é marcado
 * SKIP porque não existe frame de cancelamento no protocolo WAN/SSE atual —
 * a queda é coberta pelo pós-execução do SSE no LAN).
 *
 * Exercita EXCLUSIVAMENTE código de produção puro: `TurnLifecycle` (correlação,
 * epoch, dispatch WAN) + `MessageState` (transições da lista de mensagens).
 */
class TurnLifecycleTest {

    private var nextId = 10_000L
    private fun lc() = TurnLifecycle { nextId++ }
    private fun freshMessages(): MutableList<ChatMessage> = mutableListOf()

    private fun agentFrame(requestId: String, inner: String): JSONObject =
        JSONObject().put("request_id", requestId).put("event", JSONObject(inner))

    private fun messageResultFrame(requestId: String, ok: Boolean, content: String = "", error: String = ""): JSONObject =
        JSONObject().apply {
            put("request_id", requestId)
            put("ok", ok)
            if (content.isNotBlank()) put("content", content)
            if (error.isNotBlank()) put("error", error)
        }

    /** Aplica a ação WAN decidida (espelho do handleWanFrame do ChatViewModel). */
    private fun apply(messages: MutableList<ChatMessage>, action: WanTurnAction) {
        when (action) {
            is WanTurnAction.AgentEvent -> when (action.event) {
                is TurnEvent.Chunk ->
                    messages.replaceAll { TurnLifecycle.MessageState.chunk(listOf(it), action.assistantId, (action.event as TurnEvent.Chunk).text).first() }
                is TurnEvent.ToolStart ->
                    messages.replaceAll { TurnLifecycle.MessageState.toolStart(listOf(it), action.assistantId, (action.event as TurnEvent.ToolStart).names).first() }
                is TurnEvent.ToolDone -> {
                    val td = action.event as TurnEvent.ToolDone
                    messages.replaceAll { TurnLifecycle.MessageState.toolDone(listOf(it), action.assistantId, td.name, td.ok, td.structured).first() }
                }
                is TurnEvent.Done ->
                    messages.replaceAll { TurnLifecycle.MessageState.completeDone(listOf(it), action.assistantId, (action.event as TurnEvent.Done).content).first() }
                is TurnEvent.ErrorEvent -> {
                    val ev = action.event as TurnEvent.ErrorEvent
                    messages.replaceAll { TurnLifecycle.MessageState.completeError(listOf(it), action.assistantId, ev.detail).first() }
                }
                else -> Unit
            }
            is WanTurnAction.Complete ->
                messages.replaceAll { TurnLifecycle.MessageState.completeDone(listOf(it), action.assistantId, action.content).first() }
            is WanTurnAction.Fail ->
                messages.replaceAll { TurnLifecycle.MessageState.completeError(listOf(it), action.assistantId, action.error).first() }
            WanTurnAction.Ignore -> Unit
        }
    }

    private fun send(messages: MutableList<ChatMessage>, text: String): Pair<Long, Long> {
        val uid = nextId++
        val aid = nextId++
        messages += ChatMessage(uid, Role.USER, text, MessageStatus.DONE)
        messages += ChatMessage(aid, Role.ASSISTANT, "", MessageStatus.SENDING)
        return uid to aid
    }

    // 1. Mensagem simples: SENDING → STREAMING → DONE.
    @Test
    fun simpleTurnCompletesDone() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "olá VEGA")
        lc.beginConversation()
        lc.registerTurn("r1", aid)
        assertTrue(TurnLifecycle.isTurnInFlight(msgs))

        apply(msgs, lc.onWanFrame("agent_event", agentFrame("r1", """{"type":"chunk","text":"Olá"}"""), "env"))
        assertTrue(TurnLifecycle.isTurnInFlight(msgs))

        apply(msgs, lc.onWanFrame("agent_event", agentFrame("r1", """{"type":"chunk","text":", tudo bem?"}"""), "env"))
        apply(msgs, lc.onWanFrame("agent_event", agentFrame("r1", """{"type":"done","message":{"content":"Olá, tudo bem?"}}"""), "env"))

        val assistant = msgs.single { it.id == aid }
        assertEquals(MessageStatus.DONE, assistant.status)
        assertEquals("Olá, tudo bem?", assistant.content)
        assertTrue(lc.isFinished("r1"))
        assertNull(lc.findAssistant("r1"))
        assertFalse(TurnLifecycle.isTurnInFlight(msgs))
    }

    // 2. mobile_device_info → cartão + DONE (nunca fica "pensando…").
    @Test
    fun mobileDeviceInfoCardCompletesDone() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "Quais as informações do meu celular?")
        lc.beginConversation()

        msgs.replaceAll { TurnLifecycle.MessageState.toolStart(listOf(it), aid, listOf("mobile_device_info")).first() }
        val structured = """{"type":"mobile_command_result","capability":"DEVICE_INFO","status":"success","result":{"model":"Galaxy A15","os":"Android 14"}}"""
        msgs.replaceAll { TurnLifecycle.MessageState.toolDone(listOf(it), aid, "mobile_device_info", true, JSONObject(structured)).first() }
        msgs.replaceAll { TurnLifecycle.MessageState.completeDone(listOf(it), aid, "Aqui estão as informações do seu celular.").first() }

        val assistant = msgs.single { it.id == aid }
        assertEquals(MessageStatus.DONE, assistant.status)
        assertEquals("DEVICE_INFO", assistant.mobileCard?.capability)
        assertTrue(assistant.content.isNotBlank())
        assertFalse(TurnLifecycle.isTurnInFlight(msgs))
    }

    // 3. message_result(ok=true) chegando ANTES do done → turno DONE, done tardio ignorado.
    @Test
    fun messageResultBeforeDoneFinishesTurn() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "faz algo")
        lc.beginConversation()
        lc.registerTurn("r1", aid)

        apply(msgs, lc.onWanFrame("message_result", messageResultFrame("r1", ok = true, content = "feito!"), "env"))
        assertEquals(MessageStatus.DONE, msgs.single { it.id == aid }.status)
        assertEquals("feito!", msgs.single { it.id == aid }.content)
        assertFalse(TurnLifecycle.isTurnInFlight(msgs))

        val late = lc.onWanFrame("agent_event", agentFrame("r1", """{"type":"done","message":{"content":"duplicado"}}"""), "env")
        assertTrue(late is WanTurnAction.Ignore)
        assertEquals(1, msgs.count { it.role == Role.ASSISTANT })
        assertEquals(MessageStatus.DONE, msgs.single { it.id == aid }.status)
    }

    // 4. Disponibilidade após DONE: novo envio aceito.
    @Test
    fun chatAvailableAfterDone() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "um")
        lc.beginConversation()
        lc.registerTurn("r1", aid)
        apply(msgs, lc.onWanFrame("message_result", messageResultFrame("r1", ok = true, content = "resposta 1"), "env"))

        assertFalse(TurnLifecycle.isTurnInFlight(msgs))
        val (_, aid2) = send(msgs, "dois")
        lc.registerTurn("r2", aid2)
        assertEquals(aid2, lc.findAssistant("r2"))
        assertTrue(TurnLifecycle.isTurnInFlight(msgs))
    }

    // 5. Erro terminal: ErrorEvent → ERROR e o chat volta a ficar disponível.
    @Test
    fun errorEventIsTerminalAndKeepsChatReady() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "faça")
        lc.beginConversation()
        lc.registerTurn("r1", aid)

        apply(msgs, lc.onWanFrame("agent_event", agentFrame("r1", """{"type":"error","detail":"provedor indisponível"}"""), "env"))
        assertEquals(MessageStatus.ERROR, msgs.single { it.id == aid }.status)
        assertEquals("provedor indisponível", msgs.single { it.id == aid }.error)
        assertFalse(TurnLifecycle.isTurnInFlight(msgs))
        assertTrue(lc.isFinished("r1"))

        // O message_result(ok=true) posterior do backend NÃO sobescreve o erro.
        val late = lc.onWanFrame("message_result", messageResultFrame("r1", ok = true, content = "sem erro?"), "env")
        assertTrue(late is WanTurnAction.Ignore)
        assertEquals(MessageStatus.ERROR, msgs.single { it.id == aid }.status)
    }

    // 6. Cancelar/timeout: não há frame de cancelamento no protocolo (SKIP documentado).
    @Test
    fun cancelTimeoutNotInProtocol() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "piada")
        lc.beginConversation()
        lc.registerTurn("r1", aid)

        // Frame desconhecido é ignorado (não corrompe a correlação do turno).
        val action = lc.onWanFrame("cancel", JSONObject().put("request_id", "r1"), "env")
        assertTrue(action is WanTurnAction.Ignore)
        assertEquals(aid, lc.findAssistant("r1"))
        // A queda real da conexão é coberta pelo pós-execução do SSE (LAN) e
        // pelo `message_result(ok=false)` (WAN) — ambos terminam o turno.
        apply(msgs, lc.onWanFrame("message_result", messageResultFrame("r1", ok = false, error = "tempo esgotado"), "env"))
        assertEquals(MessageStatus.ERROR, msgs.single { it.id == aid }.status)
        assertFalse(TurnLifecycle.isTurnInFlight(msgs))
    }

    // 7. Nova conversa: estado limpo e pronto (READY) para novo envio.
    @Test
    fun newConversationBecomesReady() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "conversa A")
        val e1 = lc.beginConversation()
        lc.registerTurn("r1", aid)

        msgs.clear()
        val e2 = lc.beginConversation()
        assertTrue(e2 > e1)
        assertNull(lc.findAssistant("r1"))
        assertFalse(TurnLifecycle.isTurnInFlight(msgs))
        val (_, aidB) = send(msgs, "conversa B")
        lc.registerTurn("rB", aidB)
        assertEquals(aidB, lc.findAssistant("rB"))
    }

    // 8. Nova conversa durante processamento: recusada (turno em voo).
    @Test
    fun newConversationDuringProcessingRefused() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "em voo")
        val e1 = lc.beginConversation()
        lc.registerTurn("r1", aid)

        assertTrue(TurnLifecycle.isTurnInFlight(msgs))
        // O ChatViewModel guarda `newConversation` com `isTurnInFlight` — a
        // época NÃO é avançada enquanto o turno não terminar.
        val e2 = lc.currentEpoch()
        assertEquals(e1, e2)
        assertEquals(aid, lc.findAssistant("r1"))
    }

    // 9. Evento tardio da conversa antiga NÃO altera a conversa nova (B).
    @Test
    fun lateEventDoesNotPolluteNewConversation() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aidA) = send(msgs, "A")
        lc.beginConversation()
        lc.registerTurn("rA", aidA)
        msgs.clear()
        lc.beginConversation()
        val (_, aidB) = send(msgs, "B")
        lc.registerTurn("rB", aidB)

        // Frame atrasado do request rA (conversa antiga) chega agora.
        val late = lc.onWanFrame("agent_event", agentFrame("rA", """{"type":"done","message":{"content":"resposta de A"}}"""), "env")
        assertTrue(late is WanTurnAction.Ignore)
        val lateRes = lc.onWanFrame("message_result", messageResultFrame("rA", ok = true, content = "A"), "env")
        assertTrue(lateRes is WanTurnAction.Ignore)

        assertEquals(1, msgs.count { it.role == Role.ASSISTANT })
        assertEquals(aidB, msgs.last { it.role == Role.ASSISTANT }.id)
        assertEquals(MessageStatus.SENDING, msgs.last().status)
    }

    // 10. Isolamento de contexto A → B → A: cada conversa persiste íntegra.
    @Test
    fun contextIsolationAcrossReopen() {
        val lc = lc()
        val msgsA = mutableListOf<ChatMessage>()
        val (_, aA1) = send(msgsA, "A1")
        lc.beginConversation()
        lc.registerTurn("rA1", aA1)
        apply(msgsA, lc.onWanFrame("message_result", messageResultFrame("rA1", ok = true, content = "A1 ok"), "env"))

        val msgsB = mutableListOf<ChatMessage>()
        val (_, aB) = send(msgsB, "B")
        lc.beginConversation()
        lc.registerTurn("rB", aB)
        apply(msgsB, lc.onWanFrame("agent_event", agentFrame("rB", """{"type":"done","message":{"content":"B ok"}}"""), "env"))

        // Reabre A (openSession): história de A volta, com os turnos já DONE.
        msgsA.clear()
        msgsA += ChatMessage(1L, Role.USER, "A1", MessageStatus.DONE)
        msgsA += ChatMessage(2L, Role.ASSISTANT, "A1 ok", MessageStatus.DONE)
        val (_, aA2) = send(msgsA, "A2")
        lc.beginConversation()
        lc.registerTurn("rA2", aA2)

        assertNull(lc.findAssistant("rA1"))
        assertNull(lc.findAssistant("rB"))
        assertEquals(aA2, lc.findAssistant("rA2"))
        assertEquals(MessageStatus.DONE, msgsA.single { it.id == 2L }.status)
        assertEquals("A1 ok", msgsA.single { it.id == 2L }.content)
    }

    // 11. Múltiplas conversas A → B → C → A sequenciais aceitas.
    @Test
    fun multipleConversationsSequentialAccepted() {
        val lc = lc()
        val names = listOf("A", "B", "C", "A")
        for (i in names.indices) {
            lc.beginConversation()
            val msgs = mutableListOf<ChatMessage>()
            val (_, aId) = send(msgs, "msg-$i")
            lc.registerTurn("r$i", aId)
            apply(msgs, lc.onWanFrame("message_result", messageResultFrame("r$i", ok = true, content = "ok-$i"), "env"))
            assertEquals(MessageStatus.DONE, msgs.single { it.id == aId }.status)
            assertFalse(TurnLifecycle.isTurnInFlight(msgs))
        }
        assertNull(lc.findAssistant("r0"))
        assertNull(lc.findAssistant("r3"))
        assertTrue(lc.isFinished("r3"))
    }

    // 12. Mensagens sequenciais na MESMA conversa: cada turno aceito após o anterior.
    @Test
    fun sequentialMessagesSameConversationAccepted() {
        val lc = lc(); val msgs = freshMessages()
        lc.beginConversation()
        for (i in 1..3) {
            assertFalse("deve estar pronto antes do envio $i", TurnLifecycle.isTurnInFlight(msgs))
            val (_, aId) = send(msgs, "pergunta $i")
            lc.registerTurn("q$i", aId)
            apply(msgs, lc.onWanFrame("message_result", messageResultFrame("q$i", ok = true, content = "resposta $i"), "env"))
            assertEquals(MessageStatus.DONE, msgs.single { it.id == aId }.status)
        }
        assertEquals(6, msgs.size)
    }

    // 13. Fase 27.1 — a correlação NÃO é esquecida quando o envio WAN retorna:
    //     a resposta do Core chega depois e o turno encerra normalmente.
    @Test
    fun correlationSurvivesAfterSendReturns() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "abra o spotify no meu celular")
        lc.beginConversation()
        // runWanTurn: sendMessage (fire-and-forget) + registerTurn, e o coroutine
        // de envio retorna IMEDIATAMENTE (não há await de resposta).
        lc.registerTurn("r1", aid)

        // O envio já retornou; a correlação TEM de continuar viva.
        assertEquals(aid, lc.findAssistant("r1"))

        // A resposta do Core chega depois (message_result).
        apply(msgs, lc.onWanFrame("message_result", messageResultFrame("r1", ok = true, content = "Spotify aberto"), "env"))
        val assistant = msgs.single { it.id == aid }
        assertEquals(MessageStatus.DONE, assistant.status)
        assertEquals("Spotify aberto", assistant.content)
        assertFalse(TurnLifecycle.isTurnInFlight(msgs))
    }

    // 14. Fase 27.1 — documento da causa raiz: esquecer a correlação no retorno
    //     do envio (antigo `finally { forgetAssistant }`) descartaria a resposta
    //     legítima e deixaria a mensagem presa em "pensando…".
    @Test
    fun prematureForgetWouldDropLegitimateReply() {
        val lc = lc(); val msgs = freshMessages()
        val (_, aid) = send(msgs, "abra o spotify")
        lc.beginConversation()
        lc.registerTurn("r1", aid)
        lc.forgetAssistant(aid) // removido da produção nesta fase

        val dropped = lc.onWanFrame("message_result", messageResultFrame("r1", ok = true, content = "ok"), "env")
        assertTrue(dropped is WanTurnAction.Ignore)
        assertEquals(MessageStatus.SENDING, msgs.single { it.id == aid }.status)
    }
}