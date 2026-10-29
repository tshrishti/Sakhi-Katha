import os, httpx

def send_ex(phone, text):
    """Outbound WhatsApp via Twilio REST, reporting what happened: {ok, sid, reason}. Best effort: never breaks a business action.
    Recipient must have opted in (sandbox: joined). With PUBLIC_URL set, Twilio reports delivered/read back to /whatsapp/status."""
    sid, tok = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
    if not (sid and tok): return {"ok": False, "sid": None, "reason": "not_configured"}
    try:
        data = {"From": os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886"), "To": "whatsapp:" + phone, "Body": text}
        pub = os.getenv("PUBLIC_URL", "").rstrip("/")
        if pub: data["StatusCallback"] = pub + "/whatsapp/status"
        r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", auth=(sid, tok), timeout=15, data=data)
        ok = r.status_code < 300
        return {"ok": ok, "sid": (r.json().get("sid") if ok else None), "reason": None if ok else f"http_{r.status_code}"}
    except Exception:
        return {"ok": False, "sid": None, "reason": "error"}

def send(phone, text): return send_ex(phone, text)["ok"]
