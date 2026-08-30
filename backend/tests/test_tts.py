import pytest

from app.services import tts


def test_tts_rejects_empty(client):
    response = client.get("/api/tts", params={"text": "   "})
    assert response.status_code == 400


def test_tts_rejects_too_long(client):
    long_text = "a" * (tts.MAX_TEXT_CHARS + 1)
    response = client.get("/api/tts", params={"text": long_text})
    assert response.status_code == 400


def test_tts_unavailable_voice_falls_back_to_default(client, monkeypatch):
    # Sem rede real nos testes: monkeypatch o serviço para devolver bytes falsos.
    async def fake_synthesize(text, voice=tts.DEFAULT_VOICE):
        return b"\xff\xf3fake-mp3"

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    response = client.get("/api/tts", params={"text": "olá"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"\xff\xf3fake-mp3"


@pytest.mark.asyncio
async def test_synthesize_rejects_empty_text():
    with pytest.raises(ValueError):
        await tts.synthesize("   ")


@pytest.mark.asyncio
async def test_synthesize_rejects_long_text():
    with pytest.raises(ValueError):
        await tts.synthesize("a" * (tts.MAX_TEXT_CHARS + 1))