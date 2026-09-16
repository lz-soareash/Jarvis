package app.vega.client.mobile

import app.vega.client.model.CommandResult
import app.vega.client.model.CommandStatus
import app.vega.client.model.MobileCapabilities
import app.vega.client.model.MobileCommand
import app.vega.client.model.MobileCapability
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/**
 * Executor de comandos de dispositivo (Fase 25) no cliente móvel.
 *
 * Lógica pura (testável em JVM): resolvimento de capability, allowlist para
 * OPEN_APP, gating por permissão (SET_BRIGHTNESS → WRITE_SETTINGS), timeout,
 * idempotência por command_id e emissão do vocabulário canônico de status.
 * As chamadas Android ficam na fronteira [MobileOps].
 *
 * Segurança:
 * - comando de capability DESCONHECIDA → `unsupported` (nunca roda);
 * - OPEN_APP só abre pacotes da allowlist LOCAL (→ `denied`);
 * - OPEN_URL recusa esquemas fora de http(s) e credenciais embutidas (→ `denied`);
 * - SET_BRIGHTNESS exige WRITE_SETTINGS concedido (→ `denied`);
 * - ACCESSIBILITY_CONTROL é declarada, mas executável=false (→ `unsupported`).
 * - Fase 27.2 (F4): comando duplicado EM EXECUÇÃO aguarda o MESMO resultado
 *   terminal da execução real (uma única execução física; sem CANCELLED fake).
 */
class MobileCommandExecutor(
    private val ops: MobileOps,
    private val allowlist: Set<String> = MobileCapabilities.DEFAULT_OPEN_APP_ALLOWLIST,
) {
    private val inflight = ConcurrentHashMap<String, CompletableDeferred<CommandResult>>()
    private val historical = ConcurrentHashMap<String, CommandResult>()
    private val maxHistory = 50

    suspend fun execute(command: MobileCommand): CommandResult {
        historical[command.commandId]?.let { return it }
        val started = nowIso()
        val signal = CompletableDeferred<CommandResult>()
        val existing = inflight.putIfAbsent(command.commandId, signal)
        if (existing != null) {
            // F4 — duplicado aguarda o mesmo resultado terminal (idempotência
            // sem execução dupla e sem CANCELLED artificial).
            return existing.await()
        }
        return try {
            val result = executeOne(command, started)
            remember(result)
            signal.complete(result)
            result
        } catch (e: Throwable) {
            signal.completeExceptionally(e)
            throw e
        } finally {
            inflight.remove(command.commandId)
        }
    }

    private suspend fun executeOne(command: MobileCommand, started: String): CommandResult {
        val spec = MobileCapabilities.byName(command.capability)
        if (spec == null) {
            return CommandResult(command.commandId, CommandStatus.UNSUPPORTED.value, null, "capability desconhecida no cliente", started, nowIso())
        }
        if (!spec.executableOnMobile) {
            return CommandResult(command.commandId, CommandStatus.UNSUPPORTED.value, null, "capability declarada, não executável nesta fase", started, nowIso())
        }
        val deny = localGateCheck(spec, command.args)
        if (deny != null) {
            return CommandResult(command.commandId, CommandStatus.DENIED.value, null, deny, started, nowIso())
        }
        return try {
            val out = withTimeout(command.timeoutMs.toLong().coerceAtLeast(1_000L)) {
                withContext(kotlinx.coroutines.Dispatchers.IO) { runHandler(spec, command.args) }
            }
            CommandResult(command.commandId, CommandStatus.SUCCESS.value, out, null, started, nowIso())
        } catch (e: TimeoutCancellationException) {
            CommandResult(command.commandId, CommandStatus.TIMEOUT.value, null, "tempo do comando excedido no dispositivo", started, nowIso())
        } catch (e: Exception) {
            CommandResult(command.commandId, CommandStatus.FAILED.value, null, sanitize(e), started, nowIso())
        }
    }

    private fun localGateCheck(spec: MobileCapability, args: JSONObject): String? = when (spec.name) {
        "OPEN_APP" -> {
            val pkg = args.optString("package_name").trim()
            if (pkg !in allowlist) "pacote fora da allowlist local" else null
        }
        "OPEN_URL" -> UrlAllowlist.validate(args.optString("url"))
        "SET_BRIGHTNESS" -> {
            if (!ops.canWriteSettings()) "WRITE_SETTINGS não concedido no dispositivo" else null
        }
        else -> null
    }

    private suspend fun runHandler(spec: MobileCapability, args: JSONObject): JSONObject = when (spec.name) {
        "DEVICE_INFO" -> ops.deviceInfo()
        "BATTERY_STATUS" -> ops.batteryStatus()
        "NETWORK_STATUS" -> ops.networkStatus()
        "OPEN_URL" -> ops.openUrl(args.optString("url"), args.optBoolean("external"))
        "VIBRATE" -> ops.vibrate(args.optInt("duration_ms", 500))
        "SET_VOLUME" -> ops.setVolume(args.optString("stream", "music"), args.optInt("level", 0))
        "MEDIA_STATUS" -> ops.mediaStatus()
        "OPEN_APP" -> ops.openApp(args.optString("package_name"))
        "SET_BRIGHTNESS" -> ops.setBrightness(args.optInt("level", 50))
        else -> throw IllegalStateException("capability sem handler local")
    }

    fun recentResults(): List<CommandResult> =
        historical.values.sortedByDescending { it.finishedAt }.take(10)

    private fun remember(result: CommandResult) {
        if (result.commandId.isBlank()) return
        historical[result.commandId] = result
        if (historical.size > maxHistory) {
            val oldest = historical.entries.minByOrNull { it.value.finishedAt ?: "" }?.key
            if (oldest != null) historical.remove(oldest)
        }
    }

    private fun sanitize(e: Throwable): String =
        (e.message?.trim().orEmpty().ifEmpty { "falha ao executar comando" }).take(160)

    private fun nowIso(): String =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US).format(Date())
}