import os, httpx

def send(phone, text):
    """Outbound WhatsApp via Twilio REST. Best effort: never breaks a business action. Recipient must have opted in (sandbox: joined)."""
    sid, tok = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
    if not (sid and tok): return False
    try:
        r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", auth=(sid, tok), timeout=15,
                       data={"From": os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886"), "To": "whatsapp:" + phone, "Body": text})
        return r.status_code < 300
    except Exception:
        return False
