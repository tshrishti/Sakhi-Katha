"""The shared workflow: ten tabs are views of one model, and WhatsApp and the dashboard stay in step."""
import time, db, bot, svc, hub, ops, workflow as W
from fastapi.testclient import TestClient
from test_app import LEADER, BUYER, SELLER, ORDER, PH, say, chat, place, start_order, orders, to, _in_production, _settled
TABS = W.TABS
M1, M2, M3 = PH["m0"], PH["m1"], PH["m2"]

def view(c, tab): r = c.get(f"/api/leader/view/{tab}"); assert r.status_code == 200, (tab, r.text); return r.json()

# ---------- pure workflow ----------
def test_workflow_pending_and_health_are_pure():
    f = dict(id=3, status="submitted", qty=100, created=1, last_event_ts=5, underpriced=True, suggested="85")
    p = W.pending(f); assert p[0]["kind"] == "quote" and p[0]["cmd"] == "QUOTE 3" and p[0]["owner"] == "leader"
    f = dict(id=3, status="in_production", qty=100, finished=0, late=True, days_left=-2, due=9, disputes_open=[], samples_pending=[{"i": 0, "member": 1, "name": "A", "stage": "stitching", "ts": 1, "flags": 2}], all_done=False)
    kinds = [i["kind"] for i in W.pending(f)]; assert kinds[:2] == ["late", "sample"]                      # late outranks a waiting sample
    assert W.health(f)[0] == "late" and W.health({"status": "closed"})[0] == "done" and W.health({"status": "blocked"})[0] == "blocked"
    assert W.tabs_for("progress.reported") == ["overview", "orders", "team"] and "quality" in W.tabs_for("quality.sample_submitted")

# ---------- access ----------
def test_hub_is_leader_only_and_every_tab_renders(world):
    start_order(); l = world.login(LEADER)
    for t in TABS:
        d = view(l, t); assert set(W.TABS) <= set(d["badges"]) and "cursor" in d
    for who in (BUYER, SELLER):
        c = world.login(who)
        for path in ("/api/leader/view/overview", "/api/leader/events", "/api/leader/orders/1/detail", "/api/leader/activity"): assert c.get(path).status_code == 403
    assert l.get("/api/leader/view/nope").status_code == 404

# ---------- WhatsApp -> dashboard, live ----------
def test_whatsapp_progress_reaches_dashboard_with_the_right_tabs(world):
    start_order(); l = world.login(LEADER)
    cur = l.get("/api/leader/events").json()["cursor"]
    assert l.get(f"/api/leader/events?after={cur}").json()["events"] == []                                     # quiet means quiet
    chat("m0", "DONE 1 20")
    ev = l.get(f"/api/leader/events?after={cur}").json()
    assert ev["events"] and set(ev["tabs"]) == {"overview", "orders", "team"} and "reported" in ev["events"][-1]["text"]
    team = view(l, "team"); sh = next(m for m in team["members"] if m["name"] == "Shobha")
    assert sh["status"] in ("working", "behind") and sh["week"]["pieces"] == 20 and sh["last_report"]
    o = next(c for c in view(l, "orders")["orders"] if c["id"] == 1); assert o["progress"]["pct"] > 0 and o["status"] == "in_production"
    assert view(l, "overview")["kpis"]["active_orders"] == 1

def test_sample_flows_through_quality_alerts_and_clears_on_pass(world):
    start_order(); l = world.login(LEADER); say("m1", "SAMPLE 1 first piece")
    q = view(l, "quality"); assert len(q["queue"]) == 1 and q["queue"][0]["name"] == "Lakshmi"
    al = view(l, "alerts"); assert any(a["kind"] == "sample" for a in al["alerts"]) and al["badges"]["quality"] == 1
    assert l.post("/api/leader/orders/1/quality", json={"member": q["queue"][0]["member"], "decision": "pass"}).status_code == 200
    assert view(l, "quality")["queue"] == [] and not any(a["kind"] == "sample" for a in view(l, "alerts")["alerts"])
    assert view(l, "quality")["stats"]["by_member"][0]["rate"] == 100

