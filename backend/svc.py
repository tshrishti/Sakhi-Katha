"""Business rules shared by the REST dashboards and the WhatsApp bot. Every function checks the caller's role and scope."""
import math, os, time
from sqlalchemy import select
import db, engine as E, i18n, llm, notify, screening
import defaults, ratecards

LED = db.DBLedger()
QUORUM = int(os.getenv("PAYOUT_MEMBER_QUORUM", "2"))   # members who must approve besides the leader
ALL_STAGES = list(dict.fromkeys(st["name"] for t in defaults.TEMPLATES.values() for st in t["stages"]))
def _card(o):
    """The rate card this order was priced on (snapshot). Orders made before rate cards existed use the cloth-bag default."""
    return o.data.get("rate_card") or ratecards.default_card(defaults.DEFAULT_PRODUCT)
def _snapshot(s, o):
    od = o.data["order"]; key = defaults.detect(od.get("product")) or defaults.detect((o.data.get("evidence") or {}).get("text", "")) or o.data.get("product_key") or defaults.DEFAULT_PRODUCT
    _set(o, product_key=key, rate_card=ratecards.active(s, o.group_id, key))

POST_PAY = ("settled", "ready_for_delivery", "dispatched", "partially_delivered", "closed")   # money is settled; delivery states follow
DELIVERY_OPEN = ("settled", "ready_for_delivery", "dispatched", "partially_delivered")
CANCELLABLE = ("submitted", "quoted", "accepted", "planned")
def _dv(o):
    d = o.data.get("delivery") or {}
    return {"shipments": d.get("shipments", []), "delivered": d.get("delivered", 0), "qc": d.get("qc")}
def _dview(o):
    d, q = _dv(o), o.data["order"]["qty"]
    return {"ordered": q, "delivered": d["delivered"], "remaining": max(q - d["delivered"], 0), "qc": d["qc"],
            "blocked": o.data.get("blocked"), "cancelled": o.data.get("cancelled"),
            "shipments": [{k: x.get(k) for k in ("i", "qty", "courier", "tracking", "date", "note", "received", "signoff", "ts")} | {"has_photo": bool(x.get("photo"))} for x in d["shipments"]]}

class Err(Exception):
    def __init__(self, code, msg): self.code, self.msg = code, msg

def _log(s, o, type_, user, payload=None): LED.append(type_, payload or {}, f"{user['role']}:{user['id']}", o.id, s=s)
def _set(o, status=None, **kw):
    o.data = {**o.data, **kw}                           # reassign so the JSON column is saved
    if status: o.status = status
def _need(o, *st):
    if o.status not in st: raise Err(409, f"Order is '{o.status}', expected {' or '.join(st)}")
def _order(s, oid, user):
    o = s.get(db.Order, oid)
    ok = o and ((user["role"] == "buyer" and o.buyer_id == user["id"]) or (user["role"] in ("leader", "seller") and o.group_id == user["group_id"]))
    if not ok: raise Err(404, "Order not found")
    return o
def _tell(s, group_id, role, text):
    for u in s.scalars(select(db.User).where(db.User.group_id == group_id, db.User.role == role, db.User.active == True)):  # noqa: E712
        notify.send(u.phone, text)
def _view(s, o, user):
    d = o.data
    v = {"id": o.id, "status": o.status, "order": d["order"], "unclear": d.get("unclear", []), "quote": d.get("quote"), "product": (d.get("rate_card") or {}).get("product", defaults.DEFAULT_PRODUCT), "delivery": _dview(o), "due_at": o.data.get("due_at"), "days_left": _days_left(o), "task_accepted": o.data.get("task_accepted", [])}
    pg = _progress(o)
    if user["role"] == "buyer":                         # buyers never see cost, plan, members or pay: only how far along the work is
        g = s.get(db.Group, o.group_id); return {**v, "group": g.name if g else "", "progress_pct": pg["pct"]}
    q = [{**{k: x[k] for k in x if k != "photo"}, "has_photo": bool(x.get("photo"))} for x in d.get("quality", [])]
    return {**v, "rate_card": {k: (d.get("rate_card") or ratecards.default_card(defaults.DEFAULT_PRODUCT)).get(k) for k in ("product", "version", "source")}, "price": d.get("price"), "plan": d.get("plan"), "payout": d.get("payout"), "approvals": d.get("approvals"),
            "disputes": d.get("disputes", []), "declined": d.get("declined", []), "quality": q, "progress": pg, "risk": d.get("risk"),
            "checklist": _checklist(o)}
def _checklist(o):
    """The buyer-specification checklist. Orders made before this feature get one built from their details."""
    return o.data.get("checklist") or screening.build_checklist(o.data["order"])
def _lines(o): return (o.data.get("payout") or {}).get("lines", [])
def _key(x): return f"{x['member']}:{x['stage']}"
def _lang(s, uid):
    u = s.get(db.User, uid); return (u.lang if u and u.lang else "en")
def _leader_only(user):
    if user["role"] != "leader": raise Err(403, "Only the group leader can do this")
def _n_members(o): return len({x["member"] for x in (o.data.get("plan") or {}).get("assignments", [])})
def _days_left(o, now=None):
    """Whole days until the real delivery date (negative = overdue). None until work has started."""
    due = o.data.get("due_at")
    return None if due is None else math.ceil((due - (time.time() if now is None else now)) / 86400)
def _progress(o):
    """Pieces reported done per task. Reported by members, so it is an estimate until the leader's quality check."""
    pr, a = o.data.get("progress", {}), (o.data.get("plan") or {}).get("assignments", [])
    rows = [{"member": x["member"], "name": x["name"], "stage": x["stage"], "units": x["units"], "done": min(int(pr.get(_key(x), 0)), x["units"])} for x in a]
    tot, by = sum(r["units"] for r in rows), {}
    for r in rows: by[r["stage"]] = by.get(r["stage"], 0) + r["done"]
    return {"rows": rows, "finished": min(by.values()) if by else 0, "needed": o.data["order"]["qty"],      # a piece is finished only when it has passed EVERY stage
            "pct": int(100 * sum(r["done"] for r in rows) / tot) if tot else 0}

# ---- buyer ----
def groups():
    with db.S() as s:
        out = []
        for g in s.scalars(select(db.Group)):
            n = sum(1 for o in s.scalars(select(db.Order).where(db.Order.group_id == g.id)) if o.status == "closed")
            out.append({"id": g.id, "name": g.name, "village": g.village, "orders_delivered": n})
        return out
