"""Download WhatsApp photos / PDFs from Twilio. Only ever fetches from Twilio hosts, never an arbitrary URL."""
import base64, io, os
from urllib.parse import urlparse
import httpx

OK = ("image/jpeg", "image/png", "image/webp")
def _twilio(url): return (urlparse(url or "").hostname or "").endswith(("twilio.com", "twiliocdn.com"))
async def _get(url):
    sid, tok = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(url, auth=(sid, tok) if sid and tok else None); r.raise_for_status(); return r.content

def _jpeg(im, px, quality, limit):
    c = im.copy(); c.thumbnail((px, px)); b = io.BytesIO(); c.save(b, "JPEG", quality=quality); raw = b.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode() if len(raw) <= limit else None

async def fetch_image_pair(url, ctype):
    """Returns (small, large) data URLs or None. small (640px) is kept as evidence; large (1280px) is used to read handwriting."""
    if ctype not in OK or not _twilio(url): return None, None
    try: raw = await _get(url)
    except Exception: return None, None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        return _jpeg(im, 640, 70, 180_000), _jpeg(im, 1280, 80, 2_500_000)
    except Exception:                                                                       # no Pillow: keep the original only if it is small
        u = f"data:{ctype};base64," + base64.b64encode(raw).decode() if len(raw) <= 180_000 else None
        return u, u

async def fetch_image(url, ctype): return (await fetch_image_pair(url, ctype))[0]

async def fetch_pdf_text(url, max_chars=6000):
    """Text of the first pages of a PDF, or None (scanned PDFs have no text layer: we say so instead of guessing)."""
    if not _twilio(url): return None
    try:
        raw = await _get(url)
        if not raw.startswith(b"%PDF") or len(raw) > 8_000_000: return None
        from pypdf import PdfReader
        txt = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages[:5]).strip()
        return txt[:max_chars] or None
    except Exception:
        return None
