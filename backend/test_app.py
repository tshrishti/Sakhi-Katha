from fastapi.testclient import TestClient
import fixtures, bot, bootstrap, svc, auth

LEADER, BUYER, OFFICER, SELLER = "+919000000001", "+919000000002", "+919000000009", "+919000000100"
ORDER = fixtures.DATA["order_text"]

def test_login_rbac_and_no_enumeration(world):
    c = TestClient(world.app)
    assert c.get("/api/me").status_code == 401
    assert "dev_code" not in c.post("/api/auth/request-otp", json={"phone": "+911111111111"}).json()   # unknown number: nothing leaks
    assert c.post("/api/auth/verify", json={"phone": BUYER, "code": "000000"}).status_code == 401
    b = world.login(BUYER)
    assert b.get("/api/me").json()["role"] == "buyer"
    for path in ("/api/leader/orders", "/api/seller/tasks", "/api/officer/summary"): assert b.get(path).status_code == 403

def test_otp_rate_limit_and_single_use(world):
    c = TestClient(world.app)
    codes = [c.post("/api/auth/request-otp", json={"phone": BUYER}).json()["dev_code"] for _ in range(5)]
    assert c.post("/api/auth/request-otp", json={"phone": BUYER}).status_code == 429
    assert c.post("/api/auth/verify", json={"phone": BUYER, "code": codes[-1]}).status_code == 200
    assert c.post("/api/auth/verify", json={"phone": BUYER, "code": codes[-1]}).status_code == 401

def test_full_lifecycle_with_quorum_and_scoping(world):
    b, l = world.login(BUYER), world.login(LEADER)
    gid = b.get("/api/buyer/groups").json()[0]["id"]
    o = b.post("/api/buyer/orders", json={"group_id": gid, "text": ORDER}).json(); oid = o["id"]
    assert "plan" not in o and "payout" not in o                               # buyer never sees internals
    q = l.post(f"/api/leader/orders/{oid}/quote", json={}).json(); assert q["price"]["per_unit"]["floor"] == "84.74" and q["quote"] == "85"
    assert l.post(f"/api/leader/orders/{oid}/plan").status_code == 409         # buyer has not accepted yet
    assert b.post(f"/api/buyer/orders/{oid}/accept").json()["status"] == "accepted"
    p = l.post(f"/api/leader/orders/{oid}/plan").json(); assert p["plan"]["feasible"]
    assert l.post(f"/api/leader/orders/{oid}/start").json()["status"] == "in_production"
    assert l.post(f"/api/leader/orders/{oid}/payout", json={"results": {}}).json()["status"] == "payout_pending"
    assert l.post(f"/api/leader/orders/{oid}/payout/approve").json()["status"] == "payout_pending"   # leader alone is not enough
    s1, s2 = world.login(SELLER), world.login("+919000000101")
    mine = s1.get("/api/seller/earnings").json(); assert mine and all(e["line"]["member"] == s1.get("/api/me").json()["id"] for e in mine)
    assert s1.post(f"/api/seller/orders/{oid}/payout/approve").json()["status"] == "payout_pending"
    assert s2.post(f"/api/seller/orders/{oid}/payout/approve").json()["status"] == "settled"          # leader + 2 members
    assert l.get("/api/ledger/verify").json()["valid"]
    assert world.login("+919000000102").get("/api/leader/orders").status_code == 403   # a member is not a leader

def test_dispute_blocks_settlement_until_resolved(world):
    b, l, s = world.login(BUYER), world.login(LEADER), world.login(SELLER)
    gid = b.get("/api/buyer/groups").json()[0]["id"]; oid = b.post("/api/buyer/orders", json={"group_id": gid, "text": ORDER}).json()["id"]
    l.post(f"/api/leader/orders/{oid}/quote", json={}); b.post(f"/api/buyer/orders/{oid}/accept"); l.post(f"/api/leader/orders/{oid}/plan"); l.post(f"/api/leader/orders/{oid}/start")
    l.post(f"/api/leader/orders/{oid}/payout", json={}); assert s.post(f"/api/seller/orders/{oid}/dispute", json={"reason": "count is wrong"}).status_code == 200
    l.post(f"/api/leader/orders/{oid}/payout/approve")
    for ph in ("+919000000101", "+919000000102"): world.login(ph).post(f"/api/seller/orders/{oid}/payout/approve")
    assert l.get("/api/leader/orders").json()[0]["status"] == "payout_pending"
    assert l.post(f"/api/leader/orders/{oid}/disputes/0/resolve", json={"note": "recounted"}).json()["status"] == "settled"