def submit_order(user, group_id, text, pre=None):
    """pre = details already extracted and confirmed in the WhatsApp conversation: {"order": {...}, "unclear": [...], "evidence": {...}}."""
    ex = {"order": pre["order"], "unclear": pre.get("unclear", [])} if pre else llm.extract_order(text); od = ex["order"]
    missing = [k for k in ("qty", "offered_price", "deadline_days") if od.get(k) is None]
    if missing: raise Err(422, "Could not find: " + ", ".join(missing) + ". Please include quantity, price per piece and days.")
    with db.S() as s:
        g = s.get(db.Group, group_id)
        if not g: raise Err(404, "Group not found")
        data = {"order": od, "unclear": ex["unclear"], "declined": [], "disputes": [], "checklist": screening.build_checklist(od)}
        if pre and pre.get("evidence"): data["evidence"] = pre["evidence"]           # original text / transcript / photo / PDF text the order was read from
        if user["role"] == "leader": data["on_behalf"] = True                          # the leader placed it for a buyer (named in the order)
        o = db.Order(group_id=g.id, buyer_id=user["id"], status="submitted", created=time.time(), data=data)
        data["product_key"] = defaults.detect(od.get("product")) or defaults.detect(text) or defaults.DEFAULT_PRODUCT
        data["rate_card"] = ratecards.active(s, g.id, data["product_key"])
        s.add(o); s.flush(); _log(s, o, "order.submitted", user, {**od, "rate_card": {"product": data["rate_card"]["product"], "version": data["rate_card"]["version"]}}); s.commit()
        if user["role"] != "leader":
            what = f"{od.get('product') or 'order'} " if od.get("buyer_name") or od.get("product") else ""
            _tell(s, g.id, "leader", f"New order #{o.id}{' from ' + od['buyer_name'] if od.get('buyer_name') else ''}: {what}{od['qty']} pcs at Rs {od['offered_price']}, {od['deadline_days']} days. Send QUOTE {o.id}, or send MENU to review it.")
        return _view(s, o, user)
def my_orders(user):
    with db.S() as s:
        q = select(db.Order).where(db.Order.buyer_id == user["id"] if user["role"] == "buyer" else db.Order.group_id == user["group_id"])
        return [_view(s, o, user) for o in s.scalars(q.order_by(db.Order.id.desc()))]
def accept_quote(user, oid):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "quoted")
        if user["role"] == "leader" and not o.data.get("on_behalf"): raise Err(403, "Only the buyer can accept this quote")
        _set(o, "accepted"); _log(s, o, "quote.accepted", user, {"quote": o.data["quote"]}); s.commit()
        _tell(s, o.group_id, "leader", f"Buyer accepted order #{o.id} at Rs {o.data['quote']}. Send PLAN {o.id}.")
        return _view(s, o, user)

# ---- leader ----
def review(user, oid):
    """Read-only price check and feasibility for the leader. Saves nothing and commits the group to nothing."""
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); od = o.data["order"]; dec = o.data.get("declined", [])
        rc = _card(o); p = E.price_check(od["qty"], **rc["cost"], offered=od["offered_price"])
        ms = [m for m in s.scalars(select(db.User).where(db.User.group_id == o.group_id, db.User.role == "seller", db.User.active == True)) if m.id not in dec]  # noqa: E712
        a = E.allocate(od["qty"], rc["stages"], [{"id": m.id, "name": m.name, "skills": m.skills or [], "capacity": m.capacity or 1} for m in ms], days=od["deadline_days"]) if ms else {"feasible": False, "issues": ["No available members in the group"]}
        return {"rate_card": {"product": rc["product"], "version": rc["version"], "source": rc["source"]}, "price": p, "feasible": a["feasible"], "issues": a["issues"], "options": _options(od, rc, p, ms, dec, a)}
def _options(od, rc, p, ms, dec, a):
    """Revised price / smaller quantity / later deadline when the order is not viable. Empty when it is."""
    if a["feasible"]: return []
    if not ms: return [{"kind": "recruit", "label": "No members are available in the group. Add or reactivate a member first."}]
    return E.feasibility_options(od["qty"], rc["stages"], [{"id": m.id, "name": m.name, "skills": m.skills or [], "capacity": m.capacity or 1} for m in ms],
                                 od["deadline_days"], p["per_unit"]["floor"], od["offered_price"])
def quote(user, oid, counter=None):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "submitted", "quoted"); od = o.data["order"]
        _snapshot(s, o); p = E.price_check(od["qty"], **_card(o)["cost"], offered=od["offered_price"])      # price on the group's latest approved card, then pin it
        try: c = str(counter if counter not in (None, "") else p["suggested_counter"]); assert float(c) > 0
        except Exception: raise Err(422, "Counter price must be a positive number")
        _set(o, "quoted", price=p, quote=c); _log(s, o, "quote.sent", user, {"quote": c, "floor": p["per_unit"]["floor"], "rate_card_version": _card(o)["version"], "product": _card(o)["product"]}); s.commit()
        b = s.get(db.User, o.buyer_id); notify.send(b.phone, f"Quote for order #{o.id}: Rs {c} per piece. Reply ACCEPT {o.id} to confirm.")
        return _view(s, o, user)
def plan(user, oid):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "accepted", "planned"); dec = o.data.get("declined", [])
        ms = [m for m in s.scalars(select(db.User).where(db.User.group_id == o.group_id, db.User.role == "seller", db.User.active == True))  # noqa: E712
              if m.id not in dec]
        if not ms: raise Err(409, "No available members")
        a = E.allocate(o.data["order"]["qty"], _card(o)["stages"], [{"id": m.id, "name": m.name, "skills": m.skills or [], "capacity": m.capacity or 1} for m in ms],
                       days=o.data["order"]["deadline_days"])
        if not a["feasible"]:
            od = o.data["order"]; rc = _card(o); p = E.price_check(od["qty"], **rc["cost"], offered=od["offered_price"])
            a["options"] = _options(od, rc, p, ms, dec, a)
        _set(o, "planned", plan=a, task_accepted=[]); _log(s, o, "plan.proposed", user, {"feasible": a["feasible"], "issues": a["issues"], "options": [x["kind"] for x in a.get("options", [])]}); s.commit()
        return _view(s, o, user)
