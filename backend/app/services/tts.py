"""Síntese de voz neural via edge-tts (vozes Microsoft Edge).

Gera MP3 natural (ex.: pt-BR-FranciscaNeural) sem precisar de chave de API.
É um serviço externo (Microsoft) — o frontend usa fallback para a voz local
do navegador quando este serviço estiver indisponível.
"""

import io

ALLOWED_VOICES = {
    "pt-BR-FranciscaNeural": "Feminina (natural)",
    "pt-BR-AntonioNeural": "Masculina (natural)",
    "pt-PT-RaquelNeural": "Português de Portugal (feminina)",
    "en-US-AriaNeural": "Inglês (feminina)",
}

DEFAULT_VOICE = "pt-BR-FranciscaNeural"
MAX_TEXT_CHARS = 1500


async def synthesize(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Sintetiza `text` e devolve os bytes do áudio MP3."""
    if not text.strip():
        raise ValueError("Texto vazio")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"Texto muito longo (máximo {MAX_TEXT_CHARS} caracteres)")
    actual = voice if voice in ALLOWED_VOICES else DEFAULT_VOICE

    from edge_tts import Communicate

    buffer = io.BytesIO()
    communicate = Communicate(text, actual)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    data = buffer.getvalue()
    if not data:
        raise RuntimeError("Sem áudio gerado pelo serviço de voz")
    return data