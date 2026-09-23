package app.vega.client

import android.app.Application
import android.content.SharedPreferences
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.vega.client.model.ApprovalInfo
import app.vega.client.model.ChatMessage
import app.vega.client.model.CommandResult
import app.vega.client.model.ConnectionState
import app.vega.client.model.ContinuityHint
import app.vega.client.model.MessageStatus
import app.vega.client.model.MobileCommand
import app.vega.client.model.MobileCommandCard
import app.vega.client.model.Role
import app.vega.client.model.SessionInfo
import app.vega.client.model.TurnEvent
import app.vega.client.model.TurnLifecycle
import app.vega.client.model.VegaPresence
import app.vega.client.model.WanStatus
import app.vega.client.model.WanStatusResolver
import app.vega.client.model.WanTurnAction
import app.vega.client.model.MobileResultUi
import app.vega.client.model.continuityFrom
import app.vega.client.mobile.AndroidMobileOps
import app.vega.client.mobile.MobileCommandExecutor
import app.vega.client.net.ApiException
import app.vega.client.net.HttpOrigin
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
import java.util.concurrent.atomic.AtomicLong

class ChatViewModel(application: Application) : AndroidViewModel(application) {

    private val logTag = "vega-chat"
    private val app = application
    private val requireWanTls: Boolean = !BuildConfig.DEBUG
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
    private val turnLifecycle = TurnLifecycle { idCounter.getAndIncrement() }

    private val _coreUrl = MutableStateFlow(bridge.coreUrl)
    val coreUrl: StateFlow<String> = _coreUrl.asStateFlow()

    private val _wanUrl = MutableStateFlow(prefs.getString("wan_url", "").orEmpty())
    val wanUrl: StateFlow<String> = _wanUrl.asStateFlow()

    private val _wanStatus = MutableStateFlow(
        WanStatusResolver.resolve(_wanUrl.value, wan.state, requireWanTls)
    )
    val wanStatus: StateFlow<WanStatus> = _wanStatus.asStateFlow()

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

    private val _continuity = MutableStateFlow<ContinuityHint?>(null)
    val continuity: StateFlow<ContinuityHint?> = _continuity.asStateFlow()

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

    /**
     * Fase 29 — turnos enviados pela WAN em voo (assistantIds). Quando o socket
     * WAN cai, essas mensagens não têm a proteção do SSE do caminho HTTP (que
     * falha sozinho); sem isso ficariam presas em SENDING/STREAMING e bloqueariam
     * o input. Todas as operações neste set ocorrem na main thread (viewModelScope).
     */
    private val wanTurns = HashSet<Long>()
    private var lastWanState: String? = null

    init {
        wan.onTurnFrame = { type, payload, requestId -> handleWanFrame(type, payload, requestId) }
        wan.onMobileCommand = { payload -> handleMobileCommand(payload) }
        bridge.onLanCommands = { payload -> handleLanCommand(payload) }
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

    /**
     * Fase 27.1 — base HTTP do Core para os endpoints `/api/...`.
     *
     * Prefere a LAN (Core local) quando conectada; em WAN-only deriva a origem
     * HTTP do endpoint WAN (ws→http, wss→https). Devolve "" quando não há base
     * válida — evita o crash do OkHttp por URL sem esquema.
     *
     * Fase 28.1 — usada APENAS por endpoints com contrato remoto
     * (sessions/histórico/ops/aprovações/remote-*): o token do device vai no
     * header `Authorization: Bearer`. Endpoints só-LAN (/api/vega/state, TTS)
     * usam [localCoreBase], que nunca deriva da WAN.
     */
    private fun httpBase(): String {
        val lan = HttpOrigin.normalize(_coreUrl.value)
        if (isLanOnline() && lan != null) return lan
        return HttpOrigin.fromWan(_wanUrl.value) ?: lan ?: ""
    }

    /**
     * Fase 28.1 — base HTTP LAN-only (estado local + síntese de voz). Nunca
     * deriva da WAN: esses endpoints não têm contrato remoto.
     */
    private fun localCoreBase(): String? = HttpOrigin.normalize(_coreUrl.value)

    private fun httpBaseOrThrow(): String {
        val base = httpBase()
        if (base.isBlank()) {
            throw IllegalStateException("sem endereço HTTP do Core (LAN offline e WAN sem origem http/https)")
        }
        return base
    }

    /**
     * Fase 28.1 — credencial do device para `Authorization: Bearer`: LAN
     * (bridge) ou WAN (authToken em memória). O Core identifica o device pelo
     * token; prefix "Bearer" nunca vaza para logs/URLs.
     */
    private fun deviceToken(): String? = bridge.token ?: wan.authToken

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
        _wanStatus.value = WanStatusResolver.resolve(_wanUrl.value, w, requireWanTls)
        handleTransportLoss(w)
        if (bridge.conversationId != null && _conversationId.value == null) _conversationId.value = bridge.conversationId
        if (wan.conversationId != null && _conversationId.value == null) _conversationId.value = wan.conversationId
    }

