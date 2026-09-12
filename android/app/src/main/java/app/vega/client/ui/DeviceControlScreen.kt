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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
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
import app.vega.client.ChatViewModel
import app.vega.client.model.MobileCapabilities
import app.vega.client.model.MobileCapability
import app.vega.client.ui.theme.VegaColors

@Composable
fun DeviceControlScreen(vm: ChatViewModel) {
    val accessibilityEnabled by vm.accessibilityEnabled.collectAsState()
    val results by vm.mobileResults.collectAsState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(VegaColors.Background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 12.dp),
    ) {
        Spacer(Modifier.height(14.dp))
        Text("VEGA Device Control", fontSize = 20.sp, fontWeight = FontWeight.Bold, color = VegaColors.TextPrimary)
        Text(
            "Comandos de dispositivo despachados pelo Core (Fase 25). O executor local aplica allowlist e permissões do sistema.",
            color = VegaColors.TextMuted,
            fontSize = 11.sp,
        )
        Spacer(Modifier.height(12.dp))

        DeviceStatusCard(accessibilityEnabled, vm)
        Spacer(Modifier.height(14.dp))
        AllowlistCard()
        Spacer(Modifier.height(14.dp))
        CapabilitiesCard()
        Spacer(Modifier.height(14.dp))
        ResultsCard(results)
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun DeviceStatusCard(accessibilityEnabled: Boolean, vm: ChatViewModel) {
    Card(
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            "Acessibilidade (scaffold)",
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            color = VegaColors.Primary,
        )
        Spacer(Modifier.height(4.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (accessibilityEnabled) "Serviço ATIVO" else "Serviço inativo",
                modifier = Modifier.weight(1f),
                color = if (accessibilityEnabled) VegaColors.Success else VegaColors.Off,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
            )
            Button(
                onClick = { vm.openAccessibilitySettings() },
                colors = ButtonDefaults.buttonColors(containerColor = VegaColors.Primary, contentColor = Color(0xFF04101D)),
            ) { Text("Configurar", fontSize = 12.sp, fontWeight = FontWeight.SemiBold) }
        }
        Spacer(Modifier.height(4.dp))
        Text(
            "A capability ACCESSIBILITY_CONTROL é declarada mas responde unsupported nesta fase. Nenhuma ação de tela é executada.",
            color = VegaColors.TextMuted,
            fontSize = 11.sp,
        )
    }
}

@Composable
private fun AllowlistCard() {
    Card(
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            "Allowlist OPEN_APP",
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            color = VegaColors.Primary,
        )
        Spacer(Modifier.height(4.dp))
        MobileCapabilities.DEFAULT_OPEN_APP_ALLOWLIST.sorted().forEach { pkg ->
            Text("• $pkg", color = VegaColors.TextSecondary, fontSize = 12.sp, fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace)
        }
        Spacer(Modifier.height(4.dp))
        Text(
            "Somente estes pacotes podem ser abertos via OPEN_APP. Pacotes fora da allowlist respondem denied.",
            color = VegaColors.TextMuted,
            fontSize = 11.sp,
        )
    }
}

@Composable
private fun CapabilitiesCard() {
    Card(
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            "Capabilities (registry)",
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            color = VegaColors.Primary,
        )
        Spacer(Modifier.height(6.dp))
        MobileCapabilities.ALL.forEach { cap -> CapabilityRow(cap) }
    }
}

@Composable
private fun CapabilityRow(cap: MobileCapability) {
    Row(modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(cap.name, color = VegaColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Text(cap.description, color = VegaColors.TextMuted, fontSize = 11.sp)
            val gates = mutableListOf<String>()
            if (cap.needsSystemPermission != null) gates.add("perm: ${cap.needsSystemPermission.substringAfterLast('.')}")
            if (gates.isNotEmpty()) {
                Spacer(Modifier.height(2.dp))
                Text(gates.joinToString(" · "), color = VegaColors.TextMuted, fontSize = 10.sp, fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace)
            }
        }
        Text(
            if (cap.executableOnMobile) "executável" else "unsupported",
            color = if (cap.executableOnMobile) VegaColors.Success else VegaColors.Warning,
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

@Composable
private fun ResultsCard(results: List<app.vega.client.model.MobileResultUi>) {
    Card(
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            "Últimos comandos",
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            color = VegaColors.Primary,
        )
        Spacer(Modifier.height(6.dp))
        if (results.isEmpty()) {
            Text("Nenhum comando executado ainda.", color = VegaColors.TextMuted, fontSize = 11.sp)
        } else {
            results.forEach { r ->
                Row(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("${r.capability} · ${r.commandId.take(8)}", color = VegaColors.TextSecondary, fontSize = 12.sp, fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace)
                        if (!r.error.isNullOrBlank()) {
                            Text(r.error, color = VegaColors.Error, fontSize = 11.sp)
                        }
                    }
                    Text(
                        r.status,
                        color = resultColor(r.status),
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
        }
    }
}

private fun resultColor(status: String): Color = when (status) {
    "success" -> VegaColors.Success
    "denied", "timeout", "cancelled", "failed" -> VegaColors.Error
    "unsupported" -> VegaColors.Warning
    "pending", "running" -> VegaColors.Secondary
    else -> VegaColors.TextMuted
}

@Composable
private fun Card(
    modifier: Modifier = Modifier,
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit,
) {
    androidx.compose.foundation.layout.Column(
        modifier = modifier
            .clip(RoundedCornerShape(14.dp))
            .background(VegaColors.Surface)
            .border(1.dp, VegaColors.Border, RoundedCornerShape(14.dp))
            .padding(14.dp),
    ) {
        content()
    }
}