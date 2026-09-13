package app.vega.client

import app.vega.client.model.AppNavTabs
import org.junit.Assert.assertEquals
import org.junit.Test

class NavTabsTest {

    @Test
    fun personalAssistantKeepsCoreDestinations() {
        assertEquals(
            listOf("assistente", "central", "dispositivo", "config"),
            AppNavTabs.tabs.map { it.id },
        )
    }

    @Test
    fun homeTabIsAssistantIndexZero() {
        assertEquals(0, AppNavTabs.HOME_INDEX)
        assertEquals("assistente", AppNavTabs.tabs[AppNavTabs.HOME_INDEX].id)
    }

    @Test
    fun labelsAreCanonical() {
        assertEquals(
            listOf("Assistente", "Central", "Dispositivo", "Config"),
            AppNavTabs.tabs.map { it.label },
        )
    }

    @Test
    fun idsAreUnique() {
        assertEquals(4, AppNavTabs.tabs.map { it.id }.toSet().size)
    }
}