    /**
     * Fase 29 — quando a WAN sai de "connected", os turnos WAN em voo são
     * encerrados (SUCCESS/ERROR/TIMEOUT/DISCONNECTED): o OKHttp não falha a
     * mensagem como o SSE do caminho HTTP faz. Reaproveita a detecção JÁ
     * existente (state machine + heartbeat half-open) — sem novos timers.
     * Frames tardios do mesmo request são ignorados pela correlação removida.
     */
    private fun handleTransportLoss(w: String) {
        val prev = lastWanState
        lastWanState = w
        if (prev != "connected" || w == "connected" || wanTurns.isEmpty()) return
        val toFail = _messages.value
            .filter { it.status == MessageStatus.SENDING || it.status == MessageStatus.STREAMING }
            .map { it.id }
            .filter { it in wanTurns }
        if (toFail.isEmpty()) return
        Log.w(logTag, "WAN desconectada com ${toFail.size} turno(s) em voo — encerrando turnos")
        for (id in toFail) {
            if (turnLifecycle.failTurn(id)) failMessage(id, "conexão WAN perdida durante a resposta")
        }
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
                val base = localCoreBase()
                if (base == null) return@launch
                val j = withContext(Dispatchers.IO) { http.getJson(base, "/api/vega/state") }
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
        val command = MobileCommand(
            commandId = commandId,
            targetDeviceId = wan.deviceId,
            capability = payload.optString("capability"),
            args = payload.optJSONObject("args") ?: JSONObject(),
            timeoutMs = payload.optInt("timeout_ms", 15_000),
        )
        viewModelScope.launch {
            executeDeviceCommand(command) { result ->
                if (isWanOnline()) withContext(Dispatchers.IO) {
                    wan.sendCommandResult(
                        commandId = result.commandId,
                        status = result.status,
                        result = result.result,
                        error = result.error,
                        startedAt = result.startedAt,
                        finishedAt = result.finishedAt,
                    )
                }
            }
        }
    }

    private fun handleLanCommand(payload: JSONObject) {
        val commandId = payload.optString("command_id").ifBlank { return }
        val command = MobileCommand(
            commandId = commandId,
            targetDeviceId = bridge.deviceId,
            capability = payload.optString("capability"),
            args = payload.optJSONObject("args") ?: JSONObject(),
            timeoutMs = payload.optInt("timeout_ms", 15_000),
        )
        viewModelScope.launch {
            executeDeviceCommand(command) { result ->
                withContext(Dispatchers.IO) {
                    bridge.reportCommandResult(
                        commandId = result.commandId,
                        status = result.status,
                        result = result.result,
                        error = result.error,
                        startedAt = result.startedAt,
                        finishedAt = result.finishedAt,
                    )
                }
            }
        }
    }

