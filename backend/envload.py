"""Loads ../.env (or ./.env) into the environment, so keys do not have to be exported by hand. Real environment variables win.
Also tidies values people paste into hosting dashboards (stray spaces / trailing slash) and, on Render, falls back to the
service's own public URL so PUBLIC_URL does not have to be set by hand."""
import os, pathlib
if not os.getenv("SK_NO_DOTENV"):
    try:
        from dotenv import load_dotenv
        for p in (pathlib.Path(__file__).parent.parent / ".env", pathlib.Path(__file__).parent / ".env"):
            if p.exists(): load_dotenv(p, override=False)
    except ImportError:
        pass

for _k in ("SECRET_KEY", "DATABASE_URL", "PUBLIC_URL", "GROQ_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
           "TWILIO_WHATSAPP_NUMBER", "CRON_SECRET", "OWNER_PHONE", "SANDBOX_JOIN_PHRASE"):
    if os.environ.get(_k) is not None: os.environ[_k] = os.environ[_k].strip()
if not os.getenv("PUBLIC_URL") and os.getenv("RENDER_EXTERNAL_URL"):      # Render sets this automatically on every web service
    os.environ["PUBLIC_URL"] = os.environ["RENDER_EXTERNAL_URL"]
if os.getenv("PUBLIC_URL"): os.environ["PUBLIC_URL"] = os.environ["PUBLIC_URL"].rstrip("/")
