package app.vega.client.model

/**
 * Navegação do VEGA Personal Assistant (Fase 27).
 *
 * Revisão crítica: o cliente móvel consolida as 5 abas anteriores em 4
 * destinos — "Assistente" (Home/chat), "Central" (conversas + operações),
 * "Dispositivo" e "Config". O Mobile é a experiência de assistente pessoal;
 * o Desktop continua sendo o Command Center.
 */
data class AppTab(
    val id: String,
    val glyph: String,
    val label: String,
)

object AppNavTabs {
    const val HOME_INDEX = 0

    val tabs: List<AppTab> = listOf(
        AppTab(id = "assistente", glyph = "V", label = "Assistente"),
        AppTab(id = "central", glyph = "≣", label = "Central"),
        AppTab(id = "dispositivo", glyph = "◆", label = "Dispositivo"),
        AppTab(id = "config", glyph = "⚙", label = "Config"),
    )
}