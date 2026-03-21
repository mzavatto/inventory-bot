"""
WhatsApp webhook endpoint for Twilio integration.

Receives incoming WhatsApp messages from Twilio and replies via the assistant.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from twilio.request_validator import RequestValidator

from app.config import settings
from app.services.assistant import assistant_service
from app.services.session import session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

# Twilio media API base URL (trusted constant – never from user input)
_TWILIO_API_BASE = "https://api.twilio.com"

# Pattern that Twilio media paths must match
import re as _re

_TWILIO_MEDIA_PATH_RE = _re.compile(
    r"^/\d{4}-\d{2}-\d{2}/Accounts/AC[0-9a-f]+/Messages/MM[0-9a-f]+/Media/ME[0-9a-f]+$",
    _re.IGNORECASE,
)
_TWILIO_ACCOUNT_IN_PATH = _re.compile(
    r"/Accounts/(AC[0-9a-f]{32})/", _re.IGNORECASE
)


def _account_sid_from_twilio_media_url(url: str) -> str | None:
    """AccountSid dueño del recurso (debe coincidir con el Auth Token en Basic auth)."""
    try:
        path = urlparse(url).path
    except Exception:
        return None
    m = _TWILIO_ACCOUNT_IN_PATH.search(path)
    return m.group(1) if m else None


def _safe_twilio_media_url(user_url: str) -> str | None:
    """
    Validate and sanitise a Twilio-provided media URL to prevent SSRF.

    Returns a reconstructed URL built entirely from a trusted constant base
    and only the path component – after verifying that path matches the known
    Twilio media pattern. Returns None if the URL is invalid.
    """
    try:
        parsed = urlparse(user_url)
    except Exception:
        return None

    # Only allow HTTPS and the trusted api.twilio.com host
    if parsed.scheme != "https" or parsed.hostname != "api.twilio.com":
        return None

    # Validate the path against the known Twilio media path pattern
    path = parsed.path.rstrip("/")
    if not _TWILIO_MEDIA_PATH_RE.match(path):
        return None

    # Reconstruct URL from the trusted constant base + validated path only
    return _TWILIO_API_BASE + path


def _public_webhook_url(request: Request) -> str:
    """
    URL que Twilio usó al firmar: debe ser https y el host público (no el interno del proxy).
    """
    u = request.url
    path, query = u.path, u.query
    suffix = f"?{query}" if query else ""

    base = (settings.twilio_webhook_base_url or "").rstrip("/")
    if base:
        return f"{base}{path}{suffix}"

    proto = (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        or u.scheme
    )
    host = (
        request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        or request.headers.get("host", "").split(",")[0].strip()
    )
    if proto and host:
        return f"{proto}://{host}{path}{suffix}"

    return str(u)


def _twiml_response(message: str) -> str:
    """Build a simple TwiML XML response."""
    escaped = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Message>{escaped}</Message>"
        "</Response>"
    )


@router.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(...),
    Body: str = Form(default=""),
    MediaUrl0: str = Form(default=""),
    MediaContentType0: str = Form(default=""),
    x_twilio_signature: str = Header(default=""),
) -> PlainTextResponse:
    """
    Handle incoming WhatsApp messages from Twilio.

    The sender's phone number is used as the session ID so each WhatsApp
    conversation has its own persistent context.
    """
    if settings.twilio_auth_token:
        if not x_twilio_signature:
            logger.warning("Missing X-Twilio-Signature (From=%s)", From)
            raise HTTPException(status_code=403, detail="Missing signature")
        form_data = await request.form()
        url = _public_webhook_url(request)
        validator = RequestValidator(settings.twilio_auth_token)
        if not validator.validate(url, form_data, x_twilio_signature):
            logger.warning(
                "Invalid Twilio signature From=%s url_used=%s",
                From,
                url,
            )
            raise HTTPException(status_code=403, detail="Invalid signature")

    session_id = From

    # Handle voice notes (audio messages)
    if MediaUrl0 and MediaContentType0.startswith("audio/"):
        import httpx
        from openai import OpenAI

        safe_url = _safe_twilio_media_url(MediaUrl0)
        if not safe_url:
            logger.warning("Rejected media URL – failed validation: %s", MediaUrl0)
            user_message = Body or "No pude procesar el audio."
        else:
            # Basic auth: usuario = AccountSid del recurso en la URL (dueño del mensaje/medio).
            account_sid = _account_sid_from_twilio_media_url(safe_url)
            if not account_sid:
                account_sid = settings.twilio_account_sid
            token = settings.twilio_auth_token
            if (
                settings.twilio_account_sid
                and account_sid
                and account_sid.upper() != settings.twilio_account_sid.upper()
            ):
                logger.warning(
                    "TWILIO_ACCOUNT_SID no coincide con la cuenta del Media URL; "
                    "usando el SID del medio para Basic auth"
                )
            if not account_sid or not token:
                logger.error(
                    "No se puede bajar el audio: falta AccountSid o TWILIO_AUTH_TOKEN"
                )
                user_message = Body or "No pude procesar el audio."
            else:
                try:
                    async with httpx.AsyncClient(
                        follow_redirects=True, timeout=60.0
                    ) as client:
                        media_resp = await client.get(
                            safe_url,
                            auth=httpx.BasicAuth(account_sid, token),
                        )
                        media_resp.raise_for_status()
                        audio_bytes = media_resp.content

                    openai_client = OpenAI(api_key=settings.openai_api_key)
                    transcription = openai_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=("voice.ogg", audio_bytes, MediaContentType0),
                    )
                    user_message = transcription.text
                except Exception as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
                        logger.error(
                            "Twilio 401 al bajar el audio: TWILIO_AUTH_TOKEN debe ser el "
                            "**Auth Token** de la cuenta del mensaje (Console → Account), "
                            "no un API Key Secret; y debe coincidir con el Account del medio."
                        )
                    logger.exception(
                        "Error transcribing WhatsApp voice message: %s", exc
                    )
                    user_message = Body or "No pude procesar el audio."
    else:
        user_message = Body

    if not user_message.strip():
        return PlainTextResponse(
            _twiml_response("Por favor enviá un mensaje de texto o de voz."),
            media_type="application/xml",
        )

    try:
        reply = assistant_service.chat(session_id, user_message)
    except Exception as exc:
        logger.exception("Error processing WhatsApp message: %s", exc)
        reply = "Ocurrió un error. Por favor intentá de nuevo."

    return PlainTextResponse(
        _twiml_response(reply),
        media_type="application/xml",
    )
