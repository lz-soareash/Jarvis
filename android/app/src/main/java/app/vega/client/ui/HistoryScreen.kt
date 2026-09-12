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
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.vega.client.ChatViewModel
import app.vega.client.model.SessionInfo
import app.vega.client.ui.theme.VegaColors
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun HistoryScreen(vm: ChatViewModel, onOpenSession: (String) -> Unit) {
    val sessions by vm.sessions.collectAsState()
    LaunchedEffect(Unit) { vm.loadSessions() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(VegaColors.Background)
            .padding(horizontal = 12.dp),
    ) {
        Spacer(Modifier.height(14.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Histórico",
                modifier = Modifier.weight(1f),
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                color = VegaColors.TextPrimary,
            )
            OutlinedButton(onClick = { vm.newConversation() }) {
                Text("Nova conversa", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }
        }
        Spacer(Modifier.height(10.dp))
        if (sessions.isEmpty()) {
            Column(
                modifier = Modifier.weight(1f).fillMaxWidth(),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("Sem conversas ainda", color = VegaColors.TextSecondary, fontSize = 14.sp)
                Spacer(Modifier.height(4.dp))
                Text("Envie uma mensagem no Chat para começar.", color = VegaColors.TextMuted, fontSize = 12.sp)
            }
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(sessions, key = { it.id }) { s ->
                    SessionRow(s, onClick = { onOpenSession(s.id) })
                }
            }
        }
        Spacer(Modifier.height(12.dp))
    }
}

@Composable
private fun SessionRow(session: SessionInfo, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(VegaColors.Surface)
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

private fun formatDate(epochMs: Long): String =
    SimpleDateFormat("dd/MM/yyyy HH:mm", Locale.getDefault()).format(Date(epochMs))