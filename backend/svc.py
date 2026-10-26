"""Business rules shared by the REST dashboards and the WhatsApp bot. Every function checks the caller's role and scope."""
import os, time
from sqlalchemy import select
import db, engine as E, i18n, llm, notify
import defaults

LED = db.DBLedger()
QUORUM = int(os.getenv("PAYOUT_MEMBER_QUORUM", "2"))   # members who must approve besides the leader
STAGES, COST = defaults.STAGES, defaults.COST

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
    v = {"id": o.id, "status": o.status, "order": d["order"], "unclear": d.get("unclear", []), "quote": d.get("quote")}
    pg = _progress(o)
    if user["role"] == "buyer":                         # buyers never see cost, plan, members or pay: only how far along the work is
        g = s.get(db.Group, o.group_id); return {**v, "group": g.name if g else "", "progress_pct": pg["pct"]}
    q = [{**{k: x[k] for k in x if k != "photo"}, "has_photo": bool(x.get("photo"))} for x in d.get("quality", [])]
    return {**v, "price": d.get("price"), "plan": d.get("plan"), "payout": d.get("payout"), "approvals": d.get("approvals"),
            "disputes": d.get("disputes", []), "declined": d.get("declined", []), "quality": q, "progress": pg, "risk": d.get("risk")}
def _lines(o): return (o.data.get("payout") or {}).get("lines", [])
def _key(x): return f"{x['member']}:{x['stage']}"
def _lang(s, uid):
    u = s.get(db.User, uid); return (u.lang if u and u.lang else "en")
def _leader_only(user):
    if user["role"] != "leader": raise Err(403, "Only the group leader can do this")
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
            n = sum(1 for o in s.scalars(select(db.Order).where(db.Order.group_id == g.id)) if o.status == "settled")
            out.append({"id": g.id, "name": g.name, "village": g.village, "orders_settled": n})
        return out
def submit_order(user, group_id, text, pre=None):
    """pre = details already extracted and confirmed in the WhatsApp conversation: {"order": {...}, "unclear": [...], "evidence": {...}}."""
    ex = {"order": pre["order"], "unclear": pre.get("unclear", [])} if pre else llm.extract_order(text); od = ex["order"]
    missing = [k for k in ("qty", "offered_price", "deadline_days") if od.get(k) is None]
    if missing: raise Err(422, "Could not find: " + ", ".join(missing) + ". Please include quantity, price per piece and days.")
    with db.S() as s:
        g = s.get(db.Group, group_id)
        if not g: raise Err(404, "Group not found")
        data = {"order": od, "unclear": ex["unclear"], "declined": [], "disputes": []}
        if pre and pre.get("evidence"): data["evidence"] = pre["evidence"]           # original text / transcript / photo / PDF text the order was read from
        if user["role"] == "leader": data["on_behalf"] = True                          # the leader placed it for a buyer (named in the order)
        o = db.Order(group_id=g.id, buyer_id=user["id"], status="submitted", created=time.time(), data=data)
        s.add(o); s.flush(); _log(s, o, "order.submitted", user, od); s.commit()
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
        p = E.price_check(od["qty"], **COST, offered=od["offered_price"])
        ms = [m for m in s.scalars(select(db.User).where(db.User.group_id == o.group_id, db.User.role == "seller", db.User.active == True)) if m.id not in dec]  # noqa: E712
        a = E.allocate(od["qty"], STAGES, [{"id": m.id, "name": m.name, "skills": m.skills or [], "capacity": m.capacity or 1} for m in ms], days=od["deadline_days"]) if ms else {"feasible": False, "issues": ["No available members in the group"]}
        return {"price": p, "feasible": a["feasible"], "issues": a["issues"]}
def quote(user, oid, counter=None):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "submitted", "quoted"); od = o.data["order"]
        p = E.price_check(od["qty"], **COST, offered=od["offered_price"])
        try: c = str(counter if counter not in (None, "") else p["suggested_counter"]); assert float(c) > 0
        except Exception: raise Err(422, "Counter price must be a positive number")
        _set(o, "quoted", price=p, quote=c); _log(s, o, "quote.sent", user, {"quote": c, "floor": p["per_unit"]["floor"]}); s.commit()
        b = s.get(db.User, o.buyer_id); notify.send(b.phone, f"Quote for order #{o.id}: Rs {c} per piece. Reply ACCEPT {o.id} to confirm.")
        return _view(s, o, user)
def plan(user, oid):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "accepted", "planned"); dec = o.data.get("declined", [])
        ms = [m for m in s.scalars(select(db.User).where(db.User.group_id == o.group_id, db.User.role == "seller", db.User.active == True))  # noqa: E712
              if m.id not in dec]
        if not ms: raise Err(409, "No available members")
        a = E.allocate(o.data["order"]["qty"], STAGES, [{"id": m.id, "name": m.name, "skills": m.skills or [], "capacity": m.capacity or 1} for m in ms],
                       days=o.data["order"]["deadline_days"])
        _set(o, "planned", plan=a); _log(s, o, "plan.proposed", user, {"feasible": a["feasible"], "issues": a["issues"]}); s.commit()
        return _view(s, o, user)
