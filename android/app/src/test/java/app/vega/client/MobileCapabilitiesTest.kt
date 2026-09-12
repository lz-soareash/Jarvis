package app.vega.client

import app.vega.client.model.MobileCapabilities
import org.junit.Assert.*
import org.junit.Test

class MobileCapabilitiesTest {

    @Test
    fun containsExactlyTenRegisteredCapabilities() {
        assertEquals(10, MobileCapabilities.ALL.size)
        for (cap in MobileCapabilities.ALL) {
            assertNotNull(MobileCapabilities.byName(cap.name))
        }
    }

    @Test
    fun byNameCaseInsensitive() {
        assertEquals(MobileCapabilities.DEVICE_INFO, MobileCapabilities.byName("device_info"))
        assertEquals(MobileCapabilities.DEVICE_INFO, MobileCapabilities.byName("  Device_Info  "))
    }

    @Test
    fun byNameUnknownReturnsNull() {
        assertNull(MobileCapabilities.byName(null))
        assertNull(MobileCapabilities.byName("HACK"))
        assertNull(MobileCapabilities.byName(""))
    }

    @Test
    fun accessibilityControlDeclaredButNotExecutable() {
        assertTrue(MobileCapabilities.ACCESSIBILITY_CONTROL.name.isNotBlank())
        assertFalse(MobileCapabilities.ACCESSIBILITY_CONTROL.executableOnMobile)
    }

    @Test
    fun setBrightnessRequiresWriteSettings() {
        assertEquals("android.permission.WRITE_SETTINGS", MobileCapabilities.SET_BRIGHTNESS.needsSystemPermission)
    }

    @Test
    fun openAppHasPackageArg() {
        val args = MobileCapabilities.OPEN_APP.args
        assertEquals(1, args.size)
        val pkg = args["package_name"]!!
        assertEquals("str", pkg.first)
        assertTrue(pkg.second)
    }

    @Test
    fun defaultAllowlistContainsExpectedPackages() {
        val allowlist = MobileCapabilities.DEFAULT_OPEN_APP_ALLOWLIST
        assertEquals(3, allowlist.size)
        assertTrue("com.android.settings" in allowlist)
        assertTrue("com.android.chrome" in allowlist)
        assertTrue("org.mozilla.firefox" in allowlist)
    }

    @Test
    fun nonExecutableCapabilitiesAreOnlyAccessibility() {
        val nonExec = MobileCapabilities.ALL.filter { !it.executableOnMobile }
        assertEquals(1, nonExec.size)
        assertEquals("ACCESSIBILITY_CONTROL", nonExec[0].name)
    }
}