package app.vega.client.ui

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.vega.client.ChatViewModel
import app.vega.client.model.ChatMessage
import app.vega.client.model.ConnectionState
import app.vega.client.model.MessageStatus
import app.vega.client.model.Role
import app.vega.client.ui.theme.VegaColors
import app.vega.client.ui.theme.VegaSpacing
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

@Composable
fun ChatScreen(vm: ChatViewModel) {
    val messages by vm.messages.collectAsState()
    val presence by vm.presence.collectAsState()
    val connection by vm.connection.collectAsState()
    val wsDetail by vm.wsDetail.collectAsState()
    val pendingApproval by vm.pendingApproval.collectAsState()
    val banner by vm.banner.collectAsState()
    val voiceState by vm.voiceState.collectAsState()

    var draft by rememberSaveable { mutableStateOf("") }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val streaming = messages.any { it.status == MessageStatus.STREAMING || it.status == MessageStatus.SENDING }
    val lastIndex = messages.lastIndex
    var autoFollow by remember { mutableStateOf(true) }

    LaunchedEffect(lastIndex) {
        if (autoFollow && lastIndex >= 0) listState.scrollToItem(lastIndex)
    }
    LaunchedEffect(Unit) {
        snapshotFlow { listState.firstVisibleItemIndex }
            .collectLatest { idx -> if (lastIndex > 2 && idx < lastIndex - 1) autoFollow = false }
    }
    val showJump by remember {
        derivedStateOf {
            val info = listState.layoutInfo
            val last = info.visibleItemsInfo.lastOrNull()?.index ?: 0
            last < lastIndex
        }
    }

    val micLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) vm.startVoice() else vm.micDenied()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(VegaColors.Background)
            .padding(horizontal = 12.dp)
            .imePadding(),
    ) {
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    MiniOrb(presence = presence, size = 26.dp)
                    Spacer(Modifier.width(8.dp))
                    Text(
                        "VEGA",
                        fontSize = 22.sp,
                        fontWeight = FontWeight.ExtraBold,
                        color = VegaColors.Arc,
                    )
                }
                Text(wsDetail, color = VegaColors.TextMuted, fontSize = 11.sp)
            }
            PresenceChip(presence)
        }
        Spacer(Modifier.height(10.dp))

        AnimatedVisibility(banner != null) {
            banner?.let { b ->
                ErrorBanner(b, onDismiss = { vm.clearBanner() })
                Spacer(Modifier.height(8.dp))
            }
        }

        if (messages.isEmpty()) {
            EmptyChat(
                modifier = Modifier.weight(1f).fillMaxWidth(),
                offline = connection == ConnectionState.OFFLINE || connection == ConnectionState.ERROR,
                onQuickAction = { vm.send(it) },
            )
            Spacer(Modifier.height(8.dp))
        } else {
            Box(Modifier.weight(1f)) {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(messages, key = { it.id }) { msg ->
                        MessageBubble(msg)
                    }
                    item {
                        if (pendingApproval != null) {
                            pendingApproval?.let { a ->
                                ApprovalCard(
                                    approval = a,
                                    onApprove = { vm.respondApproval(true) },
                                    onDeny = { vm.respondApproval(false) },
                                )
                            }
                            Spacer(Modifier.height(4.dp))
                        }
                    }
                }
                if (showJump) {
                    Text(
                        "↓ nova resposta",
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .padding(bottom = 8.dp)
                            .clip(CircleShape)
                            .background(VegaColors.Surface2.copy(alpha = 0.95f))
                            .border(1.dp, VegaColors.BorderStrong, CircleShape)
                            .clickable {
                                autoFollow = true
                                if (lastIndex >= 0) scope.launch { listState.animateScrollToItem(lastIndex) }
                            }
                            .padding(horizontal = 14.dp, vertical = 8.dp),
                        color = VegaColors.Primary,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            if (streaming) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TypingDots(active = true)
                    Spacer(Modifier.width(8.dp))
                    Text("JARVIS está ${presenceLabel(presence)}…", color = VegaColors.TextMuted, fontSize = 12.sp)
                }
                Spacer(Modifier.height(4.dp))
            }
        }

        if (voiceState == "listening") {
            Text(
                "ouvindo… fale agora",
                color = VegaColors.Primary,
                fontSize = 12.sp,
                modifier = Modifier.padding(vertical = 2.dp),
            )
        } else if (voiceState == "processing") {
            Text("processando áudio…", color = VegaColors.TextMuted, fontSize = 12.sp, modifier = Modifier.padding(vertical = 2.dp))
        }

        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = draft,
                onValueChange = { draft = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Fale com a JARVIS…", color = VegaColors.TextMuted, fontSize = 14.sp) },
                maxLines = 4,
                textStyle = androidx.compose.ui.text.TextStyle(color = VegaColors.TextPrimary, fontSize = 14.sp),
                shape = RoundedCornerShape(14.dp),
            )
            OutlinedButton(onClick = {
                if (voiceState == "listening") vm.stopVoice() else micLauncher.launch(Manifest.permission.RECORD_AUDIO)
            }) {
                Text(if (voiceState == "listening") "Parar" else "Voz", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }
            Button(
                onClick = {
                    val t = draft
                    if (t.isNotBlank()) {
                        draft = ""
                        vm.send(t)
                    }
                },
                enabled = draft.isNotBlank() && !streaming && connection != ConnectionState.OFFLINE,
                colors = ButtonDefaults.buttonColors(
                    containerColor = VegaColors.Primary,
                    contentColor = Color(0xFF04101D),
                ),
                shape = RoundedCornerShape(14.dp),
            ) {
                Text("Enviar", fontSize = 13.sp, fontWeight = FontWeight.Bold)
            }
        }
        Spacer(Modifier.height(10.dp))
    }
}

