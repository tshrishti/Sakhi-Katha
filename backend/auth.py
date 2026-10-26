"""Phone + one-time-code login, signed HttpOnly session cookie, server-side role checks.
(MaaSakhi logged in by phone number alone; this does not.)"""
import base64, hashlib, hmac, json, os, secrets, time
from fastapi import HTTPException, Request
from sqlalchemy import select
import db, notify

DEV = os.getenv("ENV", "prod") == "dev"
SECRET = os.getenv("SECRET_KEY", "")
if not SECRET:
    if not DEV: raise RuntimeError("SECRET_KEY is required when ENV=prod. Use ENV=dev for local development only.")
    SECRET = "dev-only-secret"
TTL = 8 * 3600
SECURE_COOKIE = os.getenv("PUBLIC_URL", "").startswith("https")   # Secure cookies only work over https (ngrok / hosted); plain http://localhost needs them off
def norm(phone):
    p = "".join(c for c in str(phone).replace("whatsapp:", "") if c.isdigit() or c == "+")
    return p if p.startswith("+") else "+91" + p[-10:]
def _h(code): return hashlib.sha256((code + SECRET).encode()).hexdigest()
def _sig(b): return hmac.new(SECRET.encode(), b, hashlib.sha256).hexdigest()
def make_token(uid):
    b = base64.urlsafe_b64encode(json.dumps({"u": uid, "e": int(time.time()) + TTL}).encode())
    return b.decode() + "." + _sig(b)
def read_token(tok):
    try:
        b, sig = tok.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sig(b.encode())): return None
        d = json.loads(base64.urlsafe_b64decode(b))
        return d["u"] if d["e"] > time.time() else None
    except Exception:
        return None

def request_otp(phone):
    phone, now = norm(phone), time.time()
    with db.S() as s:
        u = s.scalars(select(db.User).where(db.User.phone == phone, db.User.active == True)).first()  # noqa: E712
        if not u: return {"sent": True}                       # same answer for unknown numbers: no account enumeration
        o = s.get(db.Otp, phone)
        if not o: o = db.Otp(phone=phone, sent=0, window_start=now, attempts=0); s.add(o)
        if now - o.window_start > 3600: o.window_start, o.sent = now, 0
        if o.sent >= 5: raise HTTPException(429, "Too many codes requested. Try again later.")
        code = f"{secrets.randbelow(10**6):06d}"
        o.code_hash, o.expires, o.attempts, o.sent = _h(code), now + 300, 0, o.sent + 1
        s.commit()
    notify.send(phone, f"Sakhi Khata login code: {code}. Valid for 5 minutes. Do not share it.")
    return {"sent": True, **({"dev_code": code} if DEV else {})}

def verify_otp(phone, code):
    phone, now = norm(phone), time.time()
    with db.S() as s:
        o = s.get(db.Otp, phone)
        if not o or o.expires < now or o.attempts >= 5: raise HTTPException(401, "Invalid or expired code")
        o.attempts += 1
        ok = hmac.compare_digest(o.code_hash or "", _h(code))
        if ok: o.expires = 0                                   # single use
        s.commit()
        if not ok: raise HTTPException(401, "Invalid or expired code")
        u = s.scalars(select(db.User).where(db.User.phone == phone, db.User.active == True)).first()  # noqa: E712
        return {"id": u.id, "name": u.name, "role": u.role}

def current_user(request: Request):
    uid = read_token(request.cookies.get("sk", ""))
    if uid is None: return None
    with db.S() as s:
        u = s.get(db.User, uid)
        if not u or not u.active: return None
        return {"id": u.id, "name": u.name, "role": u.role, "group_id": u.group_id, "phone": u.phone, "consented": bool(u.consent_at)}

def require(*roles):
    def dep(request: Request):
        u = current_user(request)
        if not u: raise HTTPException(401, "Please log in")
        if roles and u["role"] not in roles: raise HTTPException(403, "Not allowed for your role")
        return u
    return dep

