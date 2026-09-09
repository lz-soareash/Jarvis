package app.vega.client

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    VegaScreen()
                }
            }
        }
    }
}

private fun stateLabel(state: String): Pair<String, Color> {
    return when (state) {
        "connected" -> "● Conectado" to Color(0xFF2FE6A5)
        "registering", "pairing" -> "○ Conectando…" to Color(0xFFFFD166)
        "connecting" -> "○ Reconectando…" to Color(0xFFFFD166)
        "error" -> "○ Servidor indisponível" to Color(0xFFFF6B6B)
        else -> "◌ Não conectado" to Color(0xFF8FA3B6)
    }
}

@Composable
private fun VegaScreen() {
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(VegaBridge.DEFAULT_CORE_URL) }
    var phase by remember { mutableStateOf("manual") } // manual | connecting
    val bridge = remember { VegaBridge(App.instance, VegaBridge.DEFAULT_CORE_URL) }

    // Reflete o estado vivo do bridge para a UI (polling barato, 1 Hz).
    var status by remember { mutableStateOf("idle") }
    var detail by remember { mutableStateOf("") }
    var heartbeats by remember { mutableStateOf(0) }
    var hasToken by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        while (true) {
            status = bridge.state
            detail = bridge.detail
            heartbeats = bridge.heartbeatsTotal
            hasToken = bridge.hasToken
            delay(1_000)
        }
    }

    // Fase 22 — Já pareado? Reconecta automaticamente ao abrir (Device Bridge).
    LaunchedEffect(Unit) {
        if (bridge.hasToken) {
            phase = "connecting"
            bridge.coreUrl = url.trim().ifBlank { VegaBridge.DEFAULT_CORE_URL }
            scope.launch { bridge.boot() }
        }
    }

    fun connect() {
        bridge.coreUrl = url.trim().ifBlank { VegaBridge.DEFAULT_CORE_URL }
        phase = "connecting"
        scope.launch { bridge.boot() }
    }

    fun disconnect() {
        scope.launch {
            bridge.leave()
            phase = "manual"
        }
    }

    val (label, labelColor) = stateLabel(status)

    Column(
        Modifier
            .fillMaxSize()
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("VEGA Mobile — V${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.titleLarge)
        Text("UMA IA, MÚLTIPLOS CLIENTES: sem segundo AI Core neste APK.")

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("Core URL") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            enabled = phase == "manual",
        )

        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Estado: $label", color = labelColor, style = MaterialTheme.typography.titleMedium)
                Text(detail.ifBlank { "device_id: ${bridge.deviceId?.take(16) ?: "—"}" })
                Text(
                    "Heartbeats: $heartbeats · token ${if (hasToken) "salvo" else "ausente"} · " +
                        "intervalo ${bridge.heartbeatMs / 1000}s · app v${BuildConfig.VERSION_NAME}"
                )
                if (status == "error") {
                    Text("O servidor VEGA não está disponível. Verifique o Core URL e a rede.")
                }
            }
        }

        if (phase == "manual") {
            Button(onClick = { connect() }, Modifier.fillMaxWidth()) {
                Text("Conectar ao VEGA")
            }
        } else {
            Button(onClick = { disconnect() }, Modifier.fillMaxWidth()) {
                Text("Desconectar")
            }
        }
    }
}

object App {
    lateinit var instance: android.app.Application
}

class VegaApplication : android.app.Application() {
    override fun onCreate() {
        super.onCreate()
        App.instance = this
    }
}