def test_alerts_whatsapp_today_and_dashboard_needs_match(world):
    place(); l = world.login(LEADER)
    n = view(l, "overview")["needs"]; assert n[0]["kind"] == "quote"
    t = chat("leader", "TODAY"); assert "need you" in t and "QUOTE 1" in t
    chat("leader", "QUOTE 1"); assert view(l, "overview")["needs"] == [] and "Nothing needs you" in chat("leader", "TODAY")
    o = view(l, "orders")["orders"][0]; assert o["next"]["owner"] == "buyer" and o["needs_you"] is False

# ---------- money in ----------
def test_money_in_records_reminds_and_buyer_sees_totals_only(world):
    start_order(); l = world.login(LEADER); b = world.login(BUYER)
    pm = view(l, "payouts"); r = pm["money_in"][0]; assert r["total"] == "17000.00" and r["balance"] == "17000.00" and r["pay_status"] == "unpaid"
    assert l.post("/api/leader/orders/1/money", json={"amount": 99999999}).status_code == 422
    assert l.post("/api/leader/orders/1/money", json={"amount": 5000}).json()["status"] == "advance"
    r = view(l, "payouts")["money_in"][0]; assert r["paid"] == "5000.00" and r["advance"] == "5000.00" and r["balance"] == "12000.00"
    assert view(l, "overview")["kpis"]["money_due"] == "12000.00"
    world.sent.clear(); assert l.post("/api/leader/orders/1/money/remind").status_code == 200 and any("Reminder" in m for m in to(world, "buyer"))
    assert l.post("/api/leader/orders/1/money/remind").status_code == 409                                    # not spammed
    mine = b.get("/api/buyer/orders").json()[0]["money"]; assert set(mine) == {"total", "paid", "balance", "status", "estimated"}
    assert world.login(SELLER).post("/api/leader/orders/1/money", json={"amount": 1}).status_code == 403
    assert "Recorded" in chat("leader", "PAID 1 12000") and view(l, "payouts")["money_in"][0]["pay_status"] == "paid"
    assert "No buyer owes" in chat("leader", "DUE")

def test_overdue_balance_becomes_an_alert_and_a_nudge(world):
    start_order(); l = world.login(LEADER); l.put("/api/leader/orders/1/money/due", json={"due_date": "2020-01-01"})
    assert any(a["kind"] == "payment_overdue" for a in view(l, "alerts")["alerts"]) and view(l, "buyers")["buyers"][0]["overdue"]
    assert view(l, "overview")["badges"]["buyers"] == 1
    world.sent.clear(); assert svc.run_reminders()["payment_due"] == 1 and any("owes" in m for m in to(world, "leader"))

def test_margin_estimate_is_labelled_and_adds_up(world):
    start_order(); l = world.login(LEADER); m = view(l, "payouts")["margins"][0]
    assert m["estimate"] and float(m["revenue"]) - float(m["member_pay"]) - float(m["materials"]) == float(m["margin"])

# ---------- private notes ----------
def test_notes_are_private_and_not_copied_into_the_ledger(world):
    start_order(); l = world.login(LEADER); l.post("/api/leader/orders/1/notes", json={"text": "buyer is a cousin, go easy"})
    assert l.get("/api/leader/orders/1/detail").json()["notes"][0]["text"].startswith("buyer is")
    assert "cousin" not in str(world.login(BUYER).get("/api/buyer/orders").json())
    with db.S() as s: assert "cousin" not in str([e.payload for e in s.query(db.LedgerEvent)])
    assert l.post("/api/leader/orders/1/notes", json={"text": "  "}).status_code == 422

