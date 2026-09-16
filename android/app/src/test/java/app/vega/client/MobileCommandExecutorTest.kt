package app.vega.client

import app.vega.client.mobile.MobileOps
import app.vega.client.mobile.MobileCommandExecutor
import app.vega.client.model.MobileCommand
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class FakeMobileOps : MobileOps {
    var writable = true
    val openedApps = mutableListOf<String>()
    val openedUrls = mutableListOf<String>()
    var vibratedMs: Int? = null
    var volumeLevel: Int? = null
    var volumeStream: String? = null
    var brightnessLevel: Int? = null
    var deviceInfoCalls = 0
    var suspendDurationMs = 0L

    override fun canWriteSettings(): Boolean = writable

    override suspend fun deviceInfo(): JSONObject {
        deviceInfoCalls++
        maybeSuspend()
        return JSONObject().put("model", "Teste").put("manufacturer", "VEGA")
    }

    override suspend fun batteryStatus(): JSONObject = JSONObject().put("level_percent", 80).put("charging", true)
    override suspend fun networkStatus(): JSONObject = JSONObject().put("network", "wifi").put("online", true)
    override suspend fun openUrl(url: String, external: Boolean): JSONObject {
        openedUrls.add(url)
        return JSONObject().put("opened", true)
    }
    override suspend fun vibrate(durationMs: Int): JSONObject {
        vibratedMs = durationMs
        return JSONObject().put("duration_ms", durationMs)
    }
    override suspend fun setVolume(stream: String, level: Int): JSONObject {
        volumeLevel = level
        volumeStream = stream
        return JSONObject().put("stream", stream).put("level", level)
    }
    override suspend fun mediaStatus(): JSONObject = JSONObject().put("playing", false).put("stream", "music")
    override suspend fun openApp(packageName: String): JSONObject {
        openedApps.add(packageName)
        return JSONObject().put("package_name", packageName)
    }
    override suspend fun setBrightness(level: Int): JSONObject {
        brightnessLevel = level
        return JSONObject().put("level_percent", level)
    }

    private suspend fun maybeSuspend() {
        if (suspendDurationMs > 0) delay(suspendDurationMs)
    }
}

fun cmd(cap: String, args: JSONObject = JSONObject()): MobileCommand =
    MobileCommand(commandId = "cmd-t", targetDeviceId = "dev-m", capability = cap, args = args, timeoutMs = 5000)

class MobileCommandExecutorTest {

    @Test
    fun unknownCapabilityRespondsUnsupported() = runBlocking {
        val r = MobileCommandExecutor(FakeMobileOps()).execute(cmd("HACK"))
        assertEquals("unsupported", r.status)
        assertNotNull(r.error)
    }

    @Test
    fun accessibilityControlDeclaredButUnsupported() = runBlocking {
        val r = MobileCommandExecutor(FakeMobileOps()).execute(cmd("ACCESSIBILITY_CONTROL"))
        assertEquals("unsupported", r.status)
    }

    @Test
    fun deviceInfoSucceedsAndCarriesResult() = runBlocking {
        val r = MobileCommandExecutor(FakeMobileOps()).execute(cmd("DEVICE_INFO"))
        assertEquals("success", r.status)
        assertEquals("Teste", r.result!!.optString("model"))
        assertNotNull(r.startedAt)
        assertNotNull(r.finishedAt)
    }

    @Test
    fun openAppInsideAllowlistSucceeds() = runBlocking {
        val out = FakeMobileOps()
        val r = MobileCommandExecutor(out).execute(cmd("OPEN_APP", JSONObject().put("package_name", "com.android.settings")))
        assertEquals("success", r.status)
        assertEquals(listOf("com.android.settings"), out.openedApps)
    }

    @Test
    fun openAppOutsideAllowlistIsDenied() = runBlocking {
        val out = FakeMobileOps()
        val r = MobileCommandExecutor(out).execute(cmd("OPEN_APP", JSONObject().put("package_name", "com.evil.app")))
        assertEquals("denied", r.status)
        assertTrue(out.openedApps.isEmpty())
    }

    @Test
    fun openAppWithMissingPackageArgIsDenied() = runBlocking {
        val out = FakeMobileOps()
        val r = MobileCommandExecutor(out).execute(cmd("OPEN_APP"))
        assertEquals("denied", r.status)
        assertTrue(out.openedApps.isEmpty())
    }

    @Test
    fun setBrightnessDeniedWithoutWriteSettingsPermission() = runBlocking {
        val out = FakeMobileOps()
        out.writable = false
        val r = MobileCommandExecutor(out).execute(cmd("SET_BRIGHTNESS", JSONObject().put("level", 50)))
        assertEquals("denied", r.status)
        assertNull(out.brightnessLevel)
    }