def start(user, oid):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "planned"); a = o.data["plan"]
        if not a["feasible"]: raise Err(409, "Plan is not feasible: " + "; ".join(a["issues"]) + ". Options: " + " | ".join(x["label"] for x in a.get("options", [])) if a.get("options") else "Plan is not feasible: " + "; ".join(a["issues"]) + ". Replan, renegotiate, or deliver in parts.")
        due = time.time() + int(o.data["order"]["deadline_days"]) * 86400      # the real delivery date; reminders and risk checks count down from it
        _set(o, "in_production", due_at=due); _log(s, o, "production.started", user, {"assignments": len(a["assignments"]), "due_at": round(due)}); s.commit()
        for x in a["assignments"]:
            m = s.get(db.User, x["member"]); notify.send(m.phone, i18n.t("task", m.lang, o=o.id, stage=i18n.stage(x["stage"], m.lang), n=x["units"], pay=x["expected_pay"]))
        return _view(s, o, user)
def propose_payout(user, oid, results):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "in_production", "payout_pending"); lines = []
        for x in o.data["plan"]["assignments"]:
            rw = min(int(o.data.get("rework", {}).get(_key(x), 0)), x["units"])
            r = (results or {}).get(_key(x), {"accepted": x["units"] - rw, "pending": rw}); acc, pen = int(r.get("accepted", x["units"])), int(r.get("pending", 0))
            if acc < 0 or pen < 0 or acc + pen > x["units"]: raise Err(422, f"Invalid counts for {x['name']} ({x['stage']})")
            lines.append({"member": x["member"], "name": x["name"], "stage": x["stage"], "accepted": acc, "pending": pen, "rate": x["rate"]})
        _set(o, "payout_pending", payout=E.payout(lines), approvals={"leader": False, "members": []}, payout_at=time.time()); _log(s, o, "payout.proposed", user, {"total": o.data["payout"]["total"]}); s.commit()
        for m in {l["member"] for l in lines}: mu = s.get(db.User, m); notify.send(mu.phone, i18n.t("pay_ready", mu.lang, o=o.id))
        return _view(s, o, user)
def _settle(s, o, user):
    ap, members = o.data["approvals"], {l["member"] for l in _lines(o)}
    if (o.status == "payout_pending" and ap["leader"] and len(ap["members"]) >= min(QUORUM, len(members))
            and not any(d["open"] for d in o.data.get("disputes", []))):
        _set(o, "settled"); _log(s, o, "payout.settled", user, {"approvers": ap, "total": o.data["payout"]["total"]})
def approve_payout(user, oid):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "payout_pending"); ap = dict(o.data["approvals"]); members = {l["member"] for l in _lines(o)}
        if user["role"] == "leader": ap["leader"] = True
        elif user["id"] in members and user["id"] not in ap["members"]: ap["members"] = ap["members"] + [user["id"]]
        elif user["id"] not in members: raise Err(403, "You have no pay line in this order")
        _set(o, approvals=ap); _log(s, o, "payout.approval", user)
        _settle(s, o, user)
        s.commit(); return _view(s, o, user)
def resolve_dispute(user, oid, idx, note=""):
    with db.S() as s:
        o = _order(s, oid, user); ds = [dict(d) for d in o.data.get("disputes", [])]
        if not 0 <= idx < len(ds): raise Err(404, "Dispute not found")
        ds[idx].update(open=False, note=str(note)[:300]); _set(o, disputes=ds); _log(s, o, "dispute.resolved", user, {"idx": idx}); _settle(s, o, user); s.commit(); return _view(s, o, user)
def add_member(user, phone, name, skills, capacity):
    from auth import norm
    with db.S() as s:
        p = norm(phone)
        if s.scalars(select(db.User).where(db.User.phone == p)).first(): raise Err(409, "Phone already registered")
        s.add(db.User(phone=p, name=str(name)[:80], role="seller", group_id=user["group_id"], skills=list(skills or []), capacity=int(capacity or 0))); s.commit()
        return {"ok": True}
def members(user):
    with db.S() as s:
        return [{"id": m.id, "name": m.name, "skills": m.skills, "capacity": m.capacity}
                for m in s.scalars(select(db.User).where(db.User.group_id == user["group_id"], db.User.role == "seller", db.User.active == True))]  # noqa: E712

# ---- seller (SHG member): sees ONLY her own lines ----
def tasks(user):
    with db.S() as s:
        out = []
        for o in s.scalars(select(db.Order).where(db.Order.group_id == user["group_id"]).order_by(db.Order.id.desc())):
            for x in (o.data.get("plan") or {}).get("assignments", []):
                if x["member"] == user["id"]: out.append({"order_id": o.id, "status": o.status, **x, "accepted": user["id"] in o.data.get("task_accepted", []), "done": min(int(o.data.get("progress", {}).get(_key(x), 0)), x["units"]),
                                "checklist": [c["text"] for c in _checklist(o)] if o.status == "in_production" else []})
        return out
def earnings(user):
    with db.S() as s:
        out = []; lang = _lang(s, user["id"])
        for o in s.scalars(select(db.Order).where(db.Order.group_id == user["group_id"]).order_by(db.Order.id.desc())):
            for l in _lines(o):
                if l["member"] == user["id"]:
                    out.append({"order_id": o.id, "status": o.status, "line": l, "explain": i18n.explain(l, lang), "approved_by_me": user["id"] in o.data["approvals"]["members"]})
        return out
def decline(user, oid):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "accepted", "planned")
        if not any(x["member"] == user["id"] for x in (o.data.get("plan") or {}).get("assignments", [])): raise Err(403, "You have no task in this order")
        _set(o, "accepted", plan=None, task_accepted=[], declined=o.data.get("declined", []) + [user["id"]]); _log(s, o, "member.declined", user); s.commit()
        _tell(s, o.group_id, "leader", f"{user['name']} cannot do her task in order #{o.id}. No penalty. Send PLAN {o.id} to replan.")
        return {"ok": True}
def accept_task(user, oid):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "planned", "in_production")
        if not any(x["member"] == user["id"] for x in (o.data.get("plan") or {}).get("assignments", [])): raise Err(403, "You have no task in this order")
        acc = list(o.data.get("task_accepted", []))
        if user["id"] in acc: return {"ok": True, "already": True, "accepted": len(acc), "of": _n_members(o)}     # tapping twice changes nothing and does not ping the leader again
        acc.append(user["id"])
        _set(o, task_accepted=acc); _log(s, o, "task.accepted", user); s.commit()
        _tell(s, o.group_id, "leader", f"{user['name']} accepted her task in order #{o.id} ({len(acc)}/{_n_members(o)} accepted).")
        return {"ok": True, "already": False, "accepted": len(acc), "of": _n_members(o)}
