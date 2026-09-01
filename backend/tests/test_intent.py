"""Testes da Fase 11.2 — Intent Detection determinístico."""

from app.ai.intent import detect_intent


class TestIntentDetectionApps:
    def test_detect_vscode(self):
        result = detect_intent("abra o vs code")
        assert result is not None
        assert result.tool_call.name == "open_application"
        assert result.tool_call.arguments["target"] == "vscode"
        assert result.confidence >= 0.90

    def test_detect_chrome(self):
        result = detect_intent("abrir o chrome")
        assert result is not None
        assert result.tool_call.name == "open_application"
        assert result.tool_call.arguments["target"] == "chrome"

    def test_detect_firefox(self):
        result = detect_intent("abre o firefox")
        assert result is not None
        assert result.tool_call.arguments["target"] == "firefox"

    def test_detect_spotify(self):
        result = detect_intent("abra o spotify")
        assert result is not None
        assert result.tool_call.arguments["target"] == "spotify"

    def test_detect_notepad(self):
        result = detect_intent("abra o bloco de notas")
        assert result is not None
        assert result.tool_call.arguments["target"] == "notepad"

    def test_detect_notepad_direct(self):
        result = detect_intent("abra o notepad")
        assert result is not None
        assert result.tool_call.arguments["target"] == "notepad"

    def test_detect_edge(self):
        result = detect_intent("abra o edge")
        assert result is not None
        assert result.tool_call.arguments["target"] == "edge"

    def test_detect_explorer(self):
        result = detect_intent("abra o explorador de arquivos")
        assert result is not None
        assert result.tool_call.arguments["target"] == "explorer"

    def test_detect_terminal(self):
        result = detect_intent("abra o terminal")
        assert result is not None
        assert result.tool_call.arguments["target"] == "terminal"

    def test_detect_calculator(self):
        result = detect_intent("abra a calculadora")
        assert result is not None
        assert result.tool_call.arguments["target"] == "calc"

    def test_detect_paint(self):
        result = detect_intent("abra o paint")
        assert result is not None
        assert result.tool_call.arguments["target"] == "paint"

    def test_detect_task_manager(self):
        result = detect_intent("abra o gerenciador de tarefas")
        assert result is not None
        assert result.tool_call.arguments["target"] == "task manager"

    def test_detect_settings(self):
        result = detect_intent("abra as configurações")
        assert result is not None
        assert result.tool_call.arguments["target"] == "settings"


class TestIntentDetectionURLs:
    def test_detect_youtube(self):
        result = detect_intent("abra o youtube")
        assert result is not None
        assert result.tool_call.name == "open_url"
        assert result.tool_call.arguments["url"] == "https://youtube.com"

    def test_detect_github(self):
        result = detect_intent("abra o github")
        assert result is not None
        assert result.tool_call.name == "open_url"
        assert result.tool_call.arguments["url"] == "https://github.com"

    def test_detect_google(self):
        result = detect_intent("abra o google")
        assert result is not None
        assert result.tool_call.name == "open_url"
        assert result.tool_call.arguments["url"] == "https://google.com"


class TestIntentDetectionMedia:
    def test_detect_play(self):
        result = detect_intent("toque uma música")
        assert result is not None
        assert result.tool_call.name == "play_media"

    def test_detect_pause(self):
        result = detect_intent("pause a música")
        assert result is not None
        assert result.tool_call.name == "pause_media"

    def test_detect_next_track(self):
        result = detect_intent("próxima música")
        assert result is not None
        assert result.tool_call.name == "next_track"

    def test_detect_previous_track(self):
        result = detect_intent("música anterior")
        assert result is not None
        assert result.tool_call.name == "previous_track"


class TestIntentDetectionVolume:
    def test_detect_volume_up(self):
        result = detect_intent("aumente o volume")
        assert result is not None
        assert result.tool_call.name == "volume_up"

    def test_detect_volume_down(self):
        result = detect_intent("diminua o volume")
        assert result is not None
        assert result.tool_call.name == "volume_down"

    def test_detect_mute(self):
        result = detect_intent("mute")
        assert result is not None
        assert result.tool_call.name == "mute"


class TestIntentDetectionWindows:
    def test_detect_lock(self):
        result = detect_intent("bloqueie o computador")
        assert result is not None
        assert result.tool_call.name == "lock_computer"

    def test_detect_sleep(self):
        result = detect_intent("coloque o computador para dormir")
        assert result is not None
        assert result.tool_call.name == "sleep_computer"

    def test_detect_restart(self):
        result = detect_intent("reinicie o computador")
        assert result is not None
        assert result.tool_call.name == "restart_computer"

    def test_detect_shutdown(self):
        result = detect_intent("desligue o computador")
        assert result is not None
        assert result.tool_call.name == "shutdown_computer"


class TestIntentDetectionStatus:
    def test_detect_system_status(self):
        result = detect_intent("como está o meu computador")
        assert result is not None
        assert result.tool_call.name == "get_system_status"

    def test_detect_system_stats(self):
        result = detect_intent("quanto de RAM tenho")
        assert result is not None
        assert result.tool_call.name == "get_system_stats"


class TestIntentDetectionNegative:
    def test_empty_text(self):
        assert detect_intent("") is None

    def test_none_text(self):
        assert detect_intent(None) is None

    def test_whitespace_only(self):
        assert detect_intent("   ") is None

    def test_conversational_text(self):
        assert detect_intent("oi, tudo bem?") is None

    def test_question_about_something_else(self):
        assert detect_intent("qual é a capital da França?") is None

    def test_low_confidence_below_threshold(self):
        # Texto ambíguo que não atinge confidence >= 0.85
        assert detect_intent("algo") is None
