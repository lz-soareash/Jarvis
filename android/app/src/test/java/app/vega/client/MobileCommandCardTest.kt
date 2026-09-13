package app.vega.client

import app.vega.client.model.MobileCommandCard
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MobileCommandCardTest {

    @Test
    fun fromJsonTypicalBatterySuccess() {
        val json = JSONObject()
            .put("type", "mobile_command_result")
            .put("capability", "BATTERY_STATUS")
            .put("status", "success")
            .put("device", "Galaxy A15")
            .put("transport", "wan")
            .put("result", JSONObject().put("level_percent", 80).put("charging", true))
            .put("summary", "Bateria em 80%")
        val c = MobileCommandCard.fromJson(json)
        assertNotNull(c)
        assertEquals("BATTERY_STATUS", c!!.capability)
        assertEquals("success", c.status)
        assertTrue(c.ok)
        assertEquals("Galaxy A15", c.device)
        assertEquals("wan", c.transport)
        assertEquals(80, c.result!!.optInt("level_percent"))
        assertEquals("Bateria em 80%", c.summary)
        assertEquals("Bateria", c.title)
    }

    @Test
    fun fromJsonRejectsNonMobileResult() {
        assertNull(MobileCommandCard.fromJson(null))
        assertNull(MobileCommandCard.fromJson(JSONObject().put("type", "other").put("status", "success")))
        assertNull(MobileCommandCard.fromJson(JSONObject().put("type", "mobile_command_result")))
    }

    @Test
    fun fromJsonFailureCarriesErrorAndOkFalse() {
        val json = JSONObject()
            .put("type", "mobile_command_result")
            .put("capability", "OPEN_URL")
            .put("status", "denied")
            .put("error", "comando negado no dispositivo")
        val c = MobileCommandCard.fromJson(json)!!
        assertFalse(c.ok)
        assertEquals("denied", c.status)
        assertEquals("comando negado no dispositivo", c.error)
    }

    @Test
    fun a11yDescriptionIncludesSummary() {
        val c = MobileCommandCard.fromJson(
            JSONObject()
                .put("type", "mobile_command_result")
                .put("capability", "BATTERY_STATUS")
                .put("status", "success")
                .put("summary", "Bateria em 80%")
        )!!
        val desc = c.a11yDescription()
        assertTrue(desc.contains("Bateria"))
        assertTrue(desc.contains("success"))
        assertTrue(desc.contains("80%"))
    }

    @Test
    fun a11yDescriptionFallsBackToResultKeys() {
        val c = MobileCommandCard.fromJson(
            JSONObject()
                .put("type", "mobile_command_result")
                .put("capability", "NETWORK_STATUS")
                .put("status", "success")
                .put("result", JSONObject().put("network", "wifi"))
        )!!
        assertTrue(c.a11yDescription().contains("wifi"))
    }
}