"""Testes da Fase 6b — Voz & Fala: sanitizer, formatter, contexto e endpoint."""

import pytest

from app.services import tts as tts_service
from app.speech import (
    formatter,
    prepare_speech_text,
    sanitizer,
)


# ----------------------------------------------------------------- sanitarização
def test_sanitizer_removes_headings_and_bold(client):
    out = sanitizer.sanitize("## Título\n\n**Destaque** e _itálico_")
    assert "#" not in out
    assert "*" not in out
    assert "_" not in out
    assert "Título" in out
    assert "Destaque" in out


def test_sanitizer_removes_markdown_lists(client):
    out = sanitizer.sanitize("* item um\n- item dois\n1. item três")
    assert "*" not in out
    assert "- " not in out


def test_sanitizer_replaces_code_block_with_brief(client):
    out = sanitizer.sanitize("```python\nprint('x')\n```")
    assert "código" in out.lower()
    assert "print" not in out


def test_sanitizer_removes_url(client):
    out = sanitizer.sanitize("Veja https://www.exemplo.com.br/x para mais")
    assert "https" not in out
    assert "link" in out.lower()


def test_sanitizer_removes_html_and_emoji(client):
    out = sanitizer.sanitize("Olá <b>mundo</b> 😀 🔥")
    assert "<" not in out
    assert "😀" not in out
    assert "🔥" not in out


def test_sanitizer_summarizes_json(client):
    out = sanitizer.sanitize('{"status": "ok", "cpu": 32}')
    assert "{" not in out
    assert "dados" in out.lower()


def test_sanitizer_keeps_display_untouched(client):
    original = "## Status\n* CPU: 32%"
    sanitizer.sanitize(original)
    assert original == "## Status\n* CPU: 32%"


def test_sanitizer_does_not_break_markdown_links(client):
    out = sanitizer.sanitize("Leia o [artigo](http://x.com) sobre IA")
    assert "artigo" in out
    assert "http" not in out


# ----------------------------------------------------------------- formatação
def test_formatter_numbers_and_percent(client):
    assert formatter.format("32%") == "trinta e dois por cento"


def test_formatter_decimal_and_unit(client):
    out = formatter.format("8.4 GB")
    assert "oito" in out
    assert "gigabytes" in out


def test_formatter_acronyms(client):
    out = formatter.format("A CPU está a 50%")
    assert "processador" in out


def test_formatter_temperature(client):
    out = formatter.format("Temperatura 54°C")
    assert "cinquenta e quatro" in out
    assert "graus Celsius" in out


def test_formatter_protects_brands(client):
    out = formatter.format("Windows 11 e Ryzen 5 5600G")
    assert "Windows 11" in out
    assert "Ryzen 5 5600G" in out
    assert "onze" not in out


def test_formatter_empty(client):
    assert formatter.format("") == ""
    assert formatter.format("   ") == ""


def test_split_utterances_splits_long(client):
    # Sentenças curtas ficam num mesmo bloco (fala contínua); só separa
    # quando o texto passa do limite (MAX_SENTENCE_CHARS).
    short = formatter.split_into_utterances(
        "O serviço do Spotify retornou um erro de autenticação. "
        "Vou precisar renovar a autorização. Por favor aguarde."
    )
    assert len(short) == 1
    assert all(u for u in short)

    long_text = ("Cadastrei com sucesso o seu novo dispositivo. " * 60).strip()
    out = formatter.split_into_utterances(long_text)
    assert len(out) >= 2
    assert all(u for u in out)


def test_split_utterances_empty(client):
    assert formatter.split_into_utterances("") == []


# ------------------------------------------------------------------- contexto
from app.speech.context import detect_context  # noqa: E402


def test_context_confirmation(client):
    assert detect_context("Pronto. Playlist criada.").category == "CONFIRMATION"


def test_context_error(client):
    assert detect_context("Não consegui concluir essa operação.").category == "ERROR"


def test_context_alert(client):
    assert detect_context("Atenção. Detectei um problema no serviço.").category == "ALERT"


def test_context_information(client):
    assert detect_context("Os dados do sistema mostram 32% de uso.").category == "INFORMATION"


def test_context_normal_default(client):
    assert detect_context("Qualquer frase comum sem marcador.").category == "NORMAL"


# -------------------------------------------------------------------- manager
def test_prepare_speech_separates_display_and_speech(client):
    prepared = prepare_speech_text("## Status do sistema\n* CPU: 32%")
    assert prepared.display_text == "## Status do sistema\n* CPU: 32%"
    assert "##" not in prepared.speech_text
    assert "por cento" in prepared.speech_text


def test_prepare_speech_utterances(client):
    # Frases curtas são agrupadas num único bloco de fala (sem pausas por frase).
    prepared = prepare_speech_text("Primeira frase. Segunda frase.")
    assert prepared.utterances == ["Primeira frase. Segunda frase."]


# ------------------------------------------------------------------- cache tts
def test_audio_cache_lru(client):
    cache = tts_service._AudioCache(maxsize=2)
    cache.put(("a", "v"), b"1")
    cache.put(("b", "v"), b"2")
    cache.get(("a", "v"))  # reanima "a"
    cache.put(("c", "v"), b"3")  # expulsa "b" (menos recente)
    assert cache.get(("b", "v")) is None
    assert cache.get(("a", "v")) == b"1"
    assert cache.get(("c", "v")) == b"3"
    cache.clear()
    assert len(cache) == 0


def test_audio_cache_disabled_when_zero(client):
    cache = tts_service._AudioCache(maxsize=0)
    cache.put(("a", "v"), b"1")
    assert cache.get(("a", "v")) is None


# -------------------------------------------------------------------- endpoint
def test_tts_speech_endpoint_returns_speech_text(client):
    res = client.get("/api/tts/speech", params={"text": "## Status\n* CPU: 32%"})
    assert res.status_code == 200
    data = res.json()
    assert data["display_text"].startswith("## Status")
    assert "por cento" in data["speech_text"]
    assert "context" in data
    assert isinstance(data["utterances"], list)


def test_tts_speech_endpoint_empty(client):
    res = client.get("/api/tts/speech", params={"text": ""})
    assert res.status_code == 200
    data = res.json()
    assert data["speech_text"] == ""


# --------------------------------------------------------------- provider abstração
def test_tts_provider_selection_edge(client):
    from app.services.tts_providers import EdgeTTSProvider, get_tts_provider, reset_provider_cache

    reset_provider_cache()
    provider = get_tts_provider()
    assert isinstance(provider, EdgeTTSProvider)
    reset_provider_cache()


def test_tts_default_voice_is_antonio_profile(client):
    assert tts_service.DEFAULT_VOICE == "pt-BR-AntonioNeural"
