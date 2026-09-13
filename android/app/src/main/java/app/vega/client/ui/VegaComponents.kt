package app.vega.client.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.vega.client.model.ApprovalInfo
import app.vega.client.model.MobileCommandCard
import app.vega.client.model.ToolItem
import app.vega.client.ui.theme.VegaColors
import app.vega.client.ui.theme.VegaPresenceColors
import app.vega.client.ui.theme.VegaSpacing

@Composable
fun MiniOrb(presence: String, size: Dp, modifier: Modifier = Modifier) {
    val baseColor = VegaPresenceColors.color(presence)
    val infinite = rememberInfiniteTransition(label = "orb")
    val angle by infinite.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(1600, easing = LinearEasing), RepeatMode.Restart),
        label = "angle",
    )
    val coreAlpha by infinite.animateFloat(
        initialValue = 0.7f,
        targetValue = 0.5f,
        animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse),
        label = "alpha",
    )
    Canvas(modifier = modifier.size(size)) {
        val r = size.toPx() / 2f
        val strokePx = size.toPx() * 0.05f
        drawArc(
            color = baseColor.copy(alpha = 0.9f),
            startAngle = angle,
            sweepAngle = 110f,
            useCenter = false,
            style = Stroke(width = strokePx, cap = StrokeCap.Round),
        )
        drawArc(
            color = baseColor.copy(alpha = 0.5f),
            startAngle = angle + 130f,
            sweepAngle = 110f,
            useCenter = false,
            style = Stroke(width = strokePx, cap = StrokeCap.Round),
        )
        drawArc(
            color = baseColor.copy(alpha = 0.3f),
            startAngle = angle + 250f,
            sweepAngle = 110f,
            useCenter = false,
            style = Stroke(width = strokePx, cap = StrokeCap.Round),
        )
        drawCircle(color = baseColor.copy(alpha = coreAlpha), radius = r * 0.16f)
    }
}

@Composable
fun PresenceChip(presence: String, modifier: Modifier = Modifier) {
    val color = VegaPresenceColors.color(presence)
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(50))
            .background(color.copy(alpha = 0.10f))
            .border(1.dp, color.copy(alpha = 0.45f), RoundedCornerShape(50))
            .padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(color))
        Text(
            if (presence == "idle") "ociosa" else app.vega.client.model.VegaPresence.label(presence),
            color = color,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

@Composable
fun TypingDots(active: Boolean, modifier: Modifier = Modifier) {
    val infinite = rememberInfiniteTransition(label = "dots")
    val alpha by infinite.animateFloat(
        initialValue = 0.2f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(700), RepeatMode.Reverse),
        label = "dota",
    )
    Row(modifier = modifier, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        for (i in 0 until 3) {
            Box(
                Modifier
                    .size(6.dp)
                    .clip(CircleShape)
                    .background(VegaColors.TextMuted.copy(alpha = alpha)),
            )
        }
    }
}

