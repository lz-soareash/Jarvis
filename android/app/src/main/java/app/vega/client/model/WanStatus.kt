package app.vega.client.model

import app.vega.client.WanEndpoint

/**
 * Estado agregado do canal WAN, derivado da URL configurada e do estado de
 * transporte do VegaWan. Compartilhado na UI por `ChatViewModel.wanStatus`.
 *
 * Bugfix "LAN/WAN separation": NOT_CONFIGURED / INVALID são estados GUI
 * derivados da validação — NUNCA caem para LAN.
 */
enum class WanStatus(val label: String) {
    NOT_CONFIGURED("não configurado"),
    INVALID("inválido"),
    DISCONNECTED("desconectado"),
    CONNECTING("conectando"),
    CONNECTED("conectado"),
    RECONNECTING("reconectando"),
    ERROR("erro"),
}

object WanStatusResolver {
    /**
     * Resolve o estado do canal WAN de forma pura (sem dependências Android)
     * a partir do valor persistido e do estado de transporte.
     *
     * Em builds debug, `requireTls=false` permite ws:// contra um relay público
     * para testes; em release, somente wss:// é aceito.
     */
    fun resolve(configuredUrl: String?, transportState: String, requireTls: Boolean): WanStatus {
        val url = configuredUrl?.trim().orEmpty()
        if (url.isEmpty()) return WanStatus.NOT_CONFIGURED
        if (!WanEndpoint.isValid(url, requireTls)) return WanStatus.INVALID
        return when (transportState) {
            "connected" -> WanStatus.CONNECTED
            "connecting" -> WanStatus.CONNECTING
            "reconnecting" -> WanStatus.RECONNECTING
            "authentication_error", "core_unavailable" -> WanStatus.ERROR
            else -> WanStatus.DISCONNECTED
        }
    }
}