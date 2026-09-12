package app.vega.client.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

object VegaColors {
    val Background = Color(0xFF05080F)
    val BackgroundElev = Color(0xFF07101D)
    val Surface = Color(0xFF0B1524)
    val Surface2 = Color(0xFF101D31)
    val Border = Color(0xFF1B2A41)
    val BorderStrong = Color(0xFF2C4363)
    val TextPrimary = Color(0xFFE8F4FF)
    val TextSecondary = Color(0xFF9FB6D9)
    val TextMuted = Color(0xFF72889F)
    val Primary = Color(0xFF2FD0FF)
    val PrimaryHover = Color(0xFF63E0FF)
    val PrimaryActive = Color(0xFF18A9E6)
    val Secondary = Color(0xFF4D8DFF)
    val Success = Color(0xFF2FE6A5)
    val Warning = Color(0xFFFFC25E)
    val Error = Color(0xFFFF7A8F)
    val Off = Color(0xFF55637A)
    val Arc = Color(0xFF7CE8FF)

    val BrandStart = Color(0xFF7CE8FF)
    val BrandMid = Color(0xFF3AA8FF)
    val BrandEnd = Color(0xFF2F6BFF)
}

object VegaPresenceColors {
    fun color(presence: String): Color = when (presence.lowercase()) {
        "thinking", "perceiving", "planning", "observing", "verifying", "recovering" -> VegaColors.Primary
        "working", "executing" -> VegaColors.Secondary
        "listening" -> VegaColors.Secondary
        "speaking" -> VegaColors.Success
        "waiting_confirmation" -> VegaColors.Warning
        "success" -> VegaColors.Success
        "warning" -> Color(0xFFFFB454)
        "error" -> VegaColors.Error
        "offline" -> VegaColors.Off
        else -> VegaColors.Secondary
    }
}

private val VegaColorScheme = darkColorScheme(
    primary = VegaColors.Primary,
    onPrimary = Color(0xFF04101D),
    secondary = VegaColors.Secondary,
    onSecondary = Color(0xFF04101D),
    background = VegaColors.Background,
    onBackground = VegaColors.TextPrimary,
    surface = VegaColors.Surface,
    onSurface = VegaColors.TextPrimary,
    surfaceVariant = VegaColors.Surface2,
    onSurfaceVariant = VegaColors.TextSecondary,
    error = VegaColors.Error,
    onError = Color(0xFF2A0A10),
    outline = VegaColors.Border,
)

private val VegaTypography = Typography(
    displayLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Bold, fontSize = 40.sp),
    headlineLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Bold, fontSize = 28.sp),
    headlineMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 22.sp),
    titleLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 18.sp),
    titleMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 15.sp),
    bodyLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 15.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 13.sp, lineHeight = 19.sp),
    labelMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Medium, fontSize = 12.sp),
    labelSmall = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 11.sp),
)

@Composable
fun VegaTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = VegaColorScheme, typography = VegaTypography, content = content)
}