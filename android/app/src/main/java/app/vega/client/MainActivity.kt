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
import androidx.compose.material3.HorizontalDivider
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
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
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
        "reconnecting" -> "○ Reconectando…" to Color(0xFFFFD166)
        "authentication_error", "core_unavailable" -> "○ Core indisponível" to Color(0xFFFF6B6B)
        "error" -> "○ Servidor indisponível" to Color(0xFFFF6B6B)
        else -> "◌ Não conectado" to Color(0xFF8FA3B6)
    }
}

private fun wanStateLabel(state: String): Pair<String, Color> {
    return when (state) {
        "connected" -> "● WAN ativo" to Color(0xFF2FE6A5)
        "connecting" -> "○ Conectando WAN…" to Color(0xFFFFD166)
        "reconnecting" -> "○ Reconectando WAN…" to Color(0xFFFFD166)
        "authentication_error", "core_unavailable" -> "○ Core remoto indisponível" to Color(0xFFFF6B6B)
        else -> "◌ WAN offline" to Color(0xFF8FA3B6)
    }
}

@Composable
private fun VegaScreen() {
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(VegaBridge.DEFAULT_CORE_URL) }
    var phase by remember { mutableStateOf("manual") } // manual | connecting
    val bridge = remember { VegaBridge(App.instance, VegaBridge.DEFAULT_CORE_URL) }

    // WAN Gateway (Fase 24) — conexão remota ao MESMO AI Core do desktop.
    var wanUrl by remember { mutableStateOf("") }
    var pairingCode by remember { mutableStateOf("") }
    var wanPhase by remember { mutableStateOf("manual") }
    val wan = remember {
        VegaWan(App.instance, CoroutineScope(SupervisorJob() + Dispatchers.IO)).apply {
            if (hasToken) {
                configure(token = null, pairingCode = null, deviceId = null)
            }
        }
    }

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

    // Reflete o estado WAN (Fase 24) — polling barato, 1 Hz.
    var wanStatus by remember { mutableStateOf("offline") }
    var wanDetail by remember { mutableStateOf("") }
    var wanHeartbeats by remember { mutableStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            wanStatus = wan.state
            wanDetail = wan.detail
            wanHeartbeats = wan.heartbeatsTotal
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

    fun connectWan() {
        val normalized = VegaWan.normalizeWanUrl(wanUrl)
        if (normalized.isEmpty()) return
        wan.configure(token = null, pairingCode = pairingCode.ifBlank { null }, deviceId = null)
        wanPhase = "connecting"
        wan.connect(normalized)
    }

    fun disconnectWan() {
        wan.disconnect()
        pairingCode = ""
        wanPhase = "manual"
    }

    val (label, labelColor) = stateLabel(status)
    val (wanLabel, wanLabelColor) = wanStateLabel(wanStatus)

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

        HorizontalDivider()

        Text("WAN Gateway — fora da LAN (Fase 24)", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(
            value = wanUrl,
            onValueChange = { wanUrl = it },
            label = { Text("WAN URL (wss://…)") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            enabled = wanPhase == "manual",
        )
        OutlinedTextField(
            value = pairingCode,
            onValueChange = { pairingCode = it },
            label = { Text("Código de pareamento (só no 1º vínculo)") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            enabled = wanPhase == "manual",
        )

        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("WAN: $wanLabel", color = wanLabelColor, style = MaterialTheme.typography.titleMedium)
                Text(wanDetail.ifBlank { "device_id: ${wan.deviceId?.take(16) ?: "—"}" })
                Text("Heartbeats WAN: $wanHeartbeats · token ${if (wan.hasToken) "salvo" else "ausente"}")
                if (wanStatus == "authentication_error") {
                    Text("Autenticação WAN recusada. Verifique token/código e o Core.")
                }
            }
        }

        if (wanPhase == "manual") {
            Button(onClick = { connectWan() }, Modifier.fillMaxWidth()) {
                Text("Conectar fora da LAN (WAN)")
            }
        } else {
            Button(onClick = { disconnectWan() }, Modifier.fillMaxWidth()) {
                Text("Desconectar WAN")
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