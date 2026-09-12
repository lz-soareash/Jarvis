package app.vega.client.mobile

import android.media.AudioManager
import android.os.BatteryManager
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.provider.Settings
import org.json.JSONObject

/** Implementação real do MobileOps usando APIs do Android (Fase 25). */
class AndroidMobileOps(
    private val context: Context,
) : MobileOps {

    override fun canWriteSettings(): Boolean = Settings.System.canWrite(context)

    override suspend fun deviceInfo(): JSONObject =
        JSONObject()
            .put("model", Build.MODEL)
            .put("manufacturer", Build.MANUFACTURER)
            .put("android_version", Build.VERSION.RELEASE)

    override suspend fun batteryStatus(): JSONObject {
        val bm = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val level = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val status = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_STATUS)
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        return JSONObject()
            .put("level_percent", level.coerceIn(0, 100))
            .put("charging", charging)
    }

    override suspend fun networkStatus(): JSONObject {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        val network = when {
            caps == null -> "offline"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "mobile"
            else -> "offline"
        }
        val online = caps != null
        return JSONObject()
            .put("network", network)
            .put("online", online)
    }

    override suspend fun openUrl(url: String, external: Boolean): JSONObject {
        try {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (external) {
                context.startActivity(
                    Intent.createChooser(intent, "Abrir URL").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            } else {
                context.startActivity(intent)
            }
            return JSONObject().put("opened", true)
        } catch (e: ActivityNotFoundException) {
            throw IllegalStateException("nenhum app p/ abrir a URL")
        }
    }

    override suspend fun vibrate(durationMs: Int): JSONObject {
        val vibrator = context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
        if (!vibrator.hasVibrator()) throw IllegalStateException("dispositivo sem vibrator")
        val ms = durationMs.toLong().coerceIn(50L, 5000L)
        vibrator.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
        return JSONObject().put("duration_ms", ms)
    }

    override suspend fun setVolume(stream: String, level: Int): JSONObject {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val streamType = when (stream.lowercase()) {
            "alarm" -> AudioManager.STREAM_ALARM
            "ring" -> AudioManager.STREAM_RING
            "notification" -> AudioManager.STREAM_NOTIFICATION
            else -> AudioManager.STREAM_MUSIC
        }
        val max = am.getStreamMaxVolume(streamType)
        val target = ((level.coerceIn(0, 100) * max) / 100).coerceIn(0, max)
        am.setStreamVolume(streamType, target, 0)
        return JSONObject().put("stream", streamTypeName(streamType)).put("level", target)
    }

    override suspend fun mediaStatus(): JSONObject {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val current = am.getStreamVolume(AudioManager.STREAM_MUSIC)
        return JSONObject()
            .put("playing", am.isMusicActive)
            .put("stream", "music")
            .put("volume_percent", if (max > 0) (current * 100) / max else 0)
    }

    override suspend fun openApp(packageName: String): JSONObject {
        val pi = context.packageManager.getLaunchIntentForPackage(packageName)
            ?: throw IllegalStateException("app não instalado")
        pi.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(pi)
        return JSONObject().put("package_name", packageName)
    }

    override suspend fun setBrightness(level: Int): JSONObject {
        if (!canWriteSettings()) throw IllegalStateException("WRITE_SETTINGS não concedido")
        val lvl = level.coerceIn(0, 100)
        val value = (lvl * 255) / 100
        Settings.System.putInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS, value)
        return JSONObject().put("level_percent", lvl)
    }

    private fun streamTypeName(type: Int): String = when (type) {
        AudioManager.STREAM_ALARM -> "alarm"
        AudioManager.STREAM_RING -> "ring"
        AudioManager.STREAM_NOTIFICATION -> "notification"
        else -> "music"
    }
}