def test_officer_sees_aggregates_only(world):
    b, o = world.login(BUYER), world.login(OFFICER)
    gid = b.get("/api/buyer/groups").json()[0]["id"]; b.post("/api/buyer/orders", json={"group_id": gid, "text": ORDER})
    r = o.get("/api/officer/summary").json(); assert r[0]["orders"] == "<3"            # small cells suppressed
    csv = o.get("/api/officer/export.csv").text
    assert "+91" not in csv and "Lakshmi" not in csv and "Test Buyer" not in csv

def test_whatsapp_bot_roles_and_idempotency(world):
    assert "Choose your language" in bot.handle("whatsapp:+911234567890", "hi")          # OPEN_REGISTRATION=1: a new number starts registration
    assert "Welcome to Sakhi Khata" in bot.handle("whatsapp:" + BUYER, "hi", "SM1")
    assert bot.handle("whatsapp:" + BUYER, "hi", "SM1") is None                        # duplicate delivery ignored
    place()
    assert "Quote Rs 85" in bot.handle("whatsapp:" + LEADER, "quote 1")
    assert "No earnings" in bot.handle("whatsapp:" + SELLER, "earnings")
    c = TestClient(world.app)
    f = {"From": "whatsapp:" + BUYER, "Body": "status", "MessageSid": "SM2"}
    assert "#1" in c.post("/whatsapp", data=f).text

# ---------------- WhatsApp-first flow (the bot is driven exactly as Twilio would drive it) ----------------
PH = {"buyer": BUYER, "leader": LEADER, "officer": OFFICER, **{f"m{k}": f"+91900000010{k}" for k in range(6)}}
PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
def chat(who, text, image=None): return bot.handle("whatsapp:" + PH[who], text, None, {"kind": "image", "photo": image} if image else None)
def say(who_or_phone, text, image=None):
    p = "whatsapp:" + PH.get(who_or_phone, who_or_phone); return bot.handle(p, text, None, {"kind": "image", "photo": image} if image else None)
def place(who="buyer", text=ORDER, name="ABC Stores", product="cloth bags"):
    """Order through the guided conversation. The rule parser never finds the product, so the bot must ask for it."""
    say(who, "hi"); say(who, "1"); say(who, name); r = say(who, text)
    if "What product" in r: r = say(who, product)
    assert "Please confirm" in r, r
    return say(who, "1")
def start_order(decline=False):                          # decline=True: Savitha declines and the leader replans (Padma then does stitching and finishing)
    place(); steps = [("leader", "QUOTE 1"), ("buyer", "ACCEPT 1"), ("leader", "PLAN 1")] + ([("m2", "DECLINE 1"), ("leader", "PLAN 1")] if decline else []) + [("leader", "START 1")]
    for who, t in steps: chat(who, t)
def orders(world, who="leader"): return world.login(PH[who]).get("/api/leader/orders" if who == "leader" else "/api/buyer/orders").json()
def to(world, who): return [m["text"] for m in world.sent if m["phone"] == PH[who]]

def test_whatsapp_full_flow_updates_dashboards(world):
    assert "confirmed and sent" in place()
    assert "Quote Rs 85" in chat("leader", "QUOTE 1")
    assert orders(world)[0]["status"] == "quoted" and any("Quote for order #1" in m for m in to(world, "buyer"))
    assert "price" not in orders(world, "buyer")[0]                                  # buyer view still hides internals
    for who, t in (("buyer", "ACCEPT 1"), ("leader", "PLAN 1"), ("m2", "DECLINE 1"), ("leader", "PLAN 1"), ("leader", "START 1"), ("leader", "PAYOUT 1 lakshmi 4")): chat(who, t)
    assert orders(world)[0]["status"] == "payout_pending"
    assert "4 need rework" in chat("m1", "Maine kitna kamaya, aur kyun?")
    assert world.login(LEADER).get("/api/ledger/verify").json()["valid"]

