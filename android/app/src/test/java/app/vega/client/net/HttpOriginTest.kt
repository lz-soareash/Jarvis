package app.vega.client.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Fase 27.1 — regressão da origem HTTP derivada (Kotlin puro).
 *
 * Garante que bases LAN (http/https), WAN (ws/wss), host puro ou inválidas
 * nunca chegam ao OkHttp sem esquema (crash "Expected URL scheme 'http' or
 * 'https' but no scheme was found").
 */
class HttpOriginTest {

    @Test
    fun keepsHttpAndStripsTrailingPath() {
        assertEquals("http://192.168.0.10:8100", HttpOrigin.normalize("http://192.168.0.10:8100/"))
        assertEquals("https://core.example.com", HttpOrigin.normalize("https://core.example.com/api/v1"))
    }

    @Test
    fun mapsWebSocketSchemeToHttp() {
        assertEquals("http://192.168.0.10:8100", HttpOrigin.normalize("ws://192.168.0.10:8100"))
        assertEquals(
            "https://wallpapers-binding-nil-stereo.trycloudflare.com",
            HttpOrigin.normalize("wss://wallpapers-binding-nil-stereo.trycloudflare.com/api/remote/ws"),
        )
    }

    @Test
    fun assumesHttpForSchemeLessHost() {
        assertEquals("http://192.168.0.10:8100", HttpOrigin.normalize("192.168.0.10:8100"))
        assertEquals("http://localhost:8100", HttpOrigin.normalize("localhost:8100"))
    }

    @Test
    fun rejectsEmptyAndUnknownSchemes() {
        assertNull(HttpOrigin.normalize(null))
        assertNull(HttpOrigin.normalize(""))
        assertNull(HttpOrigin.normalize("   "))
        assertNull(HttpOrigin.normalize("ftp://host:21"))
        assertNull(HttpOrigin.normalize("wss://"))
    }

    @Test
    fun fromWanRequiresWebSocketScheme() {
        assertEquals("https://host.example.com", HttpOrigin.fromWan("wss://host.example.com/api/remote/ws"))
        assertEquals("http://host:8200", HttpOrigin.fromWan("ws://host:8200"))
        assertNull(HttpOrigin.fromWan("http://host"))
        assertNull(HttpOrigin.fromWan(""))
        assertNull(HttpOrigin.fromWan(null))
    }
}