def start(user, oid):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "planned"); a = o.data["plan"]
        if not a["feasible"]: raise Err(409, "Plan is not feasible: " + "; ".join(a["issues"]) + ". Replan, renegotiate, or deliver in parts.")
        _set(o, "in_production"); _log(s, o, "production.started", user, {"assignments": len(a["assignments"])}); s.commit()
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
        _set(o, "payout_pending", payout=E.payout(lines), approvals={"leader": False, "members": []}); _log(s, o, "payout.proposed", user, {"total": o.data["payout"]["total"]}); s.commit()
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
                if x["member"] == user["id"]: out.append({"order_id": o.id, "status": o.status, **x, "done": min(int(o.data.get("progress", {}).get(_key(x), 0)), x["units"])})
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
        _set(o, "accepted", plan=None, declined=o.data.get("declined", []) + [user["id"]]); _log(s, o, "member.declined", user); s.commit()
        _tell(s, o.group_id, "leader", f"{user['name']} cannot do her task in order #{o.id}. No penalty. Send PLAN {o.id} to replan.")
        return {"ok": True}
def accept_task(user, oid):
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "planned", "in_production")
        if not any(x["member"] == user["id"] for x in (o.data.get("plan") or {}).get("assignments", [])): raise Err(403, "You have no task in this order")
        acc = list(o.data.get("task_accepted", []))
        if user["id"] not in acc: acc.append(user["id"])
        _set(o, task_accepted=acc); _log(s, o, "task.accepted", user); s.commit()
        _tell(s, o.group_id, "leader", f"{user['name']} accepted her task in order #{o.id}.")
        return {"ok": True}
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
            rows.append({**base, "orders": len(os_), "settled": sum(o.status == "settled" for o in os_),
                         "in_progress": sum(o.status in ("accepted", "planned", "in_production", "payout_pending") for o in os_),
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
    """First-piece check: a member sends a sample (photo optional). A person reviews it. No AI verdict is made here."""
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "in_production")
        x = next((x for x in (o.data.get("plan") or {}).get("assignments", []) if x["member"] == user["id"]), None)
        if not x: raise Err(403, "You have no task in this order")
        qs = list(o.data.get("quality", []))
        if any(q["member"] == user["id"] and q["status"] == "pending" for q in qs): raise Err(409, "Your last sample is still waiting for the leader")
        photo = photo if isinstance(photo, str) and photo.split(";")[0] in ("data:image/jpeg", "data:image/png", "data:image/webp") and len(photo) <= 250_000 else None
        qs.append({"i": len(qs), "member": user["id"], "name": user["name"], "stage": x["stage"], "note": str(note)[:200], "photo": photo, "status": "pending", "ts": time.time()})
        _set(o, quality=qs); _log(s, o, "quality.sample_submitted", user, {"i": len(qs) - 1, "photo": bool(photo)}); s.commit()
        nm = user["name"].lower()
        _tell(s, o.group_id, "leader", f"{user['name']} sent a first {x['stage']} sample for order #{o.id}{' with a photo' if photo else ''}. Send PASS {o.id} {nm} or REWORK {o.id} {nm} <pieces> <reason>.")
        return {"ok": True, "photo": bool(photo)}

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
        _set(o, quality=qs, rework=rw); _log(s, o, "quality.decided", user, {"i": q["i"], "member": q["member"], "decision": q["status"], "rework": q.get("rework", 0)}); s.commit()
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

def risk(user, oid, days_left=None):
    """Deadline check with transparent rules: pieces finished + (slowest stage's declared capacity x days left) vs pieces needed."""
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "in_production", "planned"); a = (o.data.get("plan") or {}).get("assignments", [])
        if not a: raise Err(409, "Build the plan first")
        cap = {m.id: (m.capacity or 1) for m in s.scalars(select(db.User).where(db.User.group_id == o.group_id))}
        per, nst = {}, {}
        for x in a: nst[x["member"]] = nst.get(x["member"], 0) + 1
        for x in a: per[x["stage"]] = per.get(x["stage"], 0) + max(cap.get(x["member"], 1) // nst[x["member"]], 1)   # a woman on two stages splits her day between them
        try: dl = int(days_left) if days_left not in (None, "") else o.data["order"]["deadline_days"]; assert 0 <= dl <= 60
        except Exception: raise Err(422, "Days left must be a number from 0 to 60")
        pg = _progress(o); slow = min(per, key=per.get)
        r = {**E.rescue(pg["finished"], o.data["order"]["qty"], per[slow], dl), "done": pg["finished"], "needed": o.data["order"]["qty"], "per_day": per[slow],
             "days_left": dl, "slowest_stage": slow, "basis": "declared daily capacity of the slowest stage; a person on two stages splits her day; an estimate, not a prediction"}
        _set(o, risk=r); _log(s, o, "risk.checked", user, {"shortfall": r["shortfall"], "days_left": dl}); s.commit()
        return r

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
            u.skills = list(u.skills or []) or [x["name"] for x in STAGES]; u.capacity = u.capacity or 30
        LED.append("role.switched", {"to": role}, f"owner:{u.id}", s=s); s.commit()
    return role
