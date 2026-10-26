"""Voice note -> text using Groq Whisper. Twilio media needs your Twilio SID/token to download.
Returns None on any failure, and the bot then asks the user to type."""
import os
import httpx

async def transcribe(media_url, content_type):
    key, sid, tok = os.getenv("GROQ_API_KEY"), os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
    if not (key and media_url): return None
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            audio = await c.get(media_url, auth=(sid, tok) if sid and tok else None)
            audio.raise_for_status()
            data = {"model": os.getenv("GROQ_STT_MODEL", "whisper-large-v3"), "response_format": "json", "temperature": "0"}
            if os.getenv("STT_LANGUAGE"): data["language"] = os.getenv("STT_LANGUAGE")   # e.g. kn, hi, en; unset = auto-detect
            r = await c.post("https://api.groq.com/openai/v1/audio/transcriptions",
                             headers={"Authorization": f"Bearer {key}"}, data=data,
                             files={"file": ("voice.ogg", audio.content, content_type or "audio/ogg")})
            r.raise_for_status()
            return (r.json().get("text") or "").strip() or None
    except Exception:
        return None