def report_issue(user, oid, text):
    """A member reports a problem, delay or material need. The leader decides what happens next."""
    with db.S() as s:
        o = _order(s, oid, user); text = str(text).strip()[:300]
        if not text: raise Err(422, "Please describe the problem")
        _log(s, o, "member.issue", user, {"text": text}); s.commit()
        _tell(s, o.group_id, "leader", f"{user['name']} reports a problem in order #{o.id}: {text}")
        return {"ok": True}
def dispute(user, oid, reason):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "payout_pending")
        if not any(l["member"] == user["id"] for l in _lines(o)): raise Err(403, "You have no pay line in this order")
        _set(o, disputes=o.data.get("disputes", []) + [{"uid": user["id"], "name": user["name"], "reason": str(reason)[:300], "open": True, "ts": time.time()}])
        _log(s, o, "dispute.opened", user); s.commit(); _tell(s, o.group_id, "leader", f"{user['name']} disputed pay in order #{o.id}.")
        return {"ok": True}

# ---- consent + officer (aggregate only, small groups suppressed) ----
def consent(user):
    with db.S() as s:
        u = s.get(db.User, user["id"]); u.consent_at = time.time(); LED.append("consent.recorded", {"purpose": "order processing and earnings records"}, f"{user['role']}:{user['id']}", s=s); s.commit()
        return {"ok": True}
def officer_summary(min_cell=3):
    with db.S() as s:
        rows = []
        for g in s.scalars(select(db.Group)):
            os_ = list(s.scalars(select(db.Order).where(db.Order.group_id == g.id)))
            base = {"group": g.name, "block": g.block, "district": g.district}
            if len(os_) < min_cell: rows.append({**base, "orders": f"<{min_cell}"}); continue
            rows.append({**base, "orders": len(os_), "settled": sum(o.status in POST_PAY for o in os_),
                         "in_progress": sum(o.status in ("accepted", "planned", "in_production", "payout_pending") for o in os_),
                         "in_delivery": sum(o.status in ("ready_for_delivery", "dispatched", "partially_delivered") for o in os_),
                         "closed": sum(o.status == "closed" for o in os_), "blocked": sum(o.status == "blocked" for o in os_), "cancelled": sum(o.status == "cancelled" for o in os_),
                         "underpriced_offers": sum(bool((o.data.get("price") or {}).get("loss_making")) for o in os_),
                         "open_disputes": sum(d["open"] for o in os_ for d in o.data.get("disputes", [])),
                         "escalated_open": sum(1 for o in os_ for d in o.data.get("disputes", []) if d["open"] and d.get("escalated"))
                                           + sum(1 for o in os_ for d in o.data.get("quality", []) if d["status"] == "pending" and d.get("escalated"))})
        return rows


# ---- progress, first-sample quality gate, deadline risk (the leader decides; nothing here moves money) ----
def report_done(user, oid, n):
    """Member adds pieces she finished. Cumulative; capped at her task size."""
    try: n = int(n); assert n > 0
    except Exception: raise Err(422, "Send a positive number of pieces, e.g. DONE 1 20")
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "in_production")
        mine = [x for x in (o.data.get("plan") or {}).get("assignments", []) if x["member"] == user["id"]]
        if not mine: raise Err(403, "You have no task in this order")
        pr = dict(o.data.get("progress", {})); x = next((x for x in mine if pr.get(_key(x), 0) < x["units"]), mine[-1])
        pr[_key(x)] = min(x["units"], pr.get(_key(x), 0) + n)
        _set(o, progress=pr); _log(s, o, "progress.reported", user, {"stage": x["stage"], "done": pr[_key(x)], "of": x["units"]}); s.commit()
        if pr[_key(x)] >= x["units"]: _tell(s, o.group_id, "leader", f"{user['name']} finished {x['stage']} x{x['units']} for order #{o.id}.")
        return {"order_id": o.id, "stage": x["stage"], "done": pr[_key(x)], "units": x["units"]}

def submit_sample(user, oid, note="", photo=None):
    """First-piece check: a member sends a sample (photo optional). The AI pre-screens the photo against the buyer's checklist and FLAGS
    possible defects with a confidence level; it makes no pass/fail decision. A person (the leader) reviews it."""
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "in_production")
        x = next((x for x in (o.data.get("plan") or {}).get("assignments", []) if x["member"] == user["id"]), None)
        if not x: raise Err(403, "You have no task in this order")
        qs = list(o.data.get("quality", []))
        if any(q["member"] == user["id"] and q["status"] == "pending" for q in qs): raise Err(409, "Your last sample is still waiting for the leader")
        photo = photo if isinstance(photo, str) and photo.split(";")[0] in ("data:image/jpeg", "data:image/png", "data:image/webp") and len(photo) <= 250_000 else None
        qs.append({"i": len(qs), "member": user["id"], "name": user["name"], "stage": x["stage"], "note": str(note)[:200], "photo": photo, "status": "pending", "ts": time.time()})
        _set(o, quality=qs); _log(s, o, "quality.sample_submitted", user, {"i": len(qs) - 1, "photo": bool(photo)}); s.commit()
        i, stage, checklist = len(qs) - 1, x["stage"], _checklist(o)
    sc = screening.screen(photo, checklist, stage, note)                          # the network call runs with no database session held open
    with db.S() as s:
        o = s.get(db.Order, oid); qs = [dict(q) for q in o.data.get("quality", [])]
        qs[i]["screening"] = sc; _set(o, quality=qs)
        _log(s, o, "quality.ai_screened", {"role": "system", "id": "ai"}, {"i": i, "status": sc["status"], "flagged": sc.get("flagged", 0), "model": sc["model"],
             "flags": [{"id": f["id"], "confidence": f["confidence"]} for f in screening.flags(sc)]}); s.commit()
        nm = user["name"].lower(); ai = screening.describe(sc)
        _tell(s, o.group_id, "leader", f"{user['name']} sent a first {stage} sample for order #{oid}{' with a photo' if photo else ''}.\n{ai}\nSend PASS {oid} {nm} or REWORK {oid} {nm} <pieces> <reason>.")
    return {"ok": True, "photo": bool(photo)}

