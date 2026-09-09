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

    Column(
        Modifier
            .fillMaxSize()
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("VEGA Mobile — cliente fino do Core", style = MaterialTheme.typography.titleLarge)
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
                Text("Estado: $status", style = MaterialTheme.typography.titleMedium)
                Text(detail.ifBlank { "device_id: ${bridge.deviceId?.take(16) ?: "—"}" })
                Text(
                    "Heartbeats: $heartbeats · token ${if (hasToken) "salvo" else "ausente"} · " +
                        "intervalo ${bridge.heartbeatMs / 1000}s"
                )
            }
        }

        if (phase == "manual") {
            Button(onClick = { connect() }, Modifier.fillMaxWidth()) {
                Text("Conectar ao Core")
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