# ---------- reassigning work ----------
def test_reassign_moves_pieces_validates_and_notifies(world):
    start_order(); l = world.login(LEADER); d = l.get("/api/leader/orders/1/detail").json(); ms = {m["name"]: m["id"] for m in d["work"]["members"]}
    row = next(r for r in d["work"]["rows"] if r["stage"] == "stitching" and r["name"] == "Lakshmi"); chat("m1", "DONE 1 10")
    body = lambda **k: {"from": ms["Lakshmi"], "to": ms["Padma"], "stage": "stitching", "pieces": 20, **k}
    assert l.post("/api/leader/orders/1/reassign", json=body(**{"to": ms["Rukmini"]})).status_code == 422       # packing only: no stitching skill
    assert l.post("/api/leader/orders/1/reassign", json=body(pieces=row["units"])).status_code == 422          # cannot move what she already did
    world.sent.clear(); r = l.post("/api/leader/orders/1/reassign", json=body(pieces=5)); assert r.status_code == 200
    d = l.get("/api/leader/orders/1/detail").json(); rows = {(x["name"], x["stage"]): x for x in d["work"]["rows"]}
    assert rows[("Lakshmi", "stitching")]["units"] == row["units"] - 5
    assert any("more stitching" in m for m in to(world, "m4")) and any("moved to another member" in m for m in to(world, "m1"))
    assert sum(x["units"] for x in d["work"]["rows"] if x["stage"] == "stitching") == 200
    assert l.get("/api/ledger/verify").json()["valid"]
    assert "Moved" in chat("leader", f"REASSIGN 1 lakshmi padma 3 stitching")

# ---------- member issues ----------
def test_issue_alert_resolve_from_dashboard_and_whatsapp(world):
    start_order(); l = world.login(LEADER); assert "told your leader" in chat("m1", "ISSUE 1 need more thread")
    a = [x for x in view(l, "alerts")["alerts"] if x["kind"] == "issue"]; assert a and "thread" in a[0]["detail"] and view(l, "alerts")["badges"]["team"] == 1
    assert l.post("/api/leader/orders/1/issues/0/resolve", json={"note": "buying today"}).status_code == 200
    assert not [x for x in view(l, "alerts")["alerts"] if x["kind"] == "issue"] and any("sorted" in m for m in to(world, "m1"))
    s = world.login(M2); assert s.post("/api/seller/orders/1/issue", json={"text": "late"}).status_code == 200
    assert "sorted" in chat("leader", "FIXED 1 ok")

# ---------- placing an order for a buyer, repeating it, buyer profile ----------
def test_leader_places_order_for_a_buyer_and_repeats_it(world):
    l = world.login(LEADER)
    r = l.post("/api/leader/orders", json={"buyer_name": "Rani Stores", "buyer_phone": "9876543210", "product": "cloth bags", "qty": 100, "offered_price": 70, "deadline_days": 8, "location": "Market Road", "specs": "jute handle"})
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    assert l.post("/api/leader/orders", json={"buyer_name": "", "product": "x", "qty": 1, "offered_price": 1, "deadline_days": 1}).status_code == 422
    assert l.post("/api/leader/orders", json={"buyer_name": "Z", "buyer_phone": PH["m0"], "product": "bag", "qty": 1, "offered_price": 1, "deadline_days": 2}).status_code == 409   # a member's number
    bs = view(l, "buyers")["buyers"]; assert bs[0]["name"] == "Rani Stores" and bs[0]["phone"] == "+919876543210"
    p = l.get("/api/leader/buyers/profile", params={"key": bs[0]["key"]}).json(); assert p["addresses"] == ["Market Road"] and p["specs"][0]["specs"] == "jute handle" and "not shared with members" in p["phone_visibility"]
    l.put("/api/leader/buyers/profile", json={"key": bs[0]["key"], "notes": "pays on Fridays"}); assert l.get("/api/leader/buyers/profile", params={"key": bs[0]["key"]}).json()["notes"] == "pays on Fridays"
    r2 = l.post("/api/leader/orders/1/repeat", json={"qty": 150}); assert r2.json()["order"]["qty"] == 150 and r2.json()["order"]["specs"] == "jute handle"
    assert l.post("/api/leader/orders/1/quote", json={}).status_code == 200 and l.post("/api/leader/orders/1/accept").json()["status"] == "accepted"      # leader records acceptance for an order she placed
    assert world.login(BUYER).get("/api/leader/buyers/profile", params={"key": "x"}).status_code == 403

