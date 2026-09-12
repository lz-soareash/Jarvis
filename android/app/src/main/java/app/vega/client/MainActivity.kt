package app.vega.client

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import app.vega.client.ui.ChatScreen
import app.vega.client.ui.DeviceControlScreen
import app.vega.client.ui.HistoryScreen
import app.vega.client.ui.OpsScreen
import app.vega.client.ui.SettingsScreen
import app.vega.client.ui.theme.VegaColors
import app.vega.client.ui.theme.VegaTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            VegaTheme {
                AppRoot()
            }
        }
    }
}

@Composable
private fun AppRoot() {
    val vm: ChatViewModel = viewModel()
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val tabs = listOf(
        "◉" to "Chat",
        "≣" to "Histórico",
        "◈" to "Operações",
        "◆" to "Dispositivo",
        "⚙" to "Config",
    )
    Scaffold(
        containerColor = VegaColors.Background,
        bottomBar = {
            NavigationBar(containerColor = VegaColors.Surface) {
                tabs.forEachIndexed { index, (glyph, label) ->
                    NavigationBarItem(
                        selected = tab == index,
                        onClick = { tab = index },
                        icon = {
                            Text(glyph, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                        },
                        label = { Text(label, fontSize = 11.sp) },
                    )
                }
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
when (tab) {
            0 -> ChatScreen(vm)
            1 -> HistoryScreen(vm, onOpenSession = {
                vm.openSession(it)
                tab = 0
            })
            2 -> OpsScreen(vm)
            3 -> DeviceControlScreen(vm)
            else -> SettingsScreen(vm)
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