def test_unknown_number_gets_nothing_when_registration_is_off(world, monkeypatch):
    monkeypatch.setenv("OPEN_REGISTRATION", "0")
    assert "not registered" in bot.handle("whatsapp:+911234567899", "hi")

def test_no_demo_surface_left(world):
    c = TestClient(world.app)
    for path in ("/api/config", "/api/demo", "/api/demo/state", "/live.html", "/3d.html"): assert c.get(path).status_code == 404
    for path in ("/api/auth/demo", "/api/demo/chat", "/api/demo/reset"): assert c.post(path, json={"role": "leader"}).status_code in (404, 405)
    # assert "demo" not in open("../frontend/app/login.html").read().lower() and "demo" not in open("../frontend/index.html").read().lower()
    assert "demo" not in open("../frontend/app/login.html", encoding="utf-8").read().lower() and "demo" not in open("../frontend/index.html", encoding="utf-8").read().lower()
def test_owner_phone_is_bootstrapped_and_can_switch_role(world, monkeypatch):
    import db
    from sqlalchemy import select
    monkeypatch.setenv("OWNER_PHONE", "9315168344"); monkeypatch.setenv("OWNER_NAME", "Owner")
    bootstrap.ensure()
    with db.S() as s: u = s.scalars(select(db.User).where(db.User.phone == "+919315168344")).one(); assert u.role == "leader" and u.group_id
    me = "whatsapp:+919315168344"
    assert "You are now: buyer" in bot.handle(me, "role buyer") and "confirmed and sent" in place(me[len("whatsapp:"):])
    assert "You are now: leader" in bot.handle(me, "ROLE leader") and "Quote Rs 85" in bot.handle(me, "QUOTE 1")
    assert "You are now: seller" in bot.handle(me, "role member")
    assert "Not done" in bot.handle(me, "role boss")
    assert "You are now" not in (bot.handle("whatsapp:" + BUYER, "role leader") or "")      # nobody else can switch roles
    assert world.login("+919000000002").get("/api/me").json()["role"] == "buyer"

def test_whatsapp_self_registration_in_hindi(world):
    me = "whatsapp:+919811122233"
    assert "Choose your language" in bot.handle(me, "hello")
    assert bot.handle(me, "2") == "आपका नाम?"
    assert "कौन हैं" in bot.handle(me, "Asha")
    assert "कौन हैं" in bot.handle(me, "9")                                                # invalid choice: asked again, nothing registered
    assert "हुनर" in bot.handle(me, "1")
    out = bot.handle(me, "2"); assert "स्वागत है, Asha" in out and "DONE" in out
    j = "whatsapp:+919822233344"
    for step in ("hi", "1", "Guest"): bot.handle(j, step)
    out = bot.handle(j, "2"); assert "Welcome, Guest" in out and "ACCEPT" in out
    assert "confirmed and sent" in place(j[len("whatsapp:"):])

def test_language_switch_changes_the_pay_slip_not_the_numbers(world):
    start_order(); chat("leader", "PAYOUT 1 lakshmi 4")
    en = chat("m1", "EARNINGS"); assert "you made" in en and "Rs 1536.00" in en and "4 need rework" in en
    assert "सेट" in chat("m1", "LANG 2") or "हिन्दी" in chat("m1", "LANG 2")
    hi = chat("m1", "EARNINGS"); assert "आपने" in hi and "Rs 1536.00" in hi and "Rs 64.00" in hi
    chat("m1", "LANG 3"); hg = chat("m1", "maine kitna kamaya, aur kyun?"); assert "aapne" in hg and "Rs 1536.00" in hg
    assert "set to English" in chat("m1", "LANG 1") and "you made" in chat("m1", "EARNINGS")