def test_registered_buyers_phone_is_not_visible_to_members_and_rating(world):
    b, _l, oid = _settled(world); bs = view(_l, "buyers")["buyers"]; assert bs[0]["registered"] and bs[0]["phone"] == BUYER
    assert world.login(M1).get("/api/leader/view/buyers").status_code == 403
    assert "phone" not in str(b.get("/api/buyer/orders").json()).lower().replace("telephone", "")
    qty = b.get("/api/buyer/orders").json()[0]["order"]["qty"]
    assert b.post(f"/api/buyer/orders/{oid}/rate", json={"stars": 5}).status_code == 409                        # only after delivery
    _l.post(f"/api/leader/orders/{oid}/ready", json={"counted": qty}); _l.post(f"/api/leader/orders/{oid}/dispatch", json={"courier": "van"}); b.post(f"/api/buyer/orders/{oid}/signoff", json={"received": qty})
    assert b.post(f"/api/buyer/orders/{oid}/rate", json={"stars": 9}).status_code == 422
    assert b.post(f"/api/buyer/orders/{oid}/rate", json={"stars": 4}).status_code == 200 and b.post(f"/api/buyer/orders/{oid}/rate", json={"stars": 5}).status_code == 409
    row = view(_l, "buyers")["buyers"][0]; assert row["avg_rating"] == 4.0 and row["settled"] == 1 and row["on_time_rate"] == 100

# ---------- team: status, capacity, can we take it, messages, manage ----------
def test_team_status_behind_when_no_reports(world):
    start_order(); l = world.login(LEADER)
    with db.S() as s:
        o = s.get(db.Order, 1); o.data = {**o.data, "due_at": time.time() + 3600}; s.commit()                      # one hour left and nothing reported
    t = view(l, "team"); assert t["counts"]["behind"] >= 1

def test_capacity_calendar_and_can_we_take_this_order(world):
    start_order(); l = world.login(LEADER); cal = view(l, "team")["calendar"]; assert len(cal["dates"]) == 7 and len(cal["rows"]) == 6
    ok = l.post("/api/leader/capacity-check", json={"product": "cloth_bag", "qty": 20, "days": 30}).json(); assert ok["ok"] and ok["stages"]
    no = l.post("/api/leader/capacity-check", json={"product": "cloth_bag", "qty": 5000, "days": 2}).json()
    assert not no["ok"] and no["max_qty_by_deadline"] < 5000 and (no["earliest_days"] is None or no["earliest_days"] > 2)
    assert l.post("/api/leader/capacity-check", json={"product": "cloth_bag", "qty": 0, "days": 2}).status_code == 422

def test_messages_templates_and_delivery_status(world, monkeypatch):
    l = world.login(LEADER); out = {}
    monkeypatch.setattr(ops.notify, "send_ex", lambda phone, text: out.setdefault(phone, text) and {"ok": True, "sid": "SM" + phone[-3:], "reason": None})
    r = l.post("/api/leader/messages", json={"to": "all", "template": "daily"}).json(); assert len(r["results"]) == 6 and all(x["status"] == "sent" for x in r["results"])
    assert l.post("/api/leader/messages", json={"to": "all", "text": ""}).status_code == 422 and l.post("/api/leader/messages", json={"to": "all", "template": "zzz"}).status_code == 422
    assert ops.message_status("SM100", "delivered") and ops.message_status("SM100", "read") and ops.message_status("SM100", "sent")          # never moves backwards
    st = {m["to"]: m["status"] for m in view(l, "team")["messages"]}; assert st["Shobha"] == "read" and st["Lakshmi"] == "sent"
    c = TestClient(world.app); assert c.post("/whatsapp/status", data={"MessageSid": "SM101", "MessageStatus": "failed"}).status_code == 204
    assert [m["status"] for m in view(l, "team")["messages"] if m["to"] == "Lakshmi"] == ["failed"]
    assert "Message sent" in chat("leader", "MSG lakshmi please call me")

