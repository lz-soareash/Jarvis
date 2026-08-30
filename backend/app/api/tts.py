from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.services import tts as tts_service

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/ping")
async def tts_ping() -> dict:
    return {"status": "ok"}


@router.get("", response_class=Response)
async def tts_speak(text: str, voice: str | None = None) -> Response:
    """Converte texto em MP3 usando voz neural (Edge TTS)."""
    try:
        audio = await tts_service.synthesize(text, voice or tts_service.DEFAULT_VOICE)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — serviço externo pode falhar
        raise HTTPException(status_code=502, detail=f"Voz indisponível: {type(exc).__name__}") from exc
    latest = tts_service.ALLOWED_VOICES.get(voice, tts_service.DEFAULT_VOICE)
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-TTS-Voice": latest},
    )