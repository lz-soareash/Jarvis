package app.vega.client

import app.vega.client.model.VegaPresence
import org.junit.Assert.assertEquals
import org.junit.Test

class VegaPresenceTest {

    @Test
    fun canonicalKeepsKnownStates() {
        for (s in listOf("idle", "thinking", "working", "listening", "speaking", "waiting_confirmation", "error", "offline", "success", "warning")) {
            assertEquals(s, VegaPresence.canonical(s))
        }
    }

    @Test
    fun aliasOnlineBecomesIdle() {
        assertEquals("idle", VegaPresence.canonical("online"))
        assertEquals("idle", VegaPresence.canonical("ready"))
    }

    @Test
    fun aliasConnectingBecomesIdle() {
        assertEquals("idle", VegaPresence.canonical("connecting"))
    }

    @Test
    fun unknownMapsToIdle() {
        assertEquals("idle", VegaPresence.canonical("qualquer-coisa"))
        assertEquals("idle", VegaPresence.canonical(""))
        assertEquals("idle", VegaPresence.canonical(null))
    }

    @Test
    fun caseInsensitiveAndTrimmed() {
        assertEquals("thinking", VegaPresence.canonical("  Thinking "))
        assertEquals("listening", VegaPresence.canonical("LISTENING"))
    }

    @Test
    fun labelsExistForCanonicalStates() {
        assertEquals("pensando", VegaPresence.label("thinking"))
        assertEquals("executando", VegaPresence.label("executing"))
        assertEquals("aguardando confirmação", VegaPresence.label("waiting_confirmation"))
    }

    @Test
    fun labelForAliasUsesCanonical() {
        assertEquals("ociosa", VegaPresence.label("online"))
    }
}