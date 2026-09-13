package app.vega.client.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
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
import app.vega.client.model.SessionInfo
import app.vega.client.ui.theme.VegaColors
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Central (Fase 27): consolida conversas (histórico) e operações do Core em
 * um único destino de gestão do Personal Assistant.
 */
@Composable
fun CentralScreen(
    vm: ChatViewModel,
    onOpenSession: (String) -> Unit,
    onNavigateAssistant: () -> Unit,
) {
    val sessions by vm.sessions.collectAsState()
    val ops by vm.ops.collectAsState()
    val connection by vm.connection.collectAsState()
    val wsDetail by vm.wsDetail.collectAsState()
    val heartbeats by vm.heartbeats.collectAsState()
    LaunchedEffect(Unit) {
        vm.loadSessions()
        vm.refreshOps()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(VegaColors.Background)
            .padding(horizontal = 12.dp),
    ) {
        Spacer(Modifier.height(14.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Central",
                modifier = Modifier.weight(1f),
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                color = VegaColors.TextPrimary,
            )
            OutlinedButton(onClick = onNavigateAssistant) {
                Text("Nova conversa", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }
        }
        Spacer(Modifier.height(10.dp))

        LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                CentralCard("Conversas") {
                    if (sessions.isEmpty()) {
                        Text("Sem conversas ainda. Envie uma mensagem no Assistente.", color = VegaColors.TextMuted, fontSize = 12.sp)
                    } else {
                        sessions.forEach { s -> SessionRow(s, onClick = { onOpenSession(s.id) }) }
                    }
                }
            }
            item {
                CentralCard("Operações") {
                    InfoRow("Conexão", connection.label, color = if (connection == ConnectionState.ONLINE) VegaColors.Success else VegaColors.TextSecondary)
                    InfoRow("Transporte", wsDetail.ifBlank { "—" })
                    InfoRow("Heartbeats (LAN)", heartbeats.toString())
                    val j = ops
                    if (j == null) {
                        Text(
                            "Painel indisponível (Core OFFLINE).",
                            color = VegaColors.TextMuted,
                            fontSize = 12.sp,
                            textAlign = TextAlign.Center,
                        )
                    } else {
                        for ((key, label) in OPS_LABELS) {
                            if (!j.has(key)) continue
                            InfoRow(label, j.opt(key)?.toString() ?: "—")
                        }
                    }
                }
            }
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    MiniOrb(presence = orbFor(connection), size = 14.dp)
                    Spacer(Modifier.width(6.dp))
                    Text(
                        "Modelo e métricas refletem o que o Core publica em /api/ops/overview.",
                        color = VegaColors.TextMuted,
                        fontSize = 11.sp,
                    )
                }
                Spacer(Modifier.height(12.dp))
            }
        }
    }
}

@Composable
private fun CentralCard(title: String, content: @Composable () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(VegaColors.Surface)
            .border(1.dp, VegaColors.Border, RoundedCornerShape(12.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
    ) {
        Text(title, color = VegaColors.Primary, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(6.dp))
        content()
    }
}

@Composable
private fun SessionRow(session: SessionInfo, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(VegaColors.Surface2)
            .border(1.dp, VegaColors.Border, RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(session.title.ifBlank { session.id }, color = VegaColors.TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
            Spacer(Modifier.height(2.dp))
            Text(
                "${formatDate(session.updatedAt)}  ·  ${session.messageCount} mensagem(ns)",
                color = VegaColors.TextMuted,
                fontSize = 11.sp,
            )
        }
        Text("›", color = VegaColors.Primary, fontSize = 18.sp)
    }
}

@Composable
private fun InfoRow(label: String, value: String, color: androidx.compose.ui.graphics.Color? = null) {
    Row(modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(label, modifier = Modifier.weight(0.42f), color = VegaColors.TextMuted, fontSize = 12.sp)
        Text(
            value,
            modifier = Modifier.weight(0.58f),
            color = color ?: VegaColors.TextSecondary,
            fontSize = 12.sp,
            textAlign = TextAlign.End,
        )
    }
}

private fun orbFor(connection: ConnectionState): String = when (connection) {
    ConnectionState.ONLINE -> "success"
    ConnectionState.ERROR -> "error"
    ConnectionState.RECONNECTING, ConnectionState.CONNECTING -> "recovering"
    ConnectionState.INITIALIZING -> "idle"
    ConnectionState.OFFLINE -> "offline"
}

private fun formatDate(epochMs: Long): String =
    SimpleDateFormat("dd/MM/yyyy HH:mm", Locale.getDefault()).format(Date(epochMs))

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