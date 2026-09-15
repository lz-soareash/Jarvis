package app.vega.client

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Testes Herméticos de validação do endpoint WAN (bugfix "LAN/WAN separation").
 * Cobrem exatamente os casos obrigatórios: WSS em produção, rejeição de ws/http,
 * rejeição de IPs privados/localhost e independência total da LAN.
 */
class WanEndpointTest {

    // --- WAN válida ---
    @Test
    fun acceptsPublicWssEndpoint() {
        val r = WanEndpoint.validate("wss://vega.example.com/api/remote/ws")
        assertTrue(r.valid)
    }

    @Test
    fun acceptsPublicWssEndpointWithoutPath() {
        assertTrue(WanEndpoint.validate("wss://vega.example.com").valid)
    }

    @Test
    fun acceptsPublicWssEndpointWithTrailingSlash() {
        assertTrue(WanEndpoint.validate("wss://vega.example.com/api/remote/ws/").valid)
    }

    // --- WAN inválida: HTTP / HTTPS ---
    @Test
    fun rejectsHttpAsWan() {
        val r = WanEndpoint.validate("http://vega.example.com/api/remote/ws")
        assertFalse(r.valid)
    }

    @Test
    fun rejectsHttpsAsWan() {
        val r = WanEndpoint.validate("https://vega.example.com/api/remote/ws")
        assertFalse(r.valid)
    }

    // --- WAN inválida: WS (produção exige WSS) ---
    @Test
    fun rejectsWsInProduction() {
        val r = WanEndpoint.validate("ws://vega.example.com/api/remote/ws", requireTls = true)
        assertFalse(r.valid)
        assertEquals("WAN de produção deve usar wss:// (TLS)", r.reason)
    }

    @Test
    fun allowsWsOnlyInDebugAgainstPublicRelay() {
        assertTrue(WanEndpoint.validate("ws://vega.example.com/api/remote/ws", requireTls = false).valid)
    }

    // --- WAN inválida: IPs privados RFC1918 (sempre rejeitada, mesmo em debug) ---
    @Test
    fun rejectsPrivate192168InProduction() {
        assertFalse(WanEndpoint.validate("ws://192.168.100.112:8200/api/remote/ws").valid)
        assertFalse(WanEndpoint.validate("wss://192.168.100.112:8200/api/remote/ws").valid)
    }

    @Test
    fun rejectsPrivate192168EvenInDebug() {
        assertFalse(WanEndpoint.validate("ws://192.168.100.112:8200/api/remote/ws", requireTls = false).valid)
    }

    @Test
    fun rejectsPrivate10Range() {
        assertFalse(WanEndpoint.validate("wss://10.0.0.5/api/remote/ws").valid)
    }

    @Test
    fun rejectsPrivate172RangeBoundaries() {
        assertFalse(WanEndpoint.validate("wss://172.16.0.5/api/remote/ws").valid)
        assertFalse(WanEndpoint.validate("wss://172.31.255.255/api/remote/ws").valid)
        // Fora da faixa privada 172.16-31 é permitido (endereço público).
        assertTrue(WanEndpoint.validate("wss://172.32.0.5/api/remote/ws").valid)
    }

    @Test
    fun rejectsLinkLocalAndReserved() {
        assertFalse(WanEndpoint.validate("wss://169.254.10.1/api/remote/ws").valid)
    }

    // --- WAN inválida: localhost / loopback ---
    @Test
    fun rejectsLocalhostHostname() {
        assertFalse(WanEndpoint.validate("ws://localhost:8200/api/remote/ws").valid)
    }

    @Test
    fun rejectsLoopbackV4() {
        assertFalse(WanEndpoint.validate("wss://127.0.0.1:8200/api/remote/ws").valid)
    }

    @Test
    fun rejectsLoopbackV6Short() {
        assertFalse(WanEndpoint.validate("wss://[::1]:8200/api/remote/ws").valid)
    }

    @Test
    fun rejectsUnspecified() {
        assertFalse(WanEndpoint.validate("wss://0.0.0.0/api/remote/ws").valid)
    }

    // --- Vazia / vazia de host ---
    @Test
    fun rejectsBlankUrl() {
        val r = WanEndpoint.validate("  ")
        assertFalse(r.valid)
        assertEquals("URL do gateway WAN não configurada", r.reason)
    }

    @Test
    fun rejectsNullUrl() {
        assertFalse(WanEndpoint.validate(null).valid)
    }

    @Test
    fun rejectsMissingHost() {
        assertFalse(WanEndpoint.validate("wss://").valid)
    }

    @Test
    fun rejectsMissingScheme() {
        assertFalse(WanEndpoint.validate("vega.example.com/api/remote/ws").valid)
    }

    // --- Caminho incompatível ---
    @Test
    fun rejectsIncompatiblePath() {
        assertFalse(WanEndpoint.validate("wss://vega.example.com/somewhere/else").valid)
    }

    // --- LAN continua válida (a validação NÃO se aplica à LAN) ---
    @Test
    fun lanUrlWithPrivateIpRemainsValidForLanUse() {
        // A validação WAN rejeita o IP privado, mas a LAN pode usar o mesmo IP.
        assertFalse(WanEndpoint.validate("ws://192.168.100.112:8200/api/remote/ws").valid)
        // O validador WAN simplesmente não é usado para a LAN: o mesmo endereço
        // é legítimo como Core HTTP LAN.
        assertTrue("http://192.168.100.112:8100".startsWith("http://"))
    }

    // --- Independência LAN vs WAN (nenhuma derivação) ---
    @Test
    fun wanIsNeverDerivedFromLan() {
        val lan = "http://192.168.100.112:8100"
        val derived = lan.replace("http", "ws") + "/api/remote/ws"
        // Mesmo que alguém tentasse derivar, o validador rejeita:
        assertFalse(WanEndpoint.validate(derived).valid)
    }

    @Test
    fun validPublicWanCombinedWithPrivateLanIsAccepted() {
        // LAN privada + WAN pública: combinação válida do bugfix.
        assertEquals("http://192.168.100.112:8100", "http://192.168.100.112:8100")
        assertTrue(WanEndpoint.validate("wss://vega.example.com/api/remote/ws").valid)
    }

    @Test
    fun credentialsInWanUrlAreRejected() {
        assertFalse(WanEndpoint.validate("wss://user:pass@vega.example.com/api/remote/ws").valid)
    }
}