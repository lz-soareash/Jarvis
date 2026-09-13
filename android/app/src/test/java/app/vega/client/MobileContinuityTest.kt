package app.vega.client

import app.vega.client.model.ChatMessage
import app.vega.client.model.ContinuityHint
import app.vega.client.model.MessageStatus
import app.vega.client.model.MobileCommandCard
import app.vega.client.model.Role
import app.vega.client.model.continuityFrom
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class MobileContinuityTest {

    private fun card(device: String?, status: String) = MobileCommandCard(
        capability = "BATTERY_STATUS",
        status = status,
        device = device,
        transport = null,
        result = null,
        error = null,
        summary = null,
        ok = status == "success",
    )

    private fun message(card: MobileCommandCard?) = ChatMessage(
        id = System.nanoTime(),
        role = Role.ASSISTANT,
        content = "ok",
        status = MessageStatus.DONE,
        mobileCard = card,
    )

    @Test
    fun continuityNullWithoutMessages() {
        assertNull(continuityFrom(null))
        assertNull(continuityFrom(emptyList()))
    }

    @Test
    fun continuityUsesMostRecentSuccessfulDeviceAction() {
        val messages = listOf(
            message(card(device = "Galaxy A15", status = "success")),
            message(card(device = "Moto G84", status = "failed")),
        )
        val hint: ContinuityHint? = continuityFrom(messages)
        assertNotNull(hint)
        assertEquals("Galaxy A15", hint!!.device)
    }

    @Test
    fun continuityIgnoresNonSuccess() {
        val messages = listOf(
            message(card(device = "Galaxy A15", status = "denied")),
        )
        assertNull(continuityFrom(messages))
    }

    @Test
    fun continuityIgnoresCardsWithoutDevice() {
        val messages = listOf(
            message(card(device = null, status = "success")),
            message(null),
        )
        assertNull(continuityFrom(messages))
    }

    @Test
    fun statusGlyphAndLabelForCanonicalStates() {
        assertEquals("sucesso", MobileCommandCard.fromJson(
            JSONObject().put("type", "mobile_command_result").put("capability", "B").put("status", "success")
        )!!.statusLabel)
        assertEquals("\u2713", MobileCommandCard.fromJson(
            JSONObject().put("type", "mobile_command_result").put("capability", "B").put("status", "success")
        )!!.statusGlyph)
        assertEquals("negado", MobileCommandCard.fromJson(
            JSONObject().put("type", "mobile_command_result").put("capability", "B").put("status", "denied")
        )!!.statusLabel)
        assertEquals("falhou", MobileCommandCard.fromJson(
            JSONObject().put("type", "mobile_command_result").put("capability", "B").put("status", "failed")
        )!!.statusLabel)
    }
}