def test_manage_members_and_cannot_deactivate_someone_with_work(world):
    start_order(); l = world.login(LEADER); ms = {m["name"]: m["id"] for m in view(l, "team")["members"]}
    assert l.put(f"/api/leader/members/{ms['Shobha']}", json={"active": False}).status_code == 409                # she is on order #1
    assert l.put(f"/api/leader/members/{ms['Shobha']}", json={"capacity": 90, "skills": ["Cutting", "packing"]}).status_code == 200
    m = next(x for x in view(l, "team")["members"] if x["name"] == "Shobha"); assert m["capacity"] == 90 and m["skills"] == ["cutting", "packing"]
    assert l.put(f"/api/leader/members/{ms['Shobha']}", json={"capacity": -1}).status_code == 422
    assert l.post("/api/leader/members", json={"name": "New", "phone": "9123456780", "skills": ["packing"], "capacity": 10}).status_code == 200
    assert l.post("/api/leader/members", json={"name": "", "phone": "9123456781", "capacity": 10}).status_code == 422
    det = l.get(f"/api/leader/members/{ms['Shobha']}").json(); assert "earnings" in det and "reports" in det
    other = world.login(M2); assert other.put(f"/api/leader/members/{ms['Shobha']}", json={"capacity": 1}).status_code == 403

def test_fair_work_history_rotates_the_planner(world):
    b, l, oid = _settled(world); f = view(l, "team")["fair"]; assert f["orders_considered"] == 1 and sum(r["share_pct"] for r in f["rows"]) in range(98, 101)
    top = max(f["rows"], key=lambda r: float(r["amount"]))["name"]
    gid = b.get("/api/buyer/groups").json()[0]["id"]; o2 = b.post("/api/buyer/orders", json={"group_id": gid, "text": ORDER}).json()["id"]
    l.post(f"/api/leader/orders/{o2}/quote", json={}); b.post(f"/api/buyer/orders/{o2}/accept"); p = l.post(f"/api/leader/orders/{o2}/plan").json()
    stitchers = [x["name"] for x in p["plan"]["assignments"] if x["stage"] == "stitching"]
    assert p["plan"]["feasible"] and stitchers                                                                       # rotation never breaks feasibility

# ---------- quality, delivery, pricing, materials ----------
def test_delivery_pipeline_and_calendar(world):
    b, l, oid = _settled(world); d = view(l, "delivery"); assert next(p for p in d["pipeline"] if p["key"] == "count")["count"] == 1 and len(d["calendar"]) == 15
    qty = b.get("/api/buyer/orders").json()[0]["order"]["qty"]; l.post(f"/api/leader/orders/{oid}/ready", json={"counted": qty - 5})
    c = next(p for p in view(l, "delivery")["pipeline"] if p["key"] == "packed")["orders"][0]; assert c["packed"] == qty - 5 and c["owed"] == qty
    assert view(l, "overview")["needs"][0]["kind"] == "dispatch" and view(l, "alerts")["badges"]["delivery"] == 1

def test_pricing_calculator_and_underpriced_log(world):
    l = world.login(LEADER); b = world.login(BUYER); gid = b.get("/api/buyer/groups").json()[0]["id"]
    b.post("/api/buyer/orders", json={"group_id": gid, "text": "100 bags Rs 40 each in 5 days"})
    p = view(l, "pricing"); assert p["underpriced"] and p["underpriced"][0]["gap"] and p["products"]
    c = l.post("/api/leader/price-calc", json={"product": "cloth_bag", "qty": 200, "offered": 60}).json(); assert c["floor"] == "84.74" and c["below_floor"] and c["suggested_counter"] == "85"
    assert l.post("/api/leader/price-calc", json={"product": "cloth_bag", "qty": 0, "offered": 60}).status_code == 422