def rescreen(user, oid, i):
    """Leader asks for the AI check again (e.g. it was unavailable the first time). Only on a sample still waiting for her decision."""
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "in_production"); qs = o.data.get("quality", [])
        if not 0 <= i < len(qs) or qs[i]["status"] != "pending": raise Err(404, "No sample waiting for review with that number")
        photo, stage, note, checklist = qs[i].get("photo"), qs[i]["stage"], qs[i].get("note", ""), _checklist(o)
    sc = screening.screen(photo, checklist, stage, note)
    with db.S() as s:
        o = _order(s, oid, user); qs = [dict(q) for q in o.data.get("quality", [])]; qs[i]["screening"] = sc; _set(o, quality=qs)
        _log(s, o, "quality.ai_screened", user, {"i": i, "status": sc["status"], "flagged": sc.get("flagged", 0), "rerun": True}); s.commit(); return _view(s, o, user)

def get_checklist(user, oid):
    """Leaders and the members working on the order can read the standard they are judged against. Buyers and officers cannot."""
    if user["role"] not in ("leader", "seller"): raise Err(403, "Not allowed")
    with db.S() as s: return _checklist(_order(s, oid, user))

def set_checklist(user, oid, items):
    """The leader edits the buyer-specification checklist (add what the buyer said on the phone, remove what does not apply)."""
    _leader_only(user)
    try: cl = screening.normalise_checklist(items)
    except ValueError as e: raise Err(422, str(e))
    with db.S() as s:
        o = _order(s, oid, user)
        if o.status in POST_PAY + ("blocked", "cancelled"): raise Err(409, "This order is already settled")
        _set(o, checklist=cl); _log(s, o, "quality.checklist_set", user, {"criteria": len(cl)}); s.commit(); return cl

def review_sample(user, oid, who, decision, rework=0, reason=""):
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "in_production")
        qs = [dict(q) for q in o.data.get("quality", [])]
        q = next((q for q in qs if q["status"] == "pending" and (q["name"].lower() == str(who).lower() or str(q["member"]) == str(who))), None)
        if not q: raise Err(404, f"No sample from {who} is waiting for review")
        rw = dict(o.data.get("rework", {}))
        if decision == "pass": q.update(status="passed", rework=0); rw.pop(f"{q['member']}:{q['stage']}", None)
        else:
            units = next(x["units"] for x in o.data["plan"]["assignments"] if x["member"] == q["member"] and x["stage"] == q["stage"])
            try: n = int(rework); assert 1 <= n <= units
            except Exception: raise Err(422, f"Say how many pieces need rework (1 to {units}), e.g. REWORK {o.id} {q['name'].lower()} 4 stitching loose")
            q.update(status="rework", rework=n, reason=str(reason or "see leader")[:200]); rw[f"{q['member']}:{q['stage']}"] = n
        q.update(decided_by=user["id"], decided_ts=time.time())
        sc = q.get("screening") or {}; q["ai_flagged"] = sc.get("flagged", 0); _set(o, quality=qs, rework=rw)               # kept so the leader's decision can be compared with the AI's flags later
        _log(s, o, "quality.decided", user, {"i": q["i"], "member": q["member"], "decision": q["status"], "rework": q.get("rework", 0),
             "ai_status": sc.get("status", "none"), "ai_flagged": sc.get("flagged", 0)}); s.commit()
        m = s.get(db.User, q["member"])
        notify.send(m.phone, i18n.t("q_pass", m.lang, o=o.id) if decision == "pass" else i18n.t("q_rework", m.lang, o=o.id, n=q["rework"], why=q["reason"]))
        return _view(s, o, user)

def photo(user, oid, i):
    with db.S() as s:
        o = _order(s, oid, user)
        if user["role"] != "leader": raise Err(403, "Only the group leader can see sample photos")
        q = (o.data.get("quality") or [])
        if not 0 <= i < len(q) or not q[i].get("photo"): raise Err(404, "No photo")
        return q[i]["photo"]

def _risk_calc(s, o, dl):
    a = (o.data.get("plan") or {}).get("assignments", [])
    cap = {m.id: (m.capacity or 1) for m in s.scalars(select(db.User).where(db.User.group_id == o.group_id))}
    per, nst = {}, {}
    for x in a: nst[x["member"]] = nst.get(x["member"], 0) + 1
    for x in a: per[x["stage"]] = per.get(x["stage"], 0) + max(cap.get(x["member"], 1) // nst[x["member"]], 1)   # a woman on two stages splits her day between them
    pg = _progress(o); slow = min(per, key=per.get)
    return {**E.rescue(pg["finished"], o.data["order"]["qty"], per[slow], dl), "done": pg["finished"], "needed": o.data["order"]["qty"], "per_day": per[slow],
            "days_left": dl, "slowest_stage": slow, "basis": "declared daily capacity of the slowest stage; a person on two stages splits her day; an estimate, not a prediction"}
def risk(user, oid, days_left=None):
    """Deadline check with transparent rules: pieces finished + (slowest stage's declared capacity x days left) vs pieces needed.
    Days left comes from the real delivery date set when work started. A typed value is only an override for what-if checks."""
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "in_production", "planned")
        if not (o.data.get("plan") or {}).get("assignments"): raise Err(409, "Build the plan first")
        try:
            if days_left not in (None, ""): dl, src = int(days_left), "typed by leader"; assert 0 <= dl <= 60
            elif o.data.get("due_at") is not None: dl, src = max(_days_left(o), 0), "from the delivery date"
            else: dl, src = int(o.data["order"]["deadline_days"]), "from the ordered deadline (work not started yet)"
        except Exception: raise Err(422, "Days left must be a number from 0 to 60")
        r = {**_risk_calc(s, o, dl), "days_left_source": src}
        _set(o, risk=r); _log(s, o, "risk.checked", user, {"shortfall": r["shortfall"], "days_left": dl, "source": src}); s.commit()
        return r

