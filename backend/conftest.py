import os, pathlib
os.environ.update(ENV="dev", DATABASE_URL="sqlite:///test_sakhi.db", SK_NO_DOTENV="1", OPEN_REGISTRATION="1", OWNER_PHONE="")
for k in ("TWILIO_AUTH_TOKEN", "TWILIO_ACCOUNT_SID", "GROQ_API_KEY", "CRON_SECRET"): os.environ.pop(k, None)
pathlib.Path("test_sakhi.db").unlink(missing_ok=True)
import pytest
from fastapi.testclient import TestClient

@pytest.fixture()
def world(monkeypatch):
    import db, main, bot, notify, fixtures
    db.Base.metadata.drop_all(db.engine); db.Base.metadata.create_all(db.engine); fixtures.seed_test_world()
    bot.SEEN.clear(); bot.PREF.clear(); bot.ONB.clear()
    sent = []; monkeypatch.setattr(notify, "send", lambda phone, text: sent.append({"phone": phone, "text": text}) or True)
    def login(phone):
        c = TestClient(main.app); code = c.post("/api/auth/request-otp", json={"phone": phone}).json()["dev_code"]
        assert c.post("/api/auth/verify", json={"phone": phone, "code": code}).status_code == 200; return c
    return type("W", (), {"login": staticmethod(login), "app": main.app, "sent": sent})
