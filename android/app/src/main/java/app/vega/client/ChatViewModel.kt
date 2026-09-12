package app.vega.client

import android.app.Application
import android.content.SharedPreferences
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.vega.client.model.ApprovalInfo
import app.vega.client.model.ChatMessage
import app.vega.client.model.ConnectionState
import app.vega.client.model.MessageStatus
import app.vega.client.model.Role
import app.vega.client.model.SessionInfo
import app.vega.client.model.ToolItem
import app.vega.client.model.TurnEvent
import app.vega.client.model.VegaPresence
import app.vega.client.model.MobileCommand
import app.vega.client.model.MobileCapabilities
import app.vega.client.model.MobileResultUi
import app.vega.client.mobile.AndroidMobileOps
import app.vega.client.mobile.MobileCommandExecutor
import app.vega.client.net.ApiException
import app.vega.client.net.VegaHttp
import app.vega.client.voice.SpeechIn
import app.vega.client.voice.TtsPlayer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

class ChatViewModel(application: Application) : AndroidViewModel(application) {

    private val app = application
    private val prefs: SharedPreferences = app.getSharedPreferences("vega_core", android.content.Context.MODE_PRIVATE)

    val bridge = VegaBridge(app, prefs.getString("core_url", VegaBridge.DEFAULT_CORE_URL) ?: VegaBridge.DEFAULT_CORE_URL)
    val wan = VegaWan(app, viewModelScope)
    private val http = VegaHttp()

    private val speech = SpeechIn(
        app,
        onResult = { text ->
            _voiceState.value = "idle"
            if (text.isNotBlank()) send(text)
        },
        onState = { s -> _voiceState.value = s },
    )
    private val tts = TtsPlayer()
    private val idCounter = AtomicLong(10_000)
    private var busySending = false
    private val requestToAssistant = ConcurrentHashMap<String, Long>()
    private val assistantToRequest = ConcurrentHashMap<Long, String>()

    private val _coreUrl = MutableStateFlow(bridge.coreUrl)
    val coreUrl: StateFlow<String> = _coreUrl.asStateFlow()

    private val _wanUrl = MutableStateFlow(prefs.getString("wan_url", "").orEmpty())
    val wanUrl: StateFlow<String> = _wanUrl.asStateFlow()

    private val _connection = MutableStateFlow(ConnectionState.OFFLINE)
    val connection: StateFlow<ConnectionState> = _connection.asStateFlow()

    private val _wsDetail = MutableStateFlow("")
    val wsDetail: StateFlow<String> = _wsDetail.asStateFlow()

    private val _heartbeats = MutableStateFlow(0)
    val heartbeats: StateFlow<Int> = _heartbeats.asStateFlow()

    private val _presence = MutableStateFlow("offline")
    val presence: StateFlow<String> = _presence.asStateFlow()

    private val _messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val messages: StateFlow<List<ChatMessage>> = _messages.asStateFlow()

    private val _conversationId = MutableStateFlow<String?>(null)
    val conversationId: StateFlow<String?> = _conversationId.asStateFlow()

    private val _sessionTitle = MutableStateFlow("")
    val sessionTitle: StateFlow<String> = _sessionTitle.asStateFlow()

    private val _sessions = MutableStateFlow<List<SessionInfo>>(emptyList())
    val sessions: StateFlow<List<SessionInfo>> = _sessions.asStateFlow()

    private val _pendingApproval = MutableStateFlow<ApprovalInfo?>(null)
    val pendingApproval: StateFlow<ApprovalInfo?> = _pendingApproval.asStateFlow()

    private val _banner = MutableStateFlow<String?>(null)
    val banner: StateFlow<String?> = _banner.asStateFlow()

    private val _voiceState = MutableStateFlow("idle")
    val voiceState: StateFlow<String> = _voiceState.asStateFlow()

    private val _ttsEnabled = MutableStateFlow(prefs.getBoolean("tts_enabled", false))
    val ttsEnabled: StateFlow<Boolean> = _ttsEnabled.asStateFlow()

