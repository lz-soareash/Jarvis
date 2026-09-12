package app.vega.client.model

import org.json.JSONObject

/**
 * VEGA Mobile Control (Fase 25) — espelho Android do registry do Core.
 *
 * O Core (backend/app/remote/mobile_capabilities.py) é a FONTE de verdade das
 * capabilities; este espelho existe para o executor local e a UI de controle
 * de dispositivo. O móvel SEMPRE responde com o vocabulário canônico de status
 * (pending/running/success/failed/denied/unsupported/timeout/cancelled).
 */
data class MobileCapability(
    val name: String,
    val description: String,
    val risk: String,
    val args: Map<String, Pair<String, Boolean>> = emptyMap(),
    val needsSystemPermission: String? = null,
    val executableOnMobile: Boolean = true,
)

object MobileCapabilities {
    val DEVICE_INFO = MobileCapability(
        name = "DEVICE_INFO",
        description = "Informações básicas do dispositivo (modelo, fabricante, versão Android; nunca IDs de HW).",
        risk = "low",
    )
    val BATTERY_STATUS = MobileCapability(
        name = "BATTERY_STATUS",
        description = "Nível de bateria e estado de carga.",
        risk = "low",
    )
    val NETWORK_STATUS = MobileCapability(
        name = "NETWORK_STATUS",
        description = "Tipo de rede (wifi/mobile/offline) e conectividade atual.",
        risk = "low",
    )
    val OPEN_URL = MobileCapability(
        name = "OPEN_URL",
        description = "Abre uma URL num navegador/modo externo do dispositivo.",
        risk = "low",
        args = mapOf("url" to ("str" to true), "external" to ("bool" to false)),
    )
    val VIBRATE = MobileCapability(
        name = "VIBRATE",
        description = "Vibra o dispositivo por um intervalo (ms).",
        risk = "low",
        args = mapOf("duration_ms" to ("int" to true)),
    )
    val SET_VOLUME = MobileCapability(
        name = "SET_VOLUME",
        description = "Ajusta o volume do stream de mídia/alarme/toque (0-100).",
        risk = "low",
        args = mapOf("stream" to ("str" to false), "level" to ("int" to true)),
    )
    val MEDIA_STATUS = MobileCapability(
        name = "MEDIA_STATUS",
        description = "Estado atual de mídia tocando no dispositivo (sanitizado).",
        risk = "low",
    )
    val OPEN_APP = MobileCapability(
        name = "OPEN_APP",
        description = "Abre um pacote da allowlist explícita (DEFAULT_OPEN_APP_ALLOWLIST).",
        risk = "low",
        args = mapOf("package_name" to ("str" to true)),
    )
    val SET_BRIGHTNESS = MobileCapability(
        name = "SET_BRIGHTNESS",
        description = "Ajusta o brilho da tela (0-100). Exige WRITE_SETTINGS no móvel.",
        risk = "low",
        args = mapOf("level" to ("int" to true)),
        needsSystemPermission = "android.permission.WRITE_SETTINGS",
    )
    val ACCESSIBILITY_CONTROL = MobileCapability(
        name = "ACCESSIBILITY_CONTROL",
        description = "Controle por acessibilidade (Fase futura) — DECLARADA, não executável nesta fase.",
        risk = "medium",
        executableOnMobile = false,
    )

    val ALL: List<MobileCapability> = listOf(
        DEVICE_INFO,
        BATTERY_STATUS,
        NETWORK_STATUS,
        OPEN_URL,
        VIBRATE,
        SET_VOLUME,
        MEDIA_STATUS,
        OPEN_APP,
        SET_BRIGHTNESS,
        ACCESSIBILITY_CONTROL,
    )

    val DEFAULT_OPEN_APP_ALLOWLIST: Set<String> = setOf(
        "com.android.settings",
        "com.android.chrome",
        "org.mozilla.firefox",
    )

    fun byName(name: String?): MobileCapability? {
        val key = name?.trim()?.uppercase().orEmpty()
        return ALL.find { it.name == key }
    }
}

enum class CommandStatus(val value: String) {
    PENDING("pending"),
    RUNNING("running"),
    SUCCESS("success"),
    FAILED("failed"),
    DENIED("denied"),
    UNSUPPORTED("unsupported"),
    TIMEOUT("timeout"),
    CANCELLED("cancelled"),
}

data class MobileCommand(
    val commandId: String,
    val targetDeviceId: String?,
    val capability: String,
    val args: JSONObject,
    val timeoutMs: Int,
)

data class CommandResult(
    val commandId: String,
    val status: String,
    val result: JSONObject? = null,
    val error: String? = null,
    val startedAt: String? = null,
    val finishedAt: String? = null,
)