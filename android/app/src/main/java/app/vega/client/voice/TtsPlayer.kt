package app.vega.client.voice

import android.media.AudioAttributes
import android.media.MediaPlayer

class TtsPlayer {
    var onState: ((String) -> Unit)? = null

    private var player: MediaPlayer? = null

    fun play(url: String) {
        stop()
        val p = MediaPlayer()
        p.setAudioAttributes(
            AudioAttributes.Builder()
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .build()
        )
        p.setOnPreparedListener { mp ->
            onState?.invoke("playing")
            mp.start()
        }
        p.setOnCompletionListener { mp ->
            onState?.invoke("idle")
            mp.release()
            if (player === mp) player = null
        }
        p.setOnErrorListener { mp, _, _ ->
            onState?.invoke("error")
            mp.release()
            if (player === mp) player = null
            true
        }
        player = p
        try {
            p.setDataSource(url)
            p.prepareAsync()
        } catch (e: Exception) {
            onState?.invoke("error")
            p.release()
            if (player === p) player = null
        }
    }

    fun stop() {
        player?.let {
            runCatching { it.stop() }
            runCatching { it.release() }
        }
        player = null
        onState?.invoke("idle")
    }
}