@Composable
fun ToolFeed(tools: List<ToolItem>, modifier: Modifier = Modifier) {
    if (tools.isEmpty()) return
    Row(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        for (tool in tools) {
            val glyph = when {
                tool.running -> "●"
                tool.ok == true -> "✓"
                tool.ok == false -> "✗"
                else -> "·"
            }
            val color = when {
                tool.running -> VegaColors.Primary
                tool.ok == true -> VegaColors.Success
                tool.ok == false -> VegaColors.Error
                else -> VegaColors.TextMuted
            }
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(50))
                    .background(VegaColors.Surface2)
                    .border(1.dp, VegaColors.Border, RoundedCornerShape(50))
                    .padding(horizontal = 8.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(glyph, color = color, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                Spacer(Modifier.size(5.dp))
                Text(tool.name, color = color, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            }
        }
    }
}

@Composable
fun ApprovalCard(
    approval: ApprovalInfo,
    onApprove: () -> Unit,
    onDeny: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val warn = approval.risk == "high" || approval.permissionLevel >= 3
    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(VegaColors.Surface2)
            .border(1.dp, if (warn) VegaColors.Warning.copy(alpha = 0.7f) else VegaColors.BorderStrong, RoundedCornerShape(14.dp))
            .padding(14.dp),
    ) {
        Text("JARVIS solicita autorização", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = if (warn) VegaColors.Warning else VegaColors.TextPrimary)
        Spacer(Modifier.height(4.dp))
        Text("Ferramenta: ${approval.toolName}", fontSize = 13.sp, color = VegaColors.TextSecondary, fontFamily = FontFamily.Monospace)
        Text("Risco: ${approval.risk.ifBlank { "desconhecido" }}  ·  Nível: ${approval.permissionLevel}", fontSize = 12.sp, color = VegaColors.TextMuted)
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = onApprove,
                colors = ButtonDefaults.buttonColors(
                    containerColor = VegaColors.Success,
                    contentColor = Color(0xFF04180F),
                ),
                modifier = Modifier.weight(1f),
            ) { Text("Aprovar") }
            OutlinedButton(onClick = onDeny, modifier = Modifier.weight(1f)) { Text("Negar") }
        }
    }
}

@Composable
fun ErrorBanner(
    message: String,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(VegaColors.Error.copy(alpha = 0.12f))
            .border(1.dp, VegaColors.Error.copy(alpha = 0.5f), RoundedCornerShape(12.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("⚠", color = VegaColors.Error, fontSize = 13.sp)
        Spacer(Modifier.size(8.dp))
        Text(
            message,
            modifier = Modifier.weight(1f),
            color = VegaColors.Error,
            fontSize = 12.sp,
        )
        Text(
            "ok",
            modifier = Modifier
                .clip(CircleShape)
                .clickable(onClick = onDismiss)
                .padding(6.dp),
            color = VegaColors.TextMuted,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

@Composable
fun MobileCommandCard(card: MobileCommandCard, modifier: Modifier = Modifier) {
    val statusColor = when (card.status) {
        "success" -> VegaColors.Success
        "failed", "unsupported" -> VegaColors.Error
        "denied", "timeout", "cancelled" -> VegaColors.Warning
        else -> VegaColors.TextMuted
    }
    val meta = buildList {
        card.device?.takeIf { it.isNotBlank() }?.let { add(it) }
        card.transport?.takeIf { it.isNotBlank() }?.let { add(it) }
    }.joinToString(" · ")
    val detail = (card.summary?.takeIf { it.isNotBlank() } ?: card.error)?.let { it }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(VegaSpacing.md))
            .background(VegaColors.Surface2)
            .border(1.dp, VegaColors.Border, RoundedCornerShape(VegaSpacing.md))
            .padding(VegaSpacing.md)
            .semantics { contentDescription = card.a11yDescription() },
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                card.title,
                color = VegaColors.TextPrimary,
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(VegaSpacing.sm))
            Text(
                card.status.uppercase(),
                color = statusColor,
                fontSize = 10.sp,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.SemiBold,
            )
        }
        if (meta.isNotEmpty()) {
            Spacer(Modifier.height(VegaSpacing.xs))
            Text(meta, color = VegaColors.TextMuted, fontSize = 11.sp)
        }
        if (detail != null) {
            Spacer(Modifier.height(VegaSpacing.sm))
            Text(detail, color = if (card.ok) VegaColors.TextSecondary else statusColor, fontSize = 12.sp)
        }
        if (card.result != null) {
            val keys = card.result.keys().asSequence().toList().take(3)
            if (keys.isNotEmpty()) {
                Spacer(Modifier.height(VegaSpacing.sm))
                Column(verticalArrangement = Arrangement.spacedBy(VegaSpacing.xs)) {
                    for (k in keys) {
                        Text(
                            "$k: ${card.result.optString(k)}",
                            color = VegaColors.TextMuted,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                        )
                    }
                }
            }
        }
    }
}