    private suspend fun executeDeviceCommand(command: MobileCommand, report: suspend (CommandResult) -> Unit) {
        val result = mobileExecutor.execute(command)
        _mobileResults.update {
            (listOf(
                MobileResultUi(command.capability, result.commandId, result.status, result.error)
            ) + it).take(10)
        }
        try {
            report(result)
        } catch (e: Exception) {
            Log.w("vega-chat", "falha ao enviar command_result (${command.capability}): ${e.message}")
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
        val raw = _wanUrl.value.trim()
        // Bugfix LAN/WAN: endpoint WAN validado centralmente. Um IP privado ou
        // ws:// em produção NUNCA conecta — nem como fallback para a LAN.
        val v = WanEndpoint.validate(raw, requireTls = requireWanTls)
        if (!v.valid) {
            _banner.value = "WAN inválido: ${v.reason}"
            return
        }
        val url = v.normalized
        prefs.edit().putString("wan_url", url).apply()
        _wanUrl.value = url
        wan.configure(token = bridge.token, pairingCode = null, deviceId = bridge.deviceId)
        wan.connect(url)
    }

    fun disconnectWan() {
        wan.disconnect()
    }

    fun send(text: String) {
        val t = text.trim()
        if (t.isEmpty()) return
        if (TurnLifecycle.isTurnInFlight(_messages.value)) return
        if (!isOnline()) {
            val status = _connection.value.label
            _banner.value = "JARVIS está $status — conecte ao Core primeiro"
            return
        }
        val user = ChatMessage(idCounterAndIncrement(), Role.USER, t, MessageStatus.DONE)
        val assistant = ChatMessage(idCounterAndIncrement(), Role.ASSISTANT, "", MessageStatus.SENDING)
        _messages.update { it + user + assistant }
        recomputeContinuity()
        val runEpoch = turnLifecycle.currentEpoch()
        viewModelScope.launch {
            try {
                if (isWanOnline()) runWanTurn(t, assistant.id) else runHttpTurn(t, assistant.id, runEpoch)
            } catch (e: Exception) {
                failMessage(assistant.id, friendlyError(e))
            }
        }
    }

    private suspend fun runHttpTurn(content: String, assistantId: Long, runEpoch: Long) {
        val token = bridge.token ?: throw IllegalStateException("bridge não autenticado")
        setLocalPresence("thinking")
        Log.i(logTag, "http turn iniciado assistant=$assistantId session=${_conversationId.value}")
        http.sendMessageSse(httpBaseOrThrow(), token, _conversationId.value, content)
            .collect { ev -> handleEvent(ev, assistantId, runEpoch) }
        val msg = _messages.value.find { it.id == assistantId }
        if (msg != null && (msg.status == MessageStatus.SENDING || msg.status == MessageStatus.STREAMING)) {
            failMessage(assistantId, msg.error ?: "conexão encerrada sem conclusão")
        }
    }

    private suspend fun runWanTurn(content: String, assistantId: Long) {
        setLocalPresence("thinking")
        // Fase 28.1 — corrige a corrida "resposta chega antes do registerTurn":
        // o request_id é gerado e correlacionado ANTES do envio (para a
        // possível resposta imediata do Core); falha de envio limpa a correlação.
        val requestId = wan.newRequestId()
        turnLifecycle.registerTurn(requestId, assistantId)
        try {
            withContext(Dispatchers.IO) {
                wan.sendMessage(content, JSONObject().put("stream", true), requestId)
            }
            // Fase 29 — marca o turno como WAN-bound: se o socket cair antes da
            // resposta, o handleTransportLoss encerra a mensagem (e não o SSE).
            wanTurns.add(assistantId)
            Log.i(logTag, "wan turn iniciado assistant=$assistantId request=${requestId.take(8)}")
        } catch (e: Exception) {
            wanTurns.remove(assistantId)
            turnLifecycle.forgetAssistant(assistantId)
            throw e
        }
    }

    private fun handleEvent(ev: TurnEvent, assistantId: Long, runEpoch: Long) {
        if (runEpoch != turnLifecycle.currentEpoch()) return
        when (ev) {
            is TurnEvent.Chunk -> {
                _messages.update { TurnLifecycle.MessageState.chunk(it, assistantId, ev.text) }
                recomputeContinuity()
                if (_presence.value != "thinking") setLocalPresence("thinking")
            }
            is TurnEvent.ToolStart -> {
                _messages.update { TurnLifecycle.MessageState.toolStart(it, assistantId, ev.names) }
                recomputeContinuity()
                setLocalPresence("executing")
            }
            is TurnEvent.ToolDone -> {
                _messages.update { TurnLifecycle.MessageState.toolDone(it, assistantId, ev.name, ev.ok, ev.structured) }
                recomputeContinuity()
                setLocalPresence("thinking")
            }
            is TurnEvent.ApprovalRequest -> {
                _pendingApproval.value = ev.approval
                setLocalPresence("waiting_confirmation")
            }
            is TurnEvent.Done -> {
                completeTurn(assistantId, ev.content)
                ev.sessionId?.let { _conversationId.value = it }
            }
            is TurnEvent.ErrorEvent -> {
                if (_messages.value.none { it.id == assistantId }) return
                val detail = ev.detail.ifBlank { _messages.value.find { it.id == assistantId }?.error ?: "erro" }
                failMessage(assistantId, detail)
            }
            else -> Unit
        }
    }

    private fun handleWanFrame(type: String, payload: JSONObject, envelopeRequestId: String) {
        when (val action = turnLifecycle.onWanFrame(type, payload, envelopeRequestId)) {
            is WanTurnAction.AgentEvent ->
                viewModelScope.launch { handleEvent(action.event, action.assistantId, turnLifecycle.currentEpoch()) }
            is WanTurnAction.Complete -> completeTurn(action.assistantId, action.content)
            is WanTurnAction.Fail -> failMessage(action.assistantId, action.error)
            WanTurnAction.Ignore -> Unit
        }
    }

    private fun completeTurn(assistantId: Long, content: String) {
        wanTurns.remove(assistantId)
        _messages.update { TurnLifecycle.MessageState.completeDone(it, assistantId, content) }
        recomputeContinuity()
        setLocalPresence("success")
        postponeIdle(1500)
        Log.i(logTag, "turn completo assistant=$assistantId")
        speakLatest(assistantId)
    }

    private fun speakLatest(assistantId: Long) {
        if (!_ttsEnabled.value || !isLanOnline()) return
        val content = _messages.value.find { it.id == assistantId }?.content.orEmpty()
        if (content.isBlank()) return
        viewModelScope.launch {
            val base = localCoreBase() ?: return@launch
            tts.play(http.ttsUrl(base, content))
        }
    }

    fun respondApproval(approved: Boolean) {
        val a = _pendingApproval.value ?: return
        _pendingApproval.value = null
        setLocalPresence("verifying")
        viewModelScope.launch {
            try {
                if (isWanOnline()) {
                    val awaitingId = _messages.value.lastOrNull { it.status == MessageStatus.SENDING || it.status == MessageStatus.STREAMING }?.id
                    // Fase 28.1 — correlaciona ANTES do envio (mesmo fixo do
                    // runWanTurn): o approval_respond pode retornar no instante
                    // seguinte; falha de envio limpa a correlação.
                    val resumeRequestId = wan.newRequestId()
                    if (awaitingId != null) turnLifecycle.bindResume(resumeRequestId, awaitingId)
                    try {
                        withContext(Dispatchers.IO) {
                            wan.sendApproval(a.id, approved, resumeRequestId)
                        }
                    } catch (e: Exception) {
                        awaitingId?.let { turnLifecycle.forgetAssistant(it) }
                        throw e
                    }
                } else {
                    withContext(Dispatchers.IO) {
                        http.respondApproval(httpBaseOrThrow(), a.id, approved, deviceToken())
                    }
                }
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
            if (TurnLifecycle.isTurnInFlight(_messages.value)) return@launch
            turnLifecycle.beginConversation()
            _pendingApproval.value = null
            _messages.value = emptyList()
            _sessionTitle.value = ""
            recomputeContinuity()
            // Fase 27 — o backend rotaciona a sessão JARVIS estável do DEVICE
            // (mesmo mecanismo dos endpoints remotos). O id devolvido vira a nova
            // âncora da conversa; em Core antigo sem o endpoint, cai no fallback
            // usando a sessão estável do device (nunca quebra o próximo envio).
            try {
                val s = withContext(Dispatchers.IO) {
                    http.resetConversation(httpBaseOrThrow(), bridge.token.orEmpty(), bridge.deviceId)
                }
                _conversationId.value = s.id
                _sessionTitle.value = s.title
            } catch (e: Exception) {
                _conversationId.value = null
            }
            loadSessions()
        }
    }

    fun loadSessions() {
        viewModelScope.launch {
            try {
                _sessions.value = withContext(Dispatchers.IO) { http.listSessions(httpBaseOrThrow(), deviceToken()) }
            } catch (e: Exception) {
                _banner.value = "não foi possível carregar o histórico: ${friendlyError(e)}"
            }
        }
    }

    fun openSession(id: String) {
        viewModelScope.launch {
            if (TurnLifecycle.isTurnInFlight(_messages.value)) return@launch
            try {
                val history = withContext(Dispatchers.IO) { http.fetchHistory(httpBaseOrThrow(), id, deviceToken()) }
                turnLifecycle.beginConversation()
                _pendingApproval.value = null
                _conversationId.value = id
                _messages.value = history
                recomputeContinuity()
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
                _ops.value = withContext(Dispatchers.IO) { http.opsOverview(httpBaseOrThrow(), deviceToken()) }
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

    private fun recomputeContinuity() {
        _continuity.value = continuityFrom(_messages.value)
    }

    private fun failMessage(id: Long, error: String) {
        wanTurns.remove(id)
        _messages.update { TurnLifecycle.MessageState.completeError(it, id, error) }
        recomputeContinuity()
        setLocalPresence("error")
        postponeIdle(2500)
        Log.w(logTag, "turn falhou assistant=$id: $error")
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