# ---- reminder engine: one entry point for a scheduler (like MaaSakhi's /cron/reminders) ----
TASK_LEAD_DAYS = int(os.getenv("TASK_REMIND_DAYS", "1"))          # remind members when this many days (or fewer) remain
SAMPLE_NUDGE_MIN = int(os.getenv("SAMPLE_NUDGE_MINUTES", "60"))    # nudge the leader before the officer escalation fires
PAYOUT_NUDGE_H = int(os.getenv("PAYOUT_NUDGE_HOURS", "24"))
COOLDOWN_H = int(os.getenv("REMINDER_COOLDOWN_HOURS", "24"))       # the same reminder is not repeated inside this window
def _once(o, key, now):
    rem = dict(o.data.get("reminders", {}))
    if now - rem.get(key, 0) < COOLDOWN_H * 3600: return False
    rem[key] = now; _set(o, reminders=rem); return True
def run_reminders(now=None):
    """Send every due reminder once per cooldown. Returns counts per type. Idempotent: safe to call every few minutes."""
    now = time.time() if now is None else now; c = {"task_due": 0, "deadline_risk": 0, "sample_review": 0, "payout_approval": 0}
    with db.S() as s:
        for o in s.scalars(select(db.Order).where(db.Order.status.in_(("in_production", "payout_pending")))):
            dl = _days_left(o, now)
            if o.status == "in_production":
                if dl is not None and dl <= TASK_LEAD_DAYS:
                    for r in _progress(o)["rows"]:
                        if r["done"] < r["units"] and _once(o, f"task:{r['member']}:{r['stage']}", now):
                            m = s.get(db.User, r["member"]); left = r["units"] - r["done"]
                            notify.send(m.phone, i18n.t("task_due" if dl > 0 else "task_due_now", m.lang, o=o.id, stage=i18n.stage(r["stage"], m.lang), left=left, d=dl)); c["task_due"] += 1
                if dl is not None and (o.data.get("plan") or {}).get("assignments"):
                    r = _risk_calc(s, o, max(dl, 0))
                    if r["shortfall"] > 0 and _once(o, "risk", now):
                        _set(o, risk={**r, "days_left_source": "from the delivery date"})
                        LED.append("risk.alert", {"shortfall": r["shortfall"], "days_left": r["days_left"]}, "system:reminders", o.id, s=s)
                        _tell(s, o.group_id, "leader", f"Order #{o.id}: at the current pace you are about {r['shortfall']} pieces short, with {r['days_left']} day(s) to the delivery date. Open the order to see options.")
                        c["deadline_risk"] += 1
                old = [i for i, q in enumerate(o.data.get("quality", [])) if q["status"] == "pending" and now - q.get("ts", 0) >= SAMPLE_NUDGE_MIN * 60 and _once(o, f"sample:{i}", now)]
                if old: _tell(s, o.group_id, "leader", f"Order #{o.id}: {len(old)} sample(s) are waiting for your review. Members cannot make more until you pass or send them back."); c["sample_review"] += len(old)
            else:
                if now - (o.data.get("payout_at") or o.created or 0) < PAYOUT_NUDGE_H * 3600: continue
                ap = o.data.get("approvals") or {"leader": False, "members": []}
                if not ap["leader"] and _once(o, "payout:leader", now):
                    _tell(s, o.group_id, "leader", f"Order #{o.id}: the payout of Rs {o.data['payout']['total']} is waiting for your approval."); c["payout_approval"] += 1
                for mid in sorted({l["member"] for l in _lines(o)} - set(ap["members"])):
                    if _once(o, f"payout:{mid}", now):
                        m = s.get(db.User, mid); notify.send(m.phone, i18n.t("pay_nudge", m.lang, o=o.id)); c["payout_approval"] += 1
        s.commit()
    return {**c, "escalated": escalate_overdue()}

# ---- escalation (like MaaSakhi's unattended-alert escalation): leader does not act -> mission officer is told, with no names or pay ----
ESC_MIN = int(os.getenv("ESCALATION_MINUTES", "120"))
def escalate_overdue(minutes=None):
    """Anything waiting for the leader longer than ESCALATION_MINUTES is flagged to the mission officer(s)."""
    mins = ESC_MIN if minutes is None else int(minutes); label = mins; now, out = time.time(), []
    with db.S() as s:
        officers = list(s.scalars(select(db.User).where(db.User.role == "officer", db.User.active == True)))  # noqa: E712
        for g in s.scalars(select(db.Group)):
            nd = nq = 0
            for o in s.scalars(select(db.Order).where(db.Order.group_id == g.id)):
                ds, qs, ch = [dict(x) for x in o.data.get("disputes", [])], [dict(x) for x in o.data.get("quality", [])], False
                for x in ds:
                    if x["open"] and not x.get("escalated") and now - x.get("ts", o.created or 0) >= mins * 60: x["escalated"] = True; nd += 1; ch = True
                for x in qs:
                    if x["status"] == "pending" and not x.get("escalated") and now - x.get("ts", 0) >= mins * 60: x["escalated"] = True; nq += 1; ch = True
                if ch: _set(o, disputes=ds, quality=qs)
            if nd or nq:
                LED.append("escalation.raised", {"group": g.name, "disputes": nd, "sample_reviews": nq, "after_minutes": label}, "system:escalation", s=s)
                for u in officers: notify.send(u.phone, f"{g.name}: {nd} pay dispute(s) and {nq} sample review(s) have waited over {label} min for the group leader. Please call her. No member names or pay are shared in this message.")
                _tell(s, g.id, "leader", f"{nd + nq} item(s) waiting over {label} min were flagged to the mission officer. Please attend to them.")
                out.append({"group": g.name, "disputes": nd, "sample_reviews": nq})
        s.commit()
    return out

# ---- language, WhatsApp self-registration, owner role switch ----
def set_lang(user, lang):
    with db.S() as s:
        u = s.get(db.User, user["id"]); u.lang = lang; s.commit()
def open_registration(): return os.getenv("OPEN_REGISTRATION", "0") == "1"
def register_self(phone, name, role, skill=None, lang="en"):
    """A new number registers itself over WhatsApp (language -> name -> role -> skill). Only when OPEN_REGISTRATION=1; otherwise the leader adds members."""
    from auth import norm
    if not open_registration(): raise Err(403, "Self-registration is switched off. Please ask your group leader to add you.")
    with db.S() as s:
        p = norm(phone)
        if s.scalars(select(db.User).where(db.User.phone == p)).first(): raise Err(409, "Phone already registered")
        g = s.scalars(select(db.Group)).first()
        if not g: raise Err(409, "No group exists yet")
        member = role == "seller"
        u = db.User(phone=p, name=(str(name).strip()[:40] or "Guest"), role="seller" if member else "buyer", group_id=g.id if member else None,
                    skills=[skill] if (member and skill) else [], capacity=30 if member else 0, consent_at=time.time(), lang=lang)
        s.add(u); s.flush(); LED.append("consent.recorded", {"purpose": "order processing and earnings records", "via": "whatsapp"}, f"{u.role}:{u.id}", s=s); s.commit()
        return {"id": u.id, "name": u.name, "role": u.role}

