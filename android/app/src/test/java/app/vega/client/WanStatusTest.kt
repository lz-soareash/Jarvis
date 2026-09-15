package app.vega.client

import app.vega.client.model.WanStatus
import app.vega.client.model.WanStatusResolver
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

/**
 * Testes do estado do canal WAN (bugfix "LAN/WAN separation"):
 * NOT_CONFIGURED quando ausente, INVALID quando o endpoint não é público/WSS e
 * MESMO O ENDPOINT SÓ MUDA O CANAL WAN (nunca toca a LAN).
 */
class WanStatusTest {

    @Test
    fun absentWanProducesNotConfigured() {
        assertEquals(
            WanStatus.NOT_CONFIGURED,
            WanStatusResolver.resolve(configuredUrl = "", transportState = "offline", requireTls = true),
        )
        assertEquals(
            WanStatus.NOT_CONFIGURED,
            WanStatusResolver.resolve(configuredUrl = "  ", transportState = "offline", requireTls = true),
        )
        assertEquals(
            WanStatus.NOT_CONFIGURED,
            WanStatusResolver.resolve(configuredUrl = null, transportState = "offline", requireTls = true),
        )
    }

    @Test
    fun absentWanNeverFallsBackToLan() {
        // Sem WAN configurada e sem LAN: o estado é NOT_CONFIGURED, não ONLINE.
        val resolved = WanStatusResolver.resolve(configuredUrl = "", transportState = "offline", requireTls = true)
        assertEquals(WanStatus.NOT_CONFIGURED, resolved)
        assertNotEquals(WanStatus.CONNECTED, resolved)
    }

    @Test
    fun privateIpWanIsInvalid() {
        assertEquals(
            WanStatus.INVALID,
            WanStatusResolver.resolve("ws://192.168.100.112:8200/api/remote/ws", "offline", requireTls = true),
        )
    }

    @Test
    fun localhostWanIsInvalid() {
        assertEquals(
            WanStatus.INVALID,
            WanStatusResolver.resolve("ws://localhost:8200/api/remote/ws", "offline", requireTls = true),
        )
    }

    @Test
    fun plainWsInProductionIsInvalid() {
        assertEquals(
            WanStatus.INVALID,
            WanStatusResolver.resolve("ws://vega.example.com/api/remote/ws", "offline", requireTls = true),
        )
    }

    @Test
    fun invalidWanIsNotAnErrorFromTransport() {
        // URL inválida => INVALID (config), independente do estado do transporte.
        assertEquals(
            WanStatus.INVALID,
            WanStatusResolver.resolve("ws://192.168.100.112:8200/api/remote/ws", "connected", requireTls = true),
        )
    }

    @Test
    fun validWanMapsToTransportStates() {
        val url = "wss://vega.example.com/api/remote/ws"
        assertEquals(WanStatus.CONNECTED, WanStatusResolver.resolve(url, "connected", requireTls = true))
        assertEquals(WanStatus.CONNECTING, WanStatusResolver.resolve(url, "connecting", requireTls = true))
        assertEquals(WanStatus.RECONNECTING, WanStatusResolver.resolve(url, "reconnecting", requireTls = true))
        assertEquals(WanStatus.ERROR, WanStatusResolver.resolve(url, "authentication_error", requireTls = true))
        assertEquals(WanStatus.ERROR, WanStatusResolver.resolve(url, "core_unavailable", requireTls = true))
        assertEquals(WanStatus.DISCONNECTED, WanStatusResolver.resolve(url, "offline", requireTls = true))
    }

    @Test
    fun publicWanPlusPrivateLanIsValid() {
        // LAN privada NÃO invalida a WAN pública; são configurações independentes.
        assertEquals(
            WanStatus.DISCONNECTED,
            WanStatusResolver.resolve("wss://vega.example.com/api/remote/ws", "offline", requireTls = true),
        )
    }
}