@Composable
private fun MessageBubble(msg: ChatMessage) {
    val isUser = msg.role == Role.USER
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Column(
            modifier = Modifier.fillMaxWidth(if (isUser) 0.82f else 1f),
            horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
        ) {
            if (!isUser && msg.tools.isNotEmpty()) {
                ToolFeed(msg.tools)
                Spacer(Modifier.height(6.dp))
            }
            Column(
                modifier = Modifier
                    .clip(RoundedCornerShape(if (isUser) 14.dp else 12.dp))
                    .background(if (isUser) VegaColors.Primary.copy(alpha = 0.14f) else VegaColors.Surface)
                    .border(1.dp, if (isUser) VegaColors.Primary.copy(alpha = 0.35f) else VegaColors.Border, RoundedCornerShape(if (isUser) 14.dp else 12.dp))
                    .padding(horizontal = 12.dp, vertical = 9.dp),
            ) {
                when {
                    msg.status == MessageStatus.ERROR -> {
                        Text(msg.error ?: "erro", color = VegaColors.Error, fontSize = 13.sp)
                        if (msg.content.isNotBlank()) {
                            Spacer(Modifier.height(6.dp))
                            MarkdownContent(MarkdownParser.parse(msg.content))
                        }
                    }
                    msg.status == MessageStatus.SENDING && msg.content.isBlank() -> {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            TypingDots(active = true)
                            Spacer(Modifier.width(8.dp))
                            Text("enviando…", color = VegaColors.TextMuted, fontSize = 12.sp)
                        }
                    }
                    msg.content.isBlank() -> {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            TypingDots(active = true)
                            Spacer(Modifier.width(8.dp))
                            Text("pensando…", color = VegaColors.TextMuted, fontSize = 12.sp)
                        }
                    }
                    else -> {
                        if (isUser) {
                            Text(msg.content, color = VegaColors.TextPrimary, fontSize = 14.sp)
                        } else {
                            MarkdownContent(MarkdownParser.parse(msg.content))
                        }
                    }
                }
            }
            if (msg.mobileCard != null) {
                Spacer(Modifier.height(VegaSpacing.sm))
                MobileCommandCard(msg.mobileCard!!)
            }
        }
    }
}

@Composable
private fun EmptyChat(
    modifier: Modifier,
    offline: Boolean,
    onQuickAction: (String) -> Unit,
) {
    val quickActions = listOf(
        "Bateria" to "Quanto está a bateria do meu celular?",
        "Dispositivo" to "Quais as informações do meu celular?",
        "Rede" to "Como está a rede do meu celular?",
    )
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        MiniOrb(presence = "idle", size = 46.dp)
        Spacer(Modifier.height(12.dp))
        Text(
            if (offline) "Você ainda não está conectado ao Core" else "Pergunte algo para a JARVIS",
            color = VegaColors.TextSecondary,
            fontSize = 14.sp,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            if (offline) "Abra Configurações e conecte o Core (LAN) ou o WAN." else "Ex.: \"qual é o status da memória?\"",
            color = VegaColors.TextMuted,
            fontSize = 12.sp,
            textAlign = TextAlign.Center,
        )
        if (!offline) {
            Spacer(Modifier.height(VegaSpacing.lg))
            Row(horizontalArrangement = Arrangement.spacedBy(VegaSpacing.sm)) {
                for ((label, prompt) in quickActions) {
                    Text(
                        label,
                        modifier = Modifier
                            .clip(RoundedCornerShape(50))
                            .background(VegaColors.Surface2)
                            .border(1.dp, VegaColors.BorderStrong, RoundedCornerShape(50))
                            .clickable { onQuickAction(prompt) }
                            .padding(horizontal = VegaSpacing.md, vertical = VegaSpacing.sm),
                        color = VegaColors.Primary,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
        }
    }
}

private fun presenceLabel(p: String): String = app.vega.client.model.VegaPresence.label(p)