ROLES = ("buyer", "leader", "seller", "officer")
def switch_owner_role(user, role):
    """Programme owner only (OWNER_PHONE): lets one phone act as each role in turn, so one person can test the whole flow."""
    role = {"member": "seller", "admin": "officer"}.get(role, role)
    if role not in ROLES: raise Err(422, "Send ROLE buyer, ROLE leader, ROLE member or ROLE officer")
    with db.S() as s:
        u = s.get(db.User, user["id"]); g = s.scalars(select(db.Group)).first()
        u.role = role; u.group_id = g.id if (role in ("leader", "seller") and g) else None
        if role == "seller":
            u.skills = list(u.skills or []) or list(ALL_STAGES); u.capacity = u.capacity or 30
        LED.append("role.switched", {"to": role}, f"owner:{u.id}", s=s); s.commit()
    return role


# ---- products and group rate cards ----
def products():
    return [{"key": k, "label": t["label"], "unit": t["unit"], "stages": t["stages"], "cost": t["cost"]} for k, t in defaults.TEMPLATES.items()]
def ratecards_list(user):
    """Leader and members of a group see its cards: the active version per product (or the default) plus anything awaiting approval."""
    if user["role"] not in ("leader", "seller"): raise Err(403, "Not allowed")
    with db.S() as s:
        rows = list(s.scalars(select(db.RateCard).where(db.RateCard.group_id == user["group_id"]).order_by(db.RateCard.product, db.RateCard.version.desc())))
        act = {k: ratecards.active(s, user["group_id"], k) for k in defaults.TEMPLATES}
        return {"active": [{**c, "label": defaults.TEMPLATES[k]["label"]} for k, c in act.items()], "history": [ratecards.view(r, user["id"]) for r in rows]}
def propose_ratecard(user, product, stages, cost, note=""):
    _leader_only(user)
    try: st, c = ratecards.validate(product, stages, cost)
    except ratecards.Bad as e: raise Err(422, str(e))
    with db.S() as s:
        if s.scalars(select(db.RateCard).where(db.RateCard.group_id == user["group_id"], db.RateCard.product == product, db.RateCard.status == "proposed")).first():
            raise Err(409, "A proposed rate card for this product is already waiting for approval")
        last = s.scalars(select(db.RateCard).where(db.RateCard.group_id == user["group_id"], db.RateCard.product == product).order_by(db.RateCard.version.desc())).first()
        r = db.RateCard(group_id=user["group_id"], product=product, version=(last.version if last else 0) + 1, status="proposed", stages=st, cost=c,
                        note=str(note)[:200], leader_ok=True, member_ok=[], created=time.time())
        s.add(r); s.flush(); LED.append("ratecard.proposed", {"product": product, "version": r.version, "stages": st, "cost": c}, f"leader:{user['id']}", s=s)
        _ratecard_settle(s, user, r); s.commit()
        _tell(s, user["group_id"], "seller", f"New {defaults.TEMPLATES[product]['label']} rate card v{r.version} proposed. Please review it and approve in the dashboard.")
        return ratecards.view(r, user["id"])
def _ratecard_settle(s, user, r):
    n_members = len(list(s.scalars(select(db.User).where(db.User.group_id == r.group_id, db.User.role == "seller", db.User.active == True))))  # noqa: E712
    if r.status == "proposed" and r.leader_ok and len(r.member_ok or []) >= min(ratecards.QUORUM, n_members):
        for o in s.scalars(select(db.RateCard).where(db.RateCard.group_id == r.group_id, db.RateCard.product == r.product, db.RateCard.status == "active")): o.status = "superseded"
        r.status, r.approved_at = "active", time.time()
        LED.append("ratecard.activated", {"product": r.product, "version": r.version, "members": r.member_ok}, f"group:{r.group_id}", s=s)
def approve_ratecard(user, cid):
    if user["role"] not in ("leader", "seller"): raise Err(403, "Not allowed")
    with db.S() as s:
        r = s.get(db.RateCard, cid)
        if not r or r.group_id != user["group_id"]: raise Err(404, "Rate card not found")
        if r.status != "proposed": raise Err(409, f"This rate card is '{r.status}', not waiting for approval")
        if user["role"] == "seller":
            if user["id"] in (r.member_ok or []): raise Err(409, "You already approved this rate card")
            r.member_ok = list(r.member_ok or []) + [user["id"]]
        LED.append("ratecard.approval", {"product": r.product, "version": r.version}, f"{user['role']}:{user['id']}", s=s)
        _ratecard_settle(s, user, r); s.commit(); return ratecards.view(r, user["id"])
def reject_ratecard(user, cid):
    _leader_only(user)
    with db.S() as s:
        r = s.get(db.RateCard, cid)
        if not r or r.group_id != user["group_id"]: raise Err(404, "Rate card not found")
        if r.status != "proposed": raise Err(409, "Only a proposed rate card can be withdrawn")
        r.status = "rejected"; LED.append("ratecard.rejected", {"product": r.product, "version": r.version}, f"leader:{user['id']}", s=s); s.commit()
        return ratecards.view(r, user["id"])


# ---- delivery and acceptance: settled -> ready_for_delivery -> dispatched -> (partially_delivered ->)* closed; blocked and cancelled are side exits ----
def _img_ok(photo): return photo if isinstance(photo, str) and photo.split(";")[0] in ("data:image/jpeg", "data:image/png", "data:image/webp") and len(photo) <= 250_000 else None
def _ndv(o, **kw): d = {**(o.data.get("delivery") or {}), **kw}; _set(o, delivery=d); return d
def _int(v, lo, hi, msg):
    try: n = int(v); assert lo <= n <= hi; return n
    except Exception: raise Err(422, msg)