    private val _ops = MutableStateFlow<JSONObject?>(null)
    val ops: StateFlow<JSONObject?> = _ops.asStateFlow()

    private val mobileOps = AndroidMobileOps(app)
    private val mobileExecutor = MobileCommandExecutor(mobileOps)
    private val _mobileResults = MutableStateFlow<List<MobileResultUi>>(emptyList())
    val mobileResults: StateFlow<List<MobileResultUi>> = _mobileResults.asStateFlow()
    private val _accessibilityEnabled = MutableStateFlow(false)
    val accessibilityEnabled: StateFlow<Boolean> = _accessibilityEnabled.asStateFlow()

    private var lastLocalPresenceAt = 0L

    init {
        wan.onTurnFrame = { type, payload, requestId -> handleWanFrame(type, payload, requestId) }
        wan.onMobileCommand = { payload -> handleMobileCommand(payload) }
        tts.onState = { s ->
            when (s) {
                "playing" -> setLocalPresence("speaking")
                "idle" -> { setLocalPresence("success"); postponeIdle(1200) }
                "error" -> setLocalPresence("idle")
            }
        }
        startPolling()
        if (bridge.hasToken) viewModelScope.launch { bridge.boot() }
    }

    private fun isLanOnline() = bridge.state == "connected"
    private fun isWanOnline() = wan.state == "connected"
    private fun isOnline() = isLanOnline() || isWanOnline()

    private fun startPolling() {
        viewModelScope.launch {
            while (true) {
                syncNetwork()
                pollPresence()
                delay(1000)
            }
        }
    }

    private fun syncNetwork() {
        val b = bridge.state
        val w = wan.state
        _connection.value = when {
            b == "connected" || w == "connected" -> ConnectionState.ONLINE
            b == "reconnecting" || w == "reconnecting" -> ConnectionState.RECONNECTING
            b in setOf("registering", "pairing", "connecting") || w == "connecting" -> ConnectionState.CONNECTING
            b == "error" || w in setOf("authentication_error", "core_unavailable") -> ConnectionState.ERROR
            else -> ConnectionState.OFFLINE
        }
        _wsDetail.value = when {
            b == "connected" -> "LAN ${bridge.detail}"
            w == "connected" -> "WAN (${wan.detail})"
            else -> if (b.isNotEmpty()) bridge.detail else wan.detail
        }
        _heartbeats.value = bridge.heartbeatsTotal
        if (bridge.conversationId != null && _conversationId.value == null) _conversationId.value = bridge.conversationId
        if (wan.conversationId != null && _conversationId.value == null) _conversationId.value = wan.conversationId
    }

    private fun pollPresence() {
        if (!isOnline()) {
            if (_presence.value != "offline") setLocalPresence("offline")
            return
        }
        if (System.currentTimeMillis() - lastLocalPresenceAt < 1500) return
        if (_presence.value in setOf(
                "thinking", "executing", "listening", "speaking", "waiting_confirmation",
                "verifying", "perceiving", "planning", "observing", "recovering",
            )
        ) return
        viewModelScope.launch {
            try {
                val j = withContext(Dispatchers.IO) { http.getJson(_coreUrl.value, "/api/vega/state") }
                _presence.value = VegaPresence.canonical(j.optString("state"))
            } catch (_: Exception) {
            }
        }
        refreshDeviceControl()
    }

    private fun refreshDeviceControl() {
        _accessibilityEnabled.value = VegaAccessibilityService.isEnabled(app)
    }