def test_sample_review_photo_privacy_and_one_pending_at_a_time(world):
    start_order()
    assert "sent to the leader" in chat("m1", "SAMPLE 1 first bag")
    assert "still waiting" in chat("m1", "SAMPLE 1")
    assert orders(world)[0]["quality"][0]["status"] == "pending"
    assert any("first stitching sample" in m for m in to(world, "leader"))
    chat("m2", "PASS 1 lakshmi"); assert orders(world)[0]["quality"][0]["status"] == "pending"              # a member's PASS does nothing
    assert "passed" in chat("leader", "PASS 1 lakshmi") and orders(world)[0]["quality"][0]["status"] == "passed"
    assert "(photo)" in chat("m2", "", PIXEL)
    q = orders(world)[0]["quality"][-1]; assert q["has_photo"] and "photo" not in q
    assert world.login(LEADER).get(f"/api/leader/orders/1/quality/{q['i']}/photo").headers["content-type"] == "image/png"
    assert world.login("+919000000101").get("/api/leader/orders/1/quality/0/photo").status_code == 403    # members cannot pull evidence
    b = orders(world, "buyer")[0]; assert "price" not in b and "quality" not in b

def test_rework_recorded_by_leader_becomes_default_pending_pieces(world):
    start_order()
    chat("m1", "SAMPLE 1"); assert "Send REWORK" in chat("leader", "REWORK 1")
    assert "Noted: 4" in chat("leader", "REWORK 1 lakshmi 4 stitching loose")
    assert any("rework" in m for m in to(world, "m1"))
    chat("leader", "PAYOUT 1")
    o = orders(world)[0]; l = next(x for x in o["payout"]["lines"] if x["name"] == "Lakshmi"); a = next(x for x in o["plan"]["assignments"] if x["name"] == "Lakshmi")
    assert l["pending"] == 4 and l["accepted"] == a["units"] - 4
    assert "Rs 64.00" in chat("m1", "EARNINGS")

def test_progress_and_deadline_risk_with_transparent_rule(world):
    start_order(decline=True)
    assert "Not done" in chat("m1", "DONE 1 0")
    assert "0 of 200 pieces finished" in chat("leader", "RISK 1 1")
    chat("m5", "DONE 1 80"); assert "0 of 200 pieces finished" in chat("leader", "RISK 1 1")
    for who, n in (("m0", 200), ("m1", 66), ("m4", 134), ("m4", 112), ("m3", 88)): assert "Recorded" in chat(who, f"DONE 1 {n}")
    r = chat("leader", "RISK 1 1")
    assert "80 of 200" in r and "Slowest stage: stitching (50 pieces/day)" in r and "Short by 70" in r and "you and the group decide" in r
    assert "On time" in chat("leader", "RISK 1 9") and "Not done" in chat("leader", "RISK 1 abc")
    assert "Padma: stitching 134/134" in chat("leader", "PROGRESS 1")
    b = orders(world, "buyer")[0]; assert 0 < b["progress_pct"] < 100 and "plan" not in b and "progress" not in b and "risk" not in b
    assert world.login("+919000000101").post("/api/leader/orders/1/risk", json={}).status_code == 403

def test_escalation_tells_officer_without_names_or_pay(world):
    start_order(); chat("leader", "PAYOUT 1 lakshmi 4"); chat("m2", "DISPUTE 1 count is wrong")
    assert svc.escalate_overdue(minutes=0)[0]["disputes"] == 1
    assert svc.escalate_overdue(minutes=0) == []                                           # raised once, not on every check
    msg = to(world, "officer")[0]
    assert "dispute" in msg and "over 0 min" in msg and "Savitha" not in msg and "Lakshmi" not in msg and "Rs" not in msg
    assert any("flagged to the mission officer" in m for m in to(world, "leader"))
    csv = world.login(OFFICER).get("/api/officer/export.csv"); assert csv.status_code == 200 and "escalated_open" in csv.text

def test_cron_escalation_needs_secret(world, monkeypatch):
    c = TestClient(world.app); assert c.post("/api/cron/escalate").status_code == 404
    monkeypatch.setenv("CRON_SECRET", "s3cret"); assert c.post("/api/cron/escalate", headers={"X-Cron-Secret": "bad"}).status_code == 404
    assert c.post("/api/cron/escalate", headers={"X-Cron-Secret": "s3cret"}).json() == {"raised": []}