def mark_ready(user, oid, counted, note=""):
    """Quantity check before anything leaves the village: the leader counts the finished, packed pieces. Fewer than still owed = a partial delivery, said openly."""
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "settled", "partially_delivered"); d = _dv(o); rem = o.data["order"]["qty"] - d["delivered"]
        n = _int(counted, 1, rem, f"Count the packed pieces: a number from 1 to {rem} (still owed to the buyer)")
        qc = {"counted": n, "owed": rem, "match": n == rem, "note": str(note)[:200], "by": user["id"], "ts": time.time()}
        _ndv(o, qc=qc); _set(o, "ready_for_delivery"); _log(s, o, "delivery.qty_checked", user, {"counted": n, "owed": rem, "match": n == rem}); s.commit()
        if n < rem: _tell(s, o.group_id, "leader", f"Order #{o.id}: you packed {n} of {rem} owed pieces. This will go as a partial delivery.")
        return _view(s, o, user)

def dispatch(user, oid, courier, tracking="", date="", note=""):
    _leader_only(user)
    courier = str(courier or "").strip()[:60]
    if not courier: raise Err(422, "Say how it is being sent (courier, bus, own vehicle, hand delivery)")
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "ready_for_delivery"); d = _dv(o); qc = d["qc"]
        sh = {"i": len(d["shipments"]), "qty": qc["counted"], "courier": courier, "tracking": str(tracking or "")[:60], "date": str(date or "")[:30], "note": str(note)[:200], "photo": None, "received": None, "signoff": None, "ts": time.time()}
        _ndv(o, shipments=d["shipments"] + [sh]); _set(o, "dispatched"); _log(s, o, "delivery.dispatched", user, {"i": sh["i"], "qty": sh["qty"], "courier": courier, "tracking": sh["tracking"]}); s.commit()
        b = s.get(db.User, o.buyer_id); notify.send(b.phone, f"Order #{o.id}: {sh['qty']} pieces dispatched via {courier}{' (' + sh['tracking'] + ')' if sh['tracking'] else ''}. When they arrive, reply RECEIVED {o.id} <pieces you got>.")
        return _view(s, o, user)

def delivery_photo(user, oid, photo):
    """Proof of delivery: a photo of the packed or delivered goods, attached to the current shipment."""
    _leader_only(user)
    ph = _img_ok(photo)
    if not ph: raise Err(422, "Send a JPEG, PNG or WebP photo under about 180 KB")
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "dispatched"); d = _dv(o); sh = [dict(x) for x in d["shipments"]]; sh[-1]["photo"] = ph
        _ndv(o, shipments=sh); _log(s, o, "delivery.photo_added", user, {"i": sh[-1]["i"]}); s.commit(); return _view(s, o, user)

def delivery_photo_get(user, oid, i):
    if user["role"] not in ("leader", "buyer"): raise Err(403, "Not allowed")
    with db.S() as s:
        o = _order(s, oid, user); sh = _dv(o)["shipments"]
        if not 0 <= i < len(sh) or not sh[i].get("photo"): raise Err(404, "No photo")
        return sh[i]["photo"]

def signoff(user, oid, received, note="", final=False):
    """Buyer sign-off: how many pieces actually arrived. Short = partially_delivered (the rest can be sent), or the buyer can accept the shortfall and close."""
    if user["role"] not in ("buyer", "leader"): raise Err(403, "Only the buyer can sign off a delivery")
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "dispatched")
        if user["role"] == "leader" and not o.data.get("on_behalf"): raise Err(403, "Only the buyer can sign off this delivery")
        d = _dv(o); sh = [dict(x) for x in d["shipments"]]; cur = sh[-1]
        n = _int(received, 0, cur["qty"], f"Say how many pieces arrived: 0 to {cur['qty']}")
        cur.update(received=n, signoff={"by": user["id"], "note": str(note)[:200], "ts": time.time(), "short": cur["qty"] - n})
        tot = d["delivered"] + n; qty = o.data["order"]["qty"]
        _ndv(o, shipments=sh, delivered=tot)
        done = tot >= qty or bool(final)
        _set(o, "closed" if done else "partially_delivered")
        _log(s, o, "delivery.signed_off", user, {"i": cur["i"], "received": n, "sent": cur["qty"], "delivered": tot, "of": qty, "accepted_short": bool(final and tot < qty)}); s.commit()
        _tell(s, o.group_id, "leader", f"Order #{o.id}: buyer received {n} of {cur['qty']} pieces ({tot}/{qty} in total). " + ("Order closed." if done else f"{qty - tot} still owed: pack them and send READY {o.id} <pieces>."))
        return _view(s, o, user)

def block(user, oid, reason):
    """Delivery cannot go ahead (road closed, buyer unreachable, goods held up). Remembers the state to come back to."""
    _leader_only(user); reason = str(reason or "").strip()[:200]
    if not reason: raise Err(422, "Say why the delivery is blocked")
    with db.S() as s:
        o = _order(s, oid, user); _need(o, *DELIVERY_OPEN)
        _set(o, "blocked", blocked={"reason": reason, "prev": o.status, "ts": time.time()}); _log(s, o, "order.blocked", user, {"reason": reason, "prev": o.data["blocked"]["prev"]}); s.commit()
        b = s.get(db.User, o.buyer_id); notify.send(b.phone, f"Order #{o.id} is on hold: {reason}. The group will update you.")
        return _view(s, o, user)

def unblock(user, oid, note=""):
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "blocked"); prev = o.data["blocked"]["prev"]
        _set(o, prev, blocked=None); _log(s, o, "order.unblocked", user, {"back_to": prev, "note": str(note)[:200]}); s.commit(); return _view(s, o, user)

def cancel(user, oid, reason=""):
    """Before production starts the buyer or the leader can cancel with no money owed. After that the group and buyer must settle it (blocked + leader), not a silent cancel."""
    if user["role"] not in ("buyer", "leader"): raise Err(403, "Not allowed")
    with db.S() as s:
        o = _order(s, oid, user); _need(o, *CANCELLABLE)
        prev = o.status; _set(o, "cancelled", cancelled={"by": user["role"], "reason": str(reason or "")[:200], "ts": time.time()}); _log(s, o, "order.cancelled", user, {"reason": str(reason or "")[:200], "from": prev}); s.commit()
        if user["role"] == "buyer": _tell(s, o.group_id, "leader", f"Buyer cancelled order #{o.id}. No work had started.")
        else:
            b = s.get(db.User, o.buyer_id); notify.send(b.phone, f"Order #{o.id} was cancelled by the group. Reason: {str(reason or 'not given')[:100]}")
        return _view(s, o, user)
