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
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.vega.client.ChatViewModel
import app.vega.client.model.ConnectionState
import app.vega.client.ui.theme.VegaColors
import org.json.JSONObject

@Composable
fun OpsScreen(vm: ChatViewModel) {
    val ops by vm.ops.collectAsState()
    val connection by vm.connection.collectAsState()
    val wsDetail by vm.wsDetail.collectAsState()
    val heartbeats by vm.heartbeats.collectAsState()
    LaunchedEffect(Unit) { vm.refreshOps() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(VegaColors.Background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 12.dp),
    ) {
        Spacer(Modifier.height(14.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Operações",
                modifier = Modifier.weight(1f),
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                color = VegaColors.TextPrimary,
            )
            OutlinedButton(onClick = { vm.refreshOps() }) {
                Text("Atualizar", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }
        }
        Spacer(Modifier.height(10.dp))

        InfoCard("Conexão") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                MiniOrb(presence = orbFor(connection), size = 20.dp)
                Spacer(Modifier.width(8.dp))
                Text(
                    connection.label,
                    color = if (connection == ConnectionState.ONLINE) VegaColors.Success else VegaColors.TextSecondary,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            InfoRow("Transporte", wsDetail.ifBlank { "—" })
            InfoRow("Heartbeats (LAN)", heartbeats.toString())
        }

        val j = ops
        if (j == null) {
            Spacer(Modifier.height(10.dp))
            Text(
                "Painel de operações indisponível (Core OFFLINE ou rota correta).",
                color = VegaColors.TextMuted,
                fontSize = 12.sp,
                textAlign = TextAlign.Center,
            )
        } else {
            InfoCard("Visão geral do Core") {
                for ((key, label) in OPS_LABELS) {
                    if (!j.has(key)) continue
                    InfoRow(label, j.opt(key).toString())
                }
            }
            Spacer(Modifier.height(4.dp))
            Text(
                "Modelo e métricas refletem o que o Core publica em /api/ops/overview.",
                color = VegaColors.TextMuted,
                fontSize = 11.sp,
                textAlign = TextAlign.Center,
            )
        }
        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun InfoCard(title: String, content: @Composable () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(VegaColors.Surface)
            .border(1.dp, VegaColors.Border, RoundedCornerShape(12.dp))
            .padding(12.dp),
    ) {
        Text(title, color = VegaColors.TextMuted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(6.dp))
        content()
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(label, modifier = Modifier.weight(0.42f), color = VegaColors.TextMuted, fontSize = 12.sp)
        Text(value, modifier = Modifier.weight(0.58f), color = VegaColors.TextSecondary, fontSize = 12.sp, textAlign = TextAlign.End)
    }
}

private fun orbFor(connection: ConnectionState): String = when (connection) {
    ConnectionState.ONLINE -> "success"
    ConnectionState.ERROR -> "error"
    ConnectionState.RECONNECTING, ConnectionState.CONNECTING -> "recovering"
    ConnectionState.INITIALIZING -> "idle"
    ConnectionState.OFFLINE -> "offline"
}

private val OPS_LABELS = listOf(
    "model" to "Modelo IA",
    "level1" to "Nível 1",
    "level2" to "Nível 2",
    "latency" to "Latência Core",
    "latency_ms" to "Latência Core",
    "ai_latency_ms" to "Latência IA",
    "ai_status" to "Status IA",
    "sessions_active" to "Sessões ativas",
    "sessions_total" to "Sessões totais",
    "devices" to "Dispositivos",
    "devices_total" to "Dispositivos pareados",
    "memory_enabled" to "Memória",
    "memory_pct" to "Uso de memória",
    "freed_space_pct" to "Disco usado",
    "last_error" to "Último erro",
)