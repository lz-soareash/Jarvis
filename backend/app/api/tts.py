from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.services import tts as tts_service

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/ping")
async def tts_ping() -> dict:
    return {"status": "ok"}


@router.get("/speech")
async def tts_speech(
    text: str,
    split: bool = Query(True, description="Dividir em blocos naturais de fala"),
) -> dict:
    """Prepara o SPEECH_TEXT (limpo e formatado) a partir do DISPLAY_TEXT.

    Não altera o texto visual: apenas devolve a versão pronta para o TTS,
    com os blocos de fala sugeridos.
    """
    try:
        prepared = tts_service.prepare_speech_text(text, split=split)
    except Exception as exc:  # noqa: BLE001 — nunca derruba o TTS
        raise HTTPException(status_code=400, detail=f"Erro ao preparar fala: {exc}") from exc
    return {
        "display_text": prepared.display_text,
        "speech_text": prepared.speech_text,
        "context": prepared.context.category,
        "utterances": prepared.utterances,
    }


@router.get("", response_class=Response)
async def tts_speak(text: str, voice: str | None = None) -> Response:
    """Converte texto em MP3 usando voz neural (Edge TTS)."""
    try:
        audio = await tts_service.synthesize(text, voice or tts_service.DEFAULT_VOICE)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — serviço externo pode falhar
        raise HTTPException(status_code=502, detail=f"Voz indisponível: {type(exc).__name__}") from exc
    latest = tts_service.ALLOWED_VOICES.get(voice or tts_service.DEFAULT_VOICE, tts_service.DEFAULT_VOICE)
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-TTS-Voice": latest},
    )