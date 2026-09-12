package app.vega.client.mobile

import org.json.JSONObject

/**
 * Fronteira de sistema do executor (Fase 25) — separa a lógica do executor
 * (allowlist, status, timeout, idempotência) das chamadas reais do Android.
 * Permite testes JVM herméticos com um fake sem framework Android.
 *
 * NENHUMA capability desta fase executa controle profundo de tela
 * (tap/swipe/digitação) — pertencente a uma fase futura.
 */
interface MobileOps {
    fun canWriteSettings(): Boolean

    suspend fun deviceInfo(): JSONObject
    suspend fun batteryStatus(): JSONObject
    suspend fun networkStatus(): JSONObject
    suspend fun openUrl(url: String, external: Boolean): JSONObject
    suspend fun vibrate(durationMs: Int): JSONObject
    suspend fun setVolume(stream: String, level: Int): JSONObject
    suspend fun mediaStatus(): JSONObject
    suspend fun openApp(packageName: String): JSONObject
    suspend fun setBrightness(level: Int): JSONObject
}