def test_webhook_signature_is_enforced_when_token_is_set(world, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok"); monkeypatch.setenv("PUBLIC_URL", "https://x.example")
    c = TestClient(world.app); assert c.post("/whatsapp", data={"From": "whatsapp:" + BUYER, "Body": "status"}).status_code == 403

def test_tamper_is_detected_by_ledger_verify(world):
    import db
    from sqlalchemy import select
    start_order(); l = world.login(LEADER); assert l.get("/api/ledger/verify").json()["valid"]
    with db.S() as s: r = s.scalars(select(db.LedgerEvent).order_by(db.LedgerEvent.i.desc())).first(); r.payload = {**(r.payload or {}), "edited_outside_the_app": True}; s.commit()
    assert l.get("/api/ledger/verify").json()["valid"] is False


# ---------------- guided conversation ----------------
import llm, convo
RICH = {"order": {"product": "Blue cloth bags", "qty": 200, "size": "12 x 14 inches", "colour": "blue", "specs": None, "offered_price": 60, "deadline_days": 20, "delivery_date": "2026-11-15", "location": "Pune", "packaging": None}, "unclear": [], "source": "groq"}
def smart(parts, today=None):
    """Stands in for Groq in tests: the rule parser plus the product name, which only an LLM can read from free text."""
    r = llm._rules(parts); o, un = r; o["product"] = o["product"] or ("cloth bags" if any("cloth bags" in p for p in parts) else None)
    o, un = llm._finish(o, un, __import__("datetime").date(2026, 10, 26)); return {"order": o, "unclear": un, "source": "test"}

def test_hi_shows_role_menu_and_commands_still_work(world):
    b = say("buyer", "hi"); assert "Welcome to Sakhi Khata" in b and "1. Place a new order" in b and "2. Check an existing order" in b and "Manage my assigned work" not in b
    assert "1. Place a new order" in say("leader", "Hello") and "3. Review orders waiting for me" in say("leader", "menu")
    assert "1. Manage my assigned work" in say("m1", "namaste") and "2. Check my earnings" in say("m1", "hi")
    assert "No orders yet" in say("buyer", "STATUS")                                       # typed commands are the fallback
    h = say("leader", "help"); assert "QUOTE <id>" in h and "Send MENU" in h

def test_new_order_asks_name_then_only_what_is_missing_then_confirms(world, monkeypatch):
    monkeypatch.setattr(llm, "extract_details", smart)
    say("buyer", "hi"); assert "buyer's name" in say("buyer", "1")
    r = say("buyer", "ABC Stores"); assert "voice note" in r and "photo of a handwritten order" in r and "PDF" in r
    r = say("buyer", "I need 200 bags"); assert "What product" in r and "price per piece" in r and "How many pieces" not in r      # asks only for what is missing
    r = say("buyer", "cloth bags, Rs 60 each, in 20 days"); assert "Please confirm" in r and "Buyer: ABC Stores" in r and "Quantity: 200 pieces" in r and "₹60 per piece" in r
    assert orders(world, "buyer") == []                                                    # nothing is created before the buyer confirms
    assert "1. Confirm order" in r and "2. Edit details" in r and "3. Cancel order" in r
    assert "What would you like to change" in say("buyer", "2")
    r = say("buyer", "quantity 250, price 65"); assert "250 pieces" in r and "₹65 per piece" in r and "Please confirm" in r
    r = say("buyer", "1"); assert "Order #1 is confirmed" in r and "Test SHG" in r
    o = orders(world, "buyer")[0]; assert o["order"]["qty"] == 250 and o["order"]["buyer_name"] == "ABC Stores" and "price" not in o
    assert any("New order #1 from ABC Stores" in m for m in to(world, "leader"))

def test_single_missing_detail_accepts_a_bare_number(world):
    say("buyer", "hi"); say("buyer", "1"); say("buyer", "ABC"); assert "price per piece" in say("buyer", "200 bags, cloth bags, 5 days")
    say("buyer", "x")                                                                      # not understood: asked again, nothing breaks
    r = say("buyer", "60"); assert "What product" in r or "Please confirm" in r

def test_cancel_creates_nothing(world):
    place_steps = lambda: [say("buyer", x) for x in ("hi", "1", "ABC", ORDER, "cloth bags")]
    place_steps(); assert "cancelled" in say("buyer", "3") and orders(world, "buyer") == []
    place_steps(); assert "cancelled" in say("buyer", "cancel") and orders(world, "buyer") == []
    assert "Welcome" in say("buyer", "hi")

def test_ai_details_voice_photo_and_pdf_are_confirmed_not_assumed(world, monkeypatch):
    monkeypatch.setattr(llm, "extract_details", lambda parts, today=None: RICH)
    say("buyer", "hi"); say("buyer", "1"); say("buyer", "ABC Stores")
    r = bot.handle("whatsapp:" + BUYER, "ek sau bees bag chahiye", None, {"kind": "voice", "url": "https://api.twilio.com/m/1"})
    assert "What I heard" in r and "Blue cloth bags" in r and "15 Nov 2026" in r and "Pune" in r and "12 x 14 inches" in r
    say("buyer", "hi"); say("buyer", "1"); say("buyer", "ABC Stores")                       # a photo that cannot be read is reported, never silently accepted
    monkeypatch.setattr(llm, "read_image", lambda url: None)
    assert "could not read that photo" in bot.handle("whatsapp:" + BUYER, "", None, {"kind": "image", "photo": PIXEL, "photo_hi": PIXEL})
    monkeypatch.setattr(llm, "read_image", lambda url: "200 blue bags Rs 60 by 15 Nov")
    r = bot.handle("whatsapp:" + BUYER, "", None, {"kind": "image", "photo": PIXEL, "photo_hi": PIXEL}); assert "What I read in the photo" in r and "Please confirm" in r
    say("buyer", "hi"); say("buyer", "1"); say("buyer", "ABC Stores")
    assert "could not read any text" in bot.handle("whatsapp:" + BUYER, "", None, {"kind": "pdf", "text": None})
    assert "What I read in the PDF" in bot.handle("whatsapp:" + BUYER, "", None, {"kind": "pdf", "text": "Order: 200 blue bags"})
    assert "confirmed" in say("buyer", "1")
    import db
    with db.S() as s: d = s.get(db.Order, 1).data; assert d["evidence"]["source"] == "pdf" and d["order"]["delivery_date"] == "2026-11-15"   # original source kept with the order

def test_first_message_can_be_an_order_and_state_survives_reload(world, monkeypatch):
    monkeypatch.setattr(llm, "extract_details", smart)
    assert "buyer's name" in say("buyer", ORDER)                                           # an order sent straight away starts the flow
    import importlib; importlib.reload(convo)                                              # state is in the database, not in memory
    assert "What product" in say("buyer", "Sunrise Traders")
    assert "Please confirm" in say("buyer", "cloth bags")

def test_leader_places_order_sees_pricing_and_sends_counteroffer(world):
    for x in ("hi", "1", "ABC Stores", ORDER, "cloth bags"): say("leader", x)
    r = say("leader", "1")
    assert "Order #1 for ABC Stores is recorded" in r and "Minimum sustainable price" in r and "₹84.74" in r and "Shortfall: ₹24.74" in r and "The group decides" in r and "counteroffer of ₹85" in r
    assert "Quote of ₹85" in say("leader", "1") and orders(world)[0]["status"] == "quoted"
    assert "Recorded: the buyer accepted" in say("leader", "ACCEPT 1") and orders(world)[0]["status"] == "accepted"

def test_only_the_buyer_can_accept_a_buyers_order(world):
    place(); say("leader", "QUOTE 1"); assert "Only the buyer" in say("leader", "ACCEPT 1")

def test_leader_review_walks_an_order_through_without_commands(world):
    place(); say("leader", "menu")
    r = say("leader", "3"); assert "#1 Cloth bags × 200 - waiting for the leader's quote" in r
    r = say("leader", "1"); assert "Shortfall" in r and "Send a quote at ₹85" in r and "Delivery:" in r
    assert "Quote of ₹85" in say("leader", "1") and orders(world)[0]["status"] == "quoted"
    say("buyer", "ACCEPT 1"); say("leader", "menu"); say("leader", "3"); r = say("leader", "1"); assert "Propose a work plan" in r
    r = say("leader", "1"); assert "Proposed work" in r and "Approve the plan and start production" in r and "Shobha: cutting" in r
    r = say("leader", "1"); assert "Production started" in r and "Show progress" in r and orders(world)[0]["status"] == "in_production"
    assert any("your task is" in m for m in to(world, "m1"))
    assert "pieces finished" in say("leader", "2")                                         # deadline risk check
    r = say("leader", "3"); assert "Payout proposed" in r and "Approve the payout" in r and orders(world)[0]["status"] == "payout_pending"
    assert "Your approval is recorded" in say("leader", "1")
    assert "Not done" in say("leader", "quote 1")                                              # typed commands still work mid-conversation (order is past the quote stage)

def test_check_existing_order_respects_who_is_asking(world):
    say("buyer", "hi"); assert "no orders yet" in say("buyer", "2")
    place(); place(text="100 bags Rs 90 each in 5 days")
    say("buyer", "hi"); r = say("buyer", "2"); assert "Which order" in r and "#2" in r
    b = say("buyer", "1"); assert "Order #1" in b and "Status: waiting for the leader's quote" in b and "Delivery by:" in b and "Quality" not in b and "Unresolved" not in b and "Pending tasks" not in b
    say("leader", "hi"); assert "Which order" in say("leader", "2")
    l = say("leader", "1"); assert "Unresolved issues: none" in l and "Order #1" in l

def test_member_work_flow_accept_done_issue_and_sample(world):
    place(); say("leader", "QUOTE 1"); say("buyer", "ACCEPT 1"); say("leader", "PLAN 1")
    say("m1", "hi"); r = say("m1", "1"); assert "Your assigned work" in r and "1. Accept a task" in r
    assert "You accepted your task in order #1" in say("m1", "1") and any("Lakshmi accepted" in m for m in to(world, "leader"))
    say("m1", "menu"); say("m1", "1"); assert "None of your orders is in production" in say("m1", "2")         # cannot report work before it starts
    say("leader", "START 1")
    say("m1", "menu"); say("m1", "1"); assert "How many pieces" in say("m1", "2"); assert "Recorded" in say("m1", "10")
    say("m1", "menu"); say("m1", "1"); assert "tell me the problem" in say("m1", "5"); assert "told your leader" in say("m1", "need more thread")
    assert any("need more thread" in m for m in to(world, "leader"))
    say("m1", "menu"); say("m1", "1"); assert "send a clear photo" in say("m1", "3")
    assert "(photo)" in say("m1", "first piece", PIXEL) and orders(world)[0]["quality"][0]["status"] == "pending"
    assert "(photo)" in say("m2", "", PIXEL)                                               # a photo with no menu step is still a sample

def test_member_earnings_approve_or_dispute_in_conversation(world):
    start_order(); chat("leader", "PAYOUT 1 lakshmi 4")
    say("m1", "hi"); e = say("m1", "2"); assert "Order #1" in e and "you made" in e and "1. I agree, approve it" in e
    assert "I am sorry" in say("m1", "2") and "recorded" in say("m1", "the count is wrong")
    assert orders(world)[0]["disputes"][0]["open"] and "count is wrong" in orders(world)[0]["disputes"][0]["reason"]
    say("m2", "hi"); say("m2", "2"); assert "Thank you" in say("m2", "1")

def test_officer_is_pointed_to_the_dashboard(world):
    assert "Please use the web dashboard" in say("officer", "hi")

def test_a_crash_in_the_conversation_never_leaves_the_user_stuck(world, monkeypatch):
    monkeypatch.setattr(convo, "_welcome", lambda role: 1 / 0)
    assert "Send MENU to start again" in say("buyer", "hi")