def test_materials_stock_shortfall_purchase_and_actual_cost(world):
    start_order(); l = world.login(LEADER)
    mid = l.post("/api/leader/materials", json={"name": "Cloth", "unit": "m", "qty": 10, "per_piece": {"cloth_bag": 0.5}}).json()["id"]
    v = view(l, "materials"); assert v["shopping"][0]["short"] == 90.0 and any(a["kind"] == "material" for a in view(l, "alerts")["alerts"]) and view(l, "alerts")["badges"]["materials"] == 1
    assert l.post("/api/leader/purchases", json={"material_id": mid, "qty": 100, "cost": 3000, "supplier_name": "Ravi Textiles"}).status_code == 200
    v = view(l, "materials"); assert v["shopping"] == [] and v["suppliers"][0]["name"] == "Ravi Textiles" and v["materials"][0]["qty"] == 110.0 and v["materials"][0]["avg_price"] == 30.0
    assert l.post("/api/leader/orders/1/materials", json={"material_id": mid, "qty": 1000}).status_code == 409
    assert l.post("/api/leader/orders/1/materials", json={"material_id": mid, "qty": 50}).json()["cost"] == 1500.0
    pa = view(l, "materials")["plan_vs_actual"][0]; assert pa["actual"] == "1500.00" and pa["planned"] == "3000.00" and pa["basis"] == "planned"
    assert "Cloth" in chat("leader", "STOCK")

# ---------- activity, ledger, reminders, prefs ----------
def test_activity_filters_csv_timeline_and_group_scoping(world):
    start_order(); l = world.login(LEADER); chat("m0", "DONE 1 5")
    a = l.get("/api/leader/activity", params={"order": 1, "type": "progress"}).json(); assert a["rows"] and all(r["type"].startswith("progress") for r in a["rows"])
    csv = l.get("/api/leader/activity.csv").text; assert csv.startswith("event,time,type") and "Shobha" in csv
    t = l.get("/api/leader/orders/1/timeline").json(); assert t["ledger"]["valid"] and t["rows"][0]["type"] == "order.submitted"
    assert l.get("/api/leader/orders/999/detail").status_code == 404
    al = view(l, "alerts"); assert len(al["rules"]) >= 6 and "not shared" in al["rules"][-1]["rule"]

def test_stale_rule_and_alert_preference(world):
    start_order(); l = world.login(LEADER)
    with db.S() as s: o = s.get(db.Order, 1); o.data = {**o.data, "due_at": time.time() + 5 * 86400}; s.commit()
    far = time.time() + 30 * 3600; world.sent.clear()
    assert svc.run_reminders(now=far)["stale"] == 1
    l.put("/api/leader/prefs", json={"whatsapp_alerts": False}); world.sent.clear(); chat("m1", "ISSUE 1 broken needle")
    assert not to(world, "leader") and any(a["kind"] == "issue" for a in view(l, "alerts")["alerts"])               # muted on WhatsApp, still on the dashboard

def test_search_finds_orders_members_and_buyers(world):
    start_order(); l = world.login(LEADER); r = l.get("/api/leader/search", params={"q": "lakshmi"}).json(); assert r[0]["type"] == "member"
    assert any(x["type"] == "order" for x in l.get("/api/leader/search", params={"q": "#1"}).json()) and any(x["type"] == "buyer" for x in l.get("/api/leader/search", params={"q": "abc"}).json())

def test_typed_delivery_commands_now_reach_their_handlers(world):
    b, l, oid = _settled(world); qty = b.get("/api/buyer/orders").json()[0]["order"]["qty"]
    assert "Count recorded" in chat("leader", f"READY {oid} {qty}") and "Dispatched" in chat("leader", f"DISPATCH {oid} bus AB1")
    r = say("buyer", f"RECEIVED {oid} {qty}"); assert "received" in r and f"RATE {oid}" in r
    assert "Thank you" in say("buyer", f"RATE {oid} 5")
