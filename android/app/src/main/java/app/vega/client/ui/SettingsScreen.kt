package app.vega.client.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.vega.client.BuildConfig
import app.vega.client.ChatViewModel
import app.vega.client.WanEndpoint
import app.vega.client.model.WanStatus
import app.vega.client.ui.theme.VegaColors

@Composable
fun SettingsScreen(vm: ChatViewModel) {
    val coreUrl by vm.coreUrl.collectAsState()
    val wanUrl by vm.wanUrl.collectAsState()
    val wanStatus by vm.wanStatus.collectAsState()
    val connection by vm.connection.collectAsState()
    val wsDetail by vm.wsDetail.collectAsState()
    val heartbeats by vm.heartbeats.collectAsState()
    val ttsEnabled by vm.ttsEnabled.collectAsState()
    val bridge = vm.bridge

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(VegaColors.Background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 12.dp),
    ) {
        Spacer(Modifier.height(14.dp))
        Text("Configurações", fontSize = 20.sp, fontWeight = FontWeight.Bold, color = VegaColors.TextPrimary)
        Spacer(Modifier.height(12.dp))

        SectionTitle("Core (LAN)")
        StatusRow("Estado", connection.label + if (connection.label == "ONLINE") " — $wsDetail" else "")
        bridge.deviceId?.let {
            StatusRow("Dispositivo pareado", it)
        }
        StatusRow("Heartbeats", heartbeats.toString())
        OutlinedTextField(
            value = coreUrl,
            onValueChange = { vm.setCoreUrl(it) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("URL do Core", color = VegaColors.TextMuted, fontSize = 12.sp) },
            singleLine = true,
            textStyle = androidx.compose.ui.text.TextStyle(color = VegaColors.TextPrimary, fontSize = 13.sp),
            shape = RoundedCornerShape(12.dp),
        )
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { vm.connect() },
                colors = ButtonDefaults.buttonColors(containerColor = VegaColors.Primary, contentColor = Color(0xFF04101D)),
                modifier = Modifier.weight(1f),
            ) { Text("Conectar", fontSize = 13.sp, fontWeight = FontWeight.Bold) }
            OutlinedButton(onClick = { vm.disconnectLan() }, modifier = Modifier.weight(1f)) {
                Text("Desconectar", fontSize = 12.sp)
            }
        }
        Spacer(Modifier.height(16.dp))

        SectionTitle("WAN (via relé)")
        StatusRow("Estado", wanStatus.label + wanDetailSuffix(wanStatus, vm))
        OutlinedTextField(
            value = wanUrl,
            onValueChange = { vm.setWanUrl(it) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("URL do gateway WAN", color = VegaColors.TextMuted, fontSize = 12.sp) },
            placeholder = { Text("wss://…", color = VegaColors.TextMuted, fontSize = 12.sp) },
            singleLine = true,
            textStyle = androidx.compose.ui.text.TextStyle(color = VegaColors.TextPrimary, fontSize = 13.sp),
            shape = RoundedCornerShape(12.dp),
            isError = wanStatus == WanStatus.INVALID,
        )
        if (wanUrl.isNotBlank() && !WanEndpoint.isValid(wanUrl, requireTls = !BuildConfig.DEBUG)) {
            Text(
                "A WAN exige wss:// e um endpoint público (IP privado e localhost são rejeitados).",
                color = VegaColors.Error,
                fontSize = 11.sp,
            )
        }
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { vm.connectWan() }, modifier = Modifier.weight(1f)) {
                Text("Conectar WAN", fontSize = 12.sp)
            }
            OutlinedButton(onClick = { vm.disconnectWan() }, modifier = Modifier.weight(1f)) {
                Text("Sair", fontSize = 12.sp)
            }
        }
        Spacer(Modifier.height(16.dp))

        SectionTitle("Voz")
        ToggleRow(
            label = "Ler respostas em voz alta",
            sub = "Usa o TTS do Core (GET /api/tts). Só funciona com LAN ativa.",
            checked = ttsEnabled,
            onCheckedChange = { vm.toggleTts() },
        )
        Text(
            "Comando por voz usa o reconhecedor nativo do Android (pt-BR).",
            color = VegaColors.TextMuted,
            fontSize = 11.sp,
        )
        Spacer(Modifier.height(16.dp))

        SectionTitle("Sobre")
        StatusRow("Versão", BuildConfig.VERSION_NAME)
        StatusRow("Cliente", "VEGA Mobile (Android)")
        StatusRow("Conexões", "LAN (HTTP+SSE) · WAN (WebSocket via relé)")
        Spacer(Modifier.height(8.dp))
        Text(
            "O token de pareamento fica apenas neste dispositivo e nunca é enviado por URL.",
            color = VegaColors.TextMuted,
            fontSize = 11.sp,
        )
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun SectionTitle(title: String) {
    Text(title, color = VegaColors.Primary, fontSize = 13.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(bottom = 6.dp))
}

private fun wanDetailSuffix(status: WanStatus, vm: ChatViewModel): String = when (status) {
    WanStatus.CONNECTED, WanStatus.CONNECTING, WanStatus.RECONNECTING, WanStatus.ERROR ->
        if (vm.wan.detail.isNotBlank()) " — ${vm.wan.detail}" else ""
    else -> ""
}

@Composable
private fun StatusRow(label: String, value: String) {
    Row(modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(label, modifier = Modifier.weight(0.4f), color = VegaColors.TextMuted, fontSize = 12.sp)
        Text(value, modifier = Modifier.weight(0.6f), color = VegaColors.TextSecondary, fontSize = 12.sp, textAlign = androidx.compose.ui.text.style.TextAlign.End)
    }
}

@Composable
private fun ToggleRow(
    label: String,
    sub: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(VegaColors.Surface)
            .border(1.dp, VegaColors.Border, RoundedCornerShape(12.dp))
            .padding(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(label, color = VegaColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(2.dp))
                Text(sub, color = VegaColors.TextMuted, fontSize = 11.sp)
            }
            androidx.compose.material3.Switch(
                checked = checked,
                onCheckedChange = onCheckedChange,
                modifier = Modifier.padding(start = 8.dp),
            )
        }
    }
}