    @Test
    fun setBrightnessSucceedsWithWriteSettingsGranted() = runBlocking {
        val out = FakeMobileOps()
        val r = MobileCommandExecutor(out).execute(cmd("SET_BRIGHTNESS", JSONObject().put("level", 30)))
        assertEquals("success", r.status)
        assertEquals(30, out.brightnessLevel)
    }

    @Test
    fun vibratePassesDurationThrough() = runBlocking {
        val out = FakeMobileOps()
        val r = MobileCommandExecutor(out).execute(cmd("VIBRATE", JSONObject().put("duration_ms", 700)))
        assertEquals("success", r.status)
        assertEquals(700, out.vibratedMs)
    }

    @Test
    fun setVolumePassesStreamAndLevelThrough() = runBlocking {
        val out = FakeMobileOps()
        val r = MobileCommandExecutor(out).execute(cmd("SET_VOLUME", JSONObject().put("stream", "alarm").put("level", 40)))
        assertEquals("success", r.status)
        assertEquals(40, out.volumeLevel)
        assertEquals("alarm", out.volumeStream)
    }

    @Test
    fun timeoutRespondsTimeoutStatus() = runBlocking {
        val out = FakeMobileOps()
        out.suspendDurationMs = 10_000L
        val r = MobileCommandExecutor(out).execute(cmd("DEVICE_INFO"))
        assertEquals("timeout", r.status)
    }

    @Test
    fun duplicateCommandIdWhileRunningSharesSingleResult() = runBlocking {
        // Fase 27.2 (F4) — duplicado NÃO é CANCELLED fake: aguarda o MESMO
        // resultado terminal da execução real (uma única execução física).
        val out = FakeMobileOps()
        out.suspendDurationMs = 300L
        val ex = MobileCommandExecutor(out)

        val firstStatus = arrayOfNulls<String>(1)
        val secondStatus = arrayOfNulls<String>(1)
        val first = launch { firstStatus[0] = ex.execute(cmd("DEVICE_INFO")).status }
        val second = launch { secondStatus[0] = ex.execute(cmd("DEVICE_INFO")).status }
        first.join()
        second.join()
        assertEquals("success", firstStatus[0])
        assertEquals("success", secondStatus[0])
        assertEquals(1, out.deviceInfoCalls)
    }

    @Test
    fun openUrlAcceptsHttpAndHttpsScheme() = runBlocking {
        val out = FakeMobileOps()
        val r = MobileCommandExecutor(out).execute(
            cmd("OPEN_URL", JSONObject().put("url", "https://www.google.com/search?q=fiap"))
        )
        assertEquals("success", r.status)
        assertEquals(listOf("https://www.google.com/search?q=fiap"), out.openedUrls)
    }

    @Test
    fun openUrlRejectsUnsafeSchemesAndCredentials() = runBlocking {
        val out = FakeMobileOps()
        val ex = MobileCommandExecutor(out)
        val bad = listOf(
            "intent://evil/#Intent;scheme=https;end",
            "file:///etc/hosts",
            "content://com.foo/data",
            "javascript:alert(1)",
            "tel:+5511999999999",
            "mailto:you@example.com",
            "myapp://open",
            "google.com",
            "https://user:pass@host.example.com/x",
        )
        for (url in bad) {
            val r = ex.execute(cmd("OPEN_URL", JSONObject().put("url", url)))
            assertEquals("denied", r.status)
            assertNotNull(r.error)
        }
        assertTrue(out.openedUrls.isEmpty())
    }

    @Test
    fun completeAndReentrantExecutionRunsOnce() = runBlocking {
        val out = FakeMobileOps()
        val ex = MobileCommandExecutor(out)
        val r1 = ex.execute(cmd("DEVICE_INFO"))
        val r2 = ex.execute(cmd("DEVICE_INFO", JSONObject()))
        assertEquals("success", r1.status)
        assertEquals("success", r2.status)
    }

    @Test
    fun repollAfterCompletionReturnsStoredResultWithoutReExecution() = runBlocking {
        val out = FakeMobileOps()
        val ex = MobileCommandExecutor(out)
        val r1 = ex.execute(cmd("DEVICE_INFO"))
        assertEquals("success", r1.status)
        assertEquals(1, out.deviceInfoCalls)
        val r2 = ex.execute(cmd("DEVICE_INFO"))
        assertEquals("success", r2.status)
        assertEquals(1, out.deviceInfoCalls)
    }
}