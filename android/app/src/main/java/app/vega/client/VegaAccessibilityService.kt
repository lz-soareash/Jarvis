package app.vega.client

import android.accessibilityservice.AccessibilityService
import android.provider.Settings
import android.view.accessibility.AccessibilityEvent
import android.content.Context

/**
 * Scaffold de acessibilidade (Fase 25) — NÃO implementa controle de tela.
 *
 * A capability ACCESSIBILITY_CONTROL é DECLARADA no manifest/handler, mas o
 * executor responde `unsupported` nesta fase. Este serviço existe para (a)
 * observabilidade (estado Ativo/Inativo exposto em DeviceControl) e (b) o
 * consentimento manual do usuário via Configurações → Acessibilidade.
 *
 * canRetrieveWindowContent=false — o serviço NUNCA lê a árvore de acessibilidade.
 */
class VegaAccessibilityService : AccessibilityService() {

    companion object {
        private const val SERVICE_FLAT = "app.vega.client/.VegaAccessibilityService"

        fun isEnabled(context: Context): Boolean {
            return try {
                val flat = Settings.Secure.getString(
                    context.contentResolver,
                    Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
                ).orEmpty()
                flat.split(':').any { it.trim() == SERVICE_FLAT }
            } catch (e: Exception) {
                false
            }
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Nada a fazer: scaffold sem leitura de árvore nesta fase.
    }

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        super.onDestroy()
    }
}