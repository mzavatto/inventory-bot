"""Chat API endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from app.models import ChatRequest, ChatResponse, Order
from app.services.assistant import assistant_service
from app.services.session import session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Send a text message to the assistant and get a reply."""
    try:
        reply = assistant_service.chat(request.session_id, request.message)
        session = session_service.get(request.session_id)
        order = session.order if session else None
        return ChatResponse(
            session_id=request.session_id,
            reply=reply,
            order=order,
        )
    except Exception as exc:
        logger.exception("Error in chat endpoint: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/voice", response_model=ChatResponse)
async def chat_voice(
    session_id: str,
    audio: UploadFile = File(...),
) -> ChatResponse:
    """
    Transcribe an audio file and send it to the assistant.
    Accepts common audio formats: mp3, ogg, wav, m4a, webm, etc.
    """
    from openai import OpenAI
    from app.config import settings

    try:
        client = OpenAI(api_key=settings.openai_api_key)
        audio_bytes = await audio.read()

        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=(audio.filename or "audio.ogg", audio_bytes, audio.content_type),
        )
        user_message = transcription.text
        if not user_message.strip():
            raise HTTPException(
                status_code=422, detail="No se pudo transcribir el audio."
            )

        reply = assistant_service.chat(session_id, user_message)
        session = session_service.get(session_id)
        order = session.order if session else None
        return ChatResponse(
            session_id=session_id,
            reply=reply,
            order=order,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error in voice endpoint: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/session/{session_id}/order", response_model=Order)
async def get_order(session_id: str) -> Order:
    """Get the current order for a session."""
    session = session_service.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")
    return session.order


@router.delete("/session/{session_id}/order")
async def clear_order(session_id: str) -> JSONResponse:
    """Clear the order for a session."""
    success = session_service.reset_order(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")
    return JSONResponse(content={"message": "Pedido vaciado."})


@router.delete("/session/{session_id}")
async def delete_session(session_id: str) -> JSONResponse:
    """Delete a session (clears conversation history and order)."""
    success = session_service.delete(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")
    return JSONResponse(content={"message": "Sesión eliminada."})