    fun openAccessibilitySettings() {
        try {
            val intent = android.content.Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS)
                .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
            app.startActivity(intent)
        } catch (e: Exception) {
            _banner.value = "não foi possível abrir as Configurações de acessibilidade"
        }
    }

    private fun handleMobileCommand(payload: JSONObject) {
        val commandId = payload.optString("command_id").ifBlank { return }
        val args = payload.optJSONObject("args") ?: JSONObject()
        val timeoutMs = payload.optInt("timeout_ms", 15_000)
        val command = MobileCommand(
            commandId = commandId,
            targetDeviceId = wan.deviceId,
            capability = payload.optString("capability"),
            args = args,
            timeoutMs = timeoutMs,
        )
        viewModelScope.launch {
            val result = mobileExecutor.execute(command)
            _mobileResults.update {
                (listOf(
                    MobileResultUi(command.capability, commandId, result.status, result.error)
                ) + it).take(10)
            }
            if (isWanOnline()) withContext(Dispatchers.IO) {
                try {
                    wan.sendCommandResult(
                        commandId = result.commandId,
                        status = result.status,
                        result = result.result,
                        error = result.error,
                        startedAt = result.startedAt,
                        finishedAt = result.finishedAt,
                    )
                } catch (e: Exception) {
                    Log.w("vega-chat", "falha ao enviar command_result: ${e.message}")
                }
            }
        }
    }

    private fun setLocalPresence(p: String) {
        lastLocalPresenceAt = System.currentTimeMillis()
        _presence.value = p
    }

    private fun postponeIdle(ms: Long) {
        viewModelScope.launch {
            delay(ms)
            if (_presence.value in setOf("success", "speaking")) setLocalPresence("idle")
        }
    }

    fun setCoreUrl(url: String) {
        _coreUrl.value = url.trim()
    }

    fun connect() {
        val url = _coreUrl.value
        if (url.isBlank()) {
            _banner.value = "Defina a URL do Core antes de conectar"
            return
        }
        prefs.edit().putString("core_url", url).apply()
        bridge.coreUrl = url
        setLocalPresence("connecting")
        viewModelScope.launch { bridge.boot() }
    }

    fun disconnectLan() {
        viewModelScope.launch { bridge.leave() }
    }

    fun setWanUrl(url: String) {
        _wanUrl.value = url.trim()
    }

    fun connectWan() {
        val url = _wanUrl.value
        if (url.isBlank()) {
            _banner.value = "Defina a URL do gateway WAN (ws:// ou wss://)"
            return
        }
        prefs.edit().putString("wan_url", url).apply()
        wan.configure(token = bridge.token, pairingCode = null, deviceId = bridge.deviceId)
        wan.connect(url)
    }

    fun disconnectWan() {
        wan.disconnect()
    }

    fun send(text: String) {
        val t = text.trim()
        if (t.isEmpty()) return
        if (busySending) return
        if (!isOnline()) {
            val status = _connection.value.label
            _banner.value = "JARVIS está $status — conecte ao Core primeiro"
            return
        }
        busySending = true
        val user = ChatMessage(idCounterAndIncrement(), Role.USER, t, MessageStatus.DONE)
        val assistant = ChatMessage(idCounterAndIncrement(), Role.ASSISTANT, "", MessageStatus.SENDING)
        _messages.update { it + user + assistant }
        viewModelScope.launch {
            try {
                if (isWanOnline()) runWanTurn(t, assistant.id) else runHttpTurn(t, assistant.id)
            } catch (e: Exception) {
                failMessage(assistant.id, friendlyError(e))
            } finally {
                busySending = false
            }
        }
    }

    private suspend fun runHttpTurn(content: String, assistantId: Long) {
        val token = bridge.token ?: throw IllegalStateException("bridge não autenticado")
        setLocalPresence("thinking")
        http.sendMessageSse(_coreUrl.value, token, _conversationId.value, content)
            .collect { ev -> handleEvent(ev, assistantId) }
        val msg = _messages.value.find { it.id == assistantId }
        if (msg != null && (msg.status == MessageStatus.SENDING || msg.status == MessageStatus.STREAMING)) {
            failMessage(assistantId, msg.error ?: "conexão encerrada sem conclusão")
        }
    }

    private suspend fun runWanTurn(content: String, assistantId: Long) {
        setLocalPresence("thinking")
        val requestId = withContext(Dispatchers.IO) {
            wan.sendMessage(content, JSONObject().put("stream", true))
        }
        assistantToRequest[assistantId] = requestId
        requestToAssistant[requestId] = assistantId
    }

    private fun handleEvent(ev: TurnEvent, assistantId: Long) {
        when (ev) {
            is TurnEvent.Chunk -> {
                updateMessage(assistantId) { it.copy(content = it.content + ev.text, status = MessageStatus.STREAMING, error = null) }
                if (_presence.value != "thinking") setLocalPresence("thinking")
            }
            is TurnEvent.ToolStart -> {
                updateMessage(assistantId) { msg ->
                    val keep = msg.tools.filterNot { t -> ev.names.contains(t.name) && t.running }
                    msg.copy(status = MessageStatus.STREAMING, tools = keep + ev.names.map { ToolItem(it) })
                }
                setLocalPresence("executing")
            }
            is TurnEvent.ToolDone -> {
                updateMessage(assistantId) { msg ->
                    msg.copy(
                        tools = msg.tools.map { t ->
                            if (t.name == ev.name) t.copy(ok = ev.ok, running = false) else t
                        },
                    )
                }
                setLocalPresence("thinking")
            }
            is TurnEvent.ApprovalRequest -> {
                _pendingApproval.value = ev.approval
                setLocalPresence("waiting_confirmation")
            }
            is TurnEvent.Done -> {
                updateMessage(assistantId) {
                    val finalContent = if (ev.content.isNotBlank()) ev.content else it.content
                    it.copy(
                        content = finalContent,
                        status = MessageStatus.DONE,
                        tools = it.tools.map { t -> t.copy(running = false) },
                    )
                }
                ev.sessionId?.let { _conversationId.value = it }
                assistantToRequest[assistantId]?.let { requestToAssistant.remove(it) }
                assistantToRequest.remove(assistantId)
                setLocalPresence("success")
                postponeIdle(1500)
                speakLatest(assistantId)
            }
            is TurnEvent.ErrorEvent -> {
                if (_messages.value.none { it.id == assistantId }) return
                updateMessage(assistantId) { m ->
                    m.copy(error = ev.detail.ifBlank { m.error })
                }
            }
            else -> Unit
        }
    }

    private fun handleWanFrame(type: String, payload: JSONObject, envelopeRequestId: String) {
        val requestId = payload.optString("request_id").ifBlank { envelopeRequestId }
        when (type) {
            "agent_event" -> {
                val inner = payload.optJSONObject("event") ?: return
                val ev = TurnEvent.parse(inner) ?: return
                val assistantId = requestToAssistant[requestId]
                if (assistantId == null) {
                    if (ev is TurnEvent.Done) appendPassiveDone(ev)
                    return
                }
                viewModelScope.launch { handleEvent(ev, assistantId) }
            }
            "message_result" -> {
                val assistantId = requestToAssistant[requestId] ?: return
                if (payload.optBoolean("ok", false)) {
                    requestToAssistant.remove(requestId)
                    assistantToRequest.remove(assistantId)
                } else {
                    requestToAssistant.remove(requestId)
                    assistantToRequest.remove(assistantId)
                    failMessage(assistantId, payload.optString("error").ifBlank { "Core falhou ao processar" })
                }
            }
        }
    }

    private fun appendPassiveDone(ev: TurnEvent.Done) {
        _messages.update {
            it + ChatMessage(
                idCounterAndIncrement(), Role.ASSISTANT, ev.content, MessageStatus.DONE,
            )
        }
    }

    private fun speakLatest(assistantId: Long) {
        if (!_ttsEnabled.value || !isLanOnline()) return
        val content = _messages.value.find { it.id == assistantId }?.content.orEmpty()
        if (content.isBlank()) return
        viewModelScope.launch {
            tts.play(http.ttsUrl(_coreUrl.value, content))
        }
    }

    fun respondApproval(approved: Boolean) {
        val a = _pendingApproval.value ?: return
        _pendingApproval.value = null
        setLocalPresence("verifying")
        viewModelScope.launch {
            try {
                if (isWanOnline()) withContext(Dispatchers.IO) { wan.sendApproval(a.id, approved) }
                else withContext(Dispatchers.IO) { http.respondApproval(_coreUrl.value, a.id, approved) }
            } catch (e: Exception) {
                _banner.value = "falha ao responder: ${friendlyError(e)}"
                setLocalPresence("idle")
            }
        }
    }

    fun startVoice() {
        if (!isOnline()) {
            _banner.value = "Voz da IA pronta, mas o Core está OFFLINE"
            return
        }
        speech.start()
    }

    fun stopVoice() {
        speech.cancel()
    }

    fun micDenied() {
        _banner.value = "Microfone sem permissão — conceda acesso nas Configurações"
    }

    fun toggleTts() {
        val next = !_ttsEnabled.value
        _ttsEnabled.value = next
        prefs.edit().putBoolean("tts_enabled", next).apply()
    }

    fun newConversation() {
        viewModelScope.launch {
            if (busySending) return@launch
            try {
                val s = withContext(Dispatchers.IO) { http.createSession(_coreUrl.value) }
                _conversationId.value = s.id
                _sessionTitle.value = s.title
                _messages.value = emptyList()
                loadSessions()
            } catch (e: Exception) {
                _banner.value = "não foi possível criar a conversa: ${friendlyError(e)}"
            }
        }
    }

    fun loadSessions() {
        viewModelScope.launch {
            try {
                _sessions.value = withContext(Dispatchers.IO) { http.listSessions(_coreUrl.value) }
            } catch (e: Exception) {
                _banner.value = "não foi possível carregar o histórico: ${friendlyError(e)}"
            }
        }
    }

    fun openSession(id: String) {
        viewModelScope.launch {
            try {
                val history = withContext(Dispatchers.IO) { http.fetchHistory(_coreUrl.value, id) }
                _conversationId.value = id
                _messages.value = history
                _sessionTitle.value = _sessions.value.find { it.id == id }?.title.orEmpty()
            } catch (e: Exception) {
                _banner.value = "não foi possível abrir a conversa: ${friendlyError(e)}"
            }
        }
    }

    fun refreshOps() {
        if (!isOnline()) {
            _ops.value = null
            return
        }
        viewModelScope.launch {
            try {
                _ops.value = withContext(Dispatchers.IO) { http.opsOverview(_coreUrl.value) }
            } catch (e: Exception) {
                _ops.value = null
                _banner.value = "Operações indisponíveis: ${friendlyError(e)}"
            }
        }
    }

    fun clearBanner() {
        _banner.value = null
    }

    fun stopTts() {
        tts.stop()
    }

    private fun updateMessage(id: Long, fn: (ChatMessage) -> ChatMessage) {
        _messages.update { list -> list.map { if (it.id == id) fn(it) else it } }
    }

    private fun failMessage(id: Long, error: String) {
        updateMessage(id) { m ->
            val keepContent = m.content.ifBlank { "" }
            m.copy(
                content = keepContent.takeIf { it.isNotBlank() } ?: "",
                status = MessageStatus.ERROR,
                error = error,
                tools = m.tools.map { t -> t.copy(running = false) },
            )
        }
        setLocalPresence("error")
        postponeIdle(2500)
    }

    private fun friendlyError(e: Exception): String = when (e) {
        is ApiException -> when (e.status) {
            401 -> "sessão expirada — reconecte ao Core"
            403 -> "sem permissão no Core"
            429 -> "muitas mensagens — aguarde um pouco"
            413 -> "mensagem muito grande"
            503 -> "Core indisponível (REMOTE/DEVICE Bridge desabilitado)"
            else -> "HTTP ${e.status}: ${e.message}"
        }
        is IllegalStateException -> e.message ?: "transporte indisponível"
        else -> "erro de rede: ${e.message ?: "sem detalhes"}"
    }

    private fun idCounterAndIncrement(): Long = idCounter.getAndIncrement()
}