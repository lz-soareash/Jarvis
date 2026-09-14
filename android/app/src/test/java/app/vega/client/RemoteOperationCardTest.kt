package app.vega.client

import app.vega.client.model.RemoteOperationCard
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RemoteOperationCardTest {

    @Test
    fun fromJsonTypicalSuccess() {
        val steps = JSONArray()
            .put(JSONObject()
                .put("action", "OPEN_URL")
                .put("target", "computador")
                .put("status", "success")
                .put("message", "URL aberta: https://youtube.com")
                .put("device", "Computador"))
            .put(JSONObject()
                .put("action", "OPEN_APP")
                .put("target", "celular")
                .put("status", "success")
                .put("device", "Galaxy A15"))
        val json = JSONObject()
            .put("type", "remote.operation.result")
            .put("operation_id", "op_abc123")
            .put("status", "success")
            .put("succeeded_steps", 2)
            .put("failed_steps", 0)
            .put("requires_confirmation", false)
            .put("operation", JSONObject()
                .put("id", "op_abc123")
                .put("status", "success")
                .put("requested_action", "abrir youtube e chrome em dispositivos")
                .put("steps", steps))
        val c = RemoteOperationCard.fromJson(json)
        assertNotNull(c)
        assertEquals("op_abc123", c!!.operationId)
        assertEquals("success", c.status)
        assertEquals("abrir youtube e chrome em dispositivos", c.requestedAction)
        assertEquals(2, c.succeededSteps)
        assertEquals(0, c.failedSteps)
        assertFalse(c.requiresConfirmation)
        assertEquals(2, c.steps.size)
        assertEquals("OPEN_URL", c.steps[0].action)
        assertEquals("Galaxy A15", c.steps[1].device)
    }

    @Test
    fun fromJsonPausedL2SetsRequiresConfirmation() {
        val json = JSONObject()
            .put("type", "remote.operation.result")
            .put("operation_id", "op_pause1")
            .put("status", "awaiting_confirmation")
            .put("requires_confirmation", true)
            .put("succeeded_steps", 0)
            .put("failed_steps", 0)
            .put("operation", JSONObject()
                .put("id", "op_pause1")
                .put("status", "awaiting_confirmation")
                .put("requested_action", "fechar chrome")
                .put("steps", JSONArray().put(
                    JSONObject()
                        .put("action", "CLOSE_APP")
                        .put("target", "pc")
                        .put("status", "pending")
                        .put("message", "aguardando confirmação do usuário"))))
        val c = RemoteOperationCard.fromJson(json)!!
        assertTrue(c.requiresConfirmation)
        assertEquals("awaiting_confirmation", c.status)
        assertEquals("aguardando confirmação do usuário", c.steps[0].message)
    }

    @Test
    fun fromJsonRejectsNonOperationResult() {
        assertNull(RemoteOperationCard.fromJson(null))
        assertNull(RemoteOperationCard.fromJson(JSONObject().put("type", "other").put("status", "success")))
        assertNull(RemoteOperationCard.fromJson(JSONObject().put("type", "remote.operation.result")))
    }

    @Test
    fun fromJsonDeniedWithoutSteps() {
        val json = JSONObject()
            .put("type", "remote.operation.result")
            .put("operation_id", "op_deny1")
            .put("status", "denied")
            .put("succeeded_steps", 0)
            .put("failed_steps", 1)
            .put("error", "passo bloqueado por nível crítico")
            .put("operation", JSONObject().put("id", "op_deny1").put("status", "denied").put("steps", JSONArray()))
        val c = RemoteOperationCard.fromJson(json)!!
        assertEquals("denied", c.status)
        assertEquals(1, c.failedSteps)
        assertEquals("passo bloqueado por nível crítico", c.error)
        assertTrue(c.steps.isEmpty())
    }

    @Test
    fun a11yDescriptionSummarizesOperation() {
        val json = JSONObject()
            .put("type", "remote.operation.result")
            .put("operation_id", "op_a11y")
            .put("status", "success")
            .put("succeeded_steps", 2)
            .put("failed_steps", 0)
            .put("operation", JSONObject()
                .put("status", "success")
                .put("requested_action", "verificar dispositivos")
                .put("steps", JSONArray()))
        val c = RemoteOperationCard.fromJson(json)!!
        val desc = c.a11yDescription()
        assertTrue(desc.contains("verificar dispositivos"))
        assertTrue(desc.contains("2 ok"))
    }
}