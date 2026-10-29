"""New leader operations that sit on the shared workflow: placing an order for a buyer, recording money received, private notes, moving
work between members, resolving member issues, messaging members, managing members and buyers, and materials.
Same rules as svc.py: every function checks the caller's role and scope, and every change is appended to the hash-chained ledger,
so the dashboard, WhatsApp and the audit trail always agree."""
import datetime, math, re, time, uuid
from decimal import Decimal as D
from sqlalchemy import select
import db, defaults, engine as E, hub, i18n, money as M, notify, svc
from svc import Err, LED, _leader_only, _log, _order, _set, _tell

def _actor(u): return f"{u['role']}:{u['id']}"
def _int(v, lo, hi, msg):
    try: n = int(v); assert lo <= n <= hi; return n
    except Exception: raise Err(422, msg)
def _num(v, msg, lo=0.0, hi=1e9, allow_zero=False):
    try: x = float(v); assert (x >= lo if allow_zero else x > lo) and x <= hi; return x
    except Exception: raise Err(422, msg)
def _clean(x, n): return re.sub(r"\s+", " ", str(x or "")).strip()[:n]
def _today(): return datetime.datetime.fromtimestamp(time.time() + hub.TZ_MIN * 60, datetime.timezone.utc).date()      # "today" in the group's local time

# ---------- contact for the buyer of an order ----------
def buyer_contact(s, o):
    """(key, name, phone) for an order's buyer: a registered buyer's own number, or the number the leader saved for a buyer she ordered for."""
    od, u = o.data["order"], s.get(db.User, o.buyer_id)
    if u and u.role == "buyer": return f"u:{u.id}", (od.get("buyer_name") or u.name), u.phone
    name = od.get("buyer_name") or "Walk-in buyer"; key = hub._bkey(name)
    p = s.scalars(select(db.BuyerProfile).where(db.BuyerProfile.group_id == o.group_id, db.BuyerProfile.key == key)).first()
    return key, name, (p.phone if p else None)
def _tell_buyer(s, o, text):
    _k, _n, phone = buyer_contact(s, o)
    return bool(phone and notify.send(phone, text))
def _profile(s, gid, key, name=None):
    p = s.scalars(select(db.BuyerProfile).where(db.BuyerProfile.group_id == gid, db.BuyerProfile.key == key)).first()
    if not p: p = db.BuyerProfile(group_id=gid, key=key, name=name, addresses=[], notes="", created=time.time()); s.add(p); s.flush()
    return p

# ---------- place an order on behalf of a buyer ----------
def create_order(user, d):
    """The leader places an order for a buyer: name, product, quantity, price, delivery date and place, specifications, optional photo."""
    _leader_only(user)
    name = _clean(d.get("buyer_name"), 60); product = _clean(d.get("product"), 60)
    if not name: raise Err(422, "Please give the buyer's name or business name")
    if not product: raise Err(422, "Please say what product it is, for example cloth bags")
    qty = _int(d.get("qty"), 1, 100000, "Quantity must be a whole number of pieces")
    price = _num(d.get("offered_price"), "Price per piece must be a number above 0", hi=100000); price = int(price) if price == int(price) else round(price, 2)
    if d.get("delivery_date"):
        try: days = (datetime.date.fromisoformat(str(d["delivery_date"])) - _today()).days
        except Exception: raise Err(422, "Delivery date must look like 2026-11-20")
        if days < 1: raise Err(422, "The delivery date must be tomorrow or later")
    else: days = _int(d.get("deadline_days"), 1, 365, "Give a delivery date, or the number of days")
    od = {"product": product, "qty": qty, "offered_price": price, "deadline_days": days, "colour": _clean(d.get("colour"), 40) or None, "size": _clean(d.get("size"), 40) or None, "specs": _clean(d.get("specs"), 300) or None,
          "packaging": _clean(d.get("packaging"), 120) or None, "location": _clean(d.get("location"), 160) or None, "delivery_date": str(d["delivery_date"]) if d.get("delivery_date") else None, "buyer_name": name}
    phone = None
    if d.get("buyer_phone"):
        from auth import norm
        if len("".join(c for c in str(d["buyer_phone"]) if c.isdigit())) < 10: raise Err(422, "Please give a 10-digit phone number, or leave it blank")
        phone = norm(d["buyer_phone"])
    ev = {"source": "leader", "text": _clean(d.get("note"), 500) or f"{qty} {product} for {name}"}
    ph = svc._img_ok(d.get("photo"))
    if ph: ev["photo"] = ph
    with db.S() as s:
        reg = s.scalars(select(db.User).where(db.User.phone == phone)).first() if phone else None
        if reg and not (reg.role == "buyer"): raise Err(409, "That number belongs to a group member or leader, not a buyer")
    v = svc.submit_order(user, user["group_id"], ev["text"], pre={"order": od, "unclear": [], "evidence": ev}, for_buyer=reg.id if reg else None)
    with db.S() as s:
        o = s.get(db.Order, v["id"]); key, nm, _p = buyer_contact(s, o); p = _profile(s, user["group_id"], key, nm)
        if phone and not reg: p.phone = phone
        if od["location"] and od["location"] not in (p.addresses or []): p.addresses = list(p.addresses or []) + [od["location"]]
        s.commit()
    return v

def repeat_order(user, oid, qty=None, offered_price=None, deadline_days=None, delivery_date=None):
    """Repeat order from a buyer's saved specification: copies product, size, colour, specs, packaging and place from an earlier order."""
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); od = dict(o.data["order"]); key, name, phone = buyer_contact(s, o)
    nd = {k: od.get(k) for k in ("product", "colour", "size", "specs", "packaging", "location")}
    nd.update(buyer_name=name, buyer_phone=phone, qty=qty or od["qty"], offered_price=offered_price or od["offered_price"], note=f"Repeat of order #{oid}")
    if delivery_date: nd["delivery_date"] = delivery_date
    else: nd["deadline_days"] = deadline_days or od.get("deadline_days") or 7
    if not nd["product"]: nd["product"] = "order"
    return create_order(user, nd)

# ---------- money received from the buyer ----------
def record_payment(user, oid, amount, kind=None, method="", note=""):
    _leader_only(user)
    try: amt = E.q(D(str(amount))); assert 0 < amt <= D("100000000")
    except Exception: raise Err(422, "Amount must be a number above 0")
    now = time.time()
    with db.S() as s:
        o = _order(s, oid, user)
        if o.status not in M.OWED_FROM: raise Err(409, "You can record money after the buyer accepts the quote, and not on a cancelled order")
        ms = M.summary(o, now); bal = D(ms["balance"])
        if amt > bal: raise Err(422, f"That is more than the balance of Rs {bal}")
        kind = kind if kind in ("advance", "payment") else ("advance" if o.status in ("accepted", "planned", "in_production", "payout_pending") else "payment")
        m = dict(o.data.get("money") or {}); pays = list(m.get("payments", []))
        pays.append({"i": len(pays), "amount": str(amt), "kind": kind, "method": _clean(method, 30), "note": _clean(note, 200), "ts": now, "by": user["id"]}); m["payments"] = pays; _set(o, money=m)
        _log(s, o, "money.received", user, {"amount": str(amt), "kind": kind, "balance": str(bal - amt), "total": ms["total"]}); s.commit()
        left = bal - amt; _tell_buyer(s, o, f"Thank you. The group recorded Rs {amt} for order #{o.id}. " + ("Nothing more is due." if left <= 0 else f"Balance due: Rs {left}."))
        return M.summary(o, now)

def set_due_date(user, oid, due_date):
    _leader_only(user)
    try: datetime.date.fromisoformat(str(due_date))
    except Exception: raise Err(422, "Due date must look like 2026-11-20")
    with db.S() as s:
        o = _order(s, oid, user); m = dict(o.data.get("money") or {}); m["due_date"] = str(due_date); _set(o, money=m); _log(s, o, "money.terms", user, {"due_date": str(due_date)}); s.commit()
        return M.summary(o, time.time())

REMIND_GAP_H = 6
def remind_buyer(user, oid):
    _leader_only(user); now = time.time()
    with db.S() as s:
        o = _order(s, oid, user); ms = M.summary(o, now)
        if o.status not in M.OWED_FROM or D(ms["balance"]) <= 0: raise Err(409, "Nothing is due on this order")
        m = dict(o.data.get("money") or {}); rem = list(m.get("reminders", []))
        if rem and now - rem[-1] < REMIND_GAP_H * 3600: raise Err(409, f"The buyer was already reminded {int((now - rem[-1]) / 60)} minutes ago")
        g = s.get(db.Group, o.group_id)
        late = f" It is {ms['days_overdue']} day(s) overdue." if ms["overdue"] else ""
        if not _tell_buyer(s, o, f"Reminder from {g.name if g else 'your SHG group'}: Rs {ms['balance']} is due for order #{o.id}.{late} Please pay and tell the group leader. Thank you."):
            raise Err(409, "I could not message this buyer: no phone number is saved, or WhatsApp is not connected. Add the number on the Buyers tab, or call them.")
        rem.append(now); m["reminders"] = rem; _set(o, money=m); _log(s, o, "buyer.reminded", user, {"balance": ms["balance"]}); s.commit()
        return {"ok": True, "balance": ms["balance"]}

# ---------- private notes ----------
def add_note(user, oid, text):
    _leader_only(user); text = str(text or "").strip()[:2000]
    if not text: raise Err(422, "Write the note first")
    with db.S() as s:
        o = _order(s, oid, user); notes = list(o.data.get("notes", [])); notes.append({"i": len(notes), "text": text, "ts": time.time(), "by": user["id"]}); _set(o, notes=notes)
        _log(s, o, "order.note", user, {"len": len(text)}); s.commit()           # the ledger records THAT a note was added, never what it says
        return notes

# ---------- move pieces from one member to another ----------
def _recheck(plan, caps, days):
    load, issues = {}, []
    for x in plan["assignments"]: load[x["member"]] = load.get(x["member"], 0) + x["units"] / max(caps.get(x["member"], 1), 1)
    names = {x["member"]: x["name"] for x in plan["assignments"]}
    for m, v in load.items():
        if v > days: issues.append(f"{names[m]} needs {E.q(D(v))} days of work but has {days}")
    plan["issues"], plan["feasible"] = issues, not issues; plan["load_days"] = {names[m]: str(E.q(D(v))) for m, v in load.items()}
def reassign(user, oid, from_id, to_id, stage, pieces):
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user)
        if o.status not in ("planned", "in_production"): raise Err(409, "Work can be moved while the order is planned or in production")
        plan = {**o.data["plan"], "assignments": [dict(x) for x in o.data["plan"]["assignments"]]}
        x = next((x for x in plan["assignments"] if x["member"] == from_id and x["stage"] == stage), None)
        if not x: raise Err(404, "She has no such task on this order")
        if from_id == to_id: raise Err(422, "Choose a different member")
        n = _int(pieces, 1, x["units"], f"Move between 1 and {x['units']} pieces")
        done = min(int(o.data.get("progress", {}).get(f"{from_id}:{stage}", 0)), x["units"]) if o.status == "in_production" else 0
        if x["units"] - n < done: raise Err(422, f"{x['name']} has already reported {done} pieces. You can move at most {x['units'] - done}.")
        to = s.get(db.User, to_id)
        if not to or to.role != "seller" or to.group_id != o.group_id or not to.active: raise Err(404, "That member is not available")
        if stage not in (to.skills or []): raise Err(422, f"{to.name} does not have the {stage} skill")
        x["units"] -= n; x["expected_pay"] = str(E.q(D(x["units"]) * D(str(x["rate"]))))
        t = next((y for y in plan["assignments"] if y["member"] == to_id and y["stage"] == stage), None)
        if t: t["units"] += n; t["expected_pay"] = str(E.q(D(t["units"]) * D(str(t["rate"]))))
        else: t = {"member": to.id, "name": to.name, "stage": stage, "units": n, "rate": x["rate"], "expected_pay": str(E.q(D(n) * D(str(x["rate"])))), "why": "Moved by the leader"}; plan["assignments"].append(t)
        fr = s.get(db.User, from_id)
        if x["units"] == 0: plan["assignments"].remove(x)
        caps = {u.id: (u.capacity or 1) for u in s.scalars(select(db.User).where(db.User.group_id == o.group_id))}; _recheck(plan, caps, int(o.data["order"]["deadline_days"]))
        acc = [a for a in o.data.get("task_accepted", []) if a != to_id] if o.status == "planned" else list(o.data.get("task_accepted", []))        # she accepts her bigger task again before work starts
        _set(o, plan=plan, task_accepted=acc); _log(s, o, "work.reassigned", user, {"from": from_id, "to": to_id, "stage": stage, "pieces": n, "member": to_id}); s.commit()
        notify.send(to.phone, i18n.t("task_moved_in", to.lang, o=o.id, n=n, stage=i18n.stage(stage, to.lang), tot=t["units"], pay=t["expected_pay"]))
        if fr: notify.send(fr.phone, i18n.t("task_moved_out", fr.lang, o=o.id, n=n, stage=i18n.stage(stage, fr.lang), tot=x["units"]))
        return svc._view(s, o, user)

def resolve_issue(user, oid, idx, note=""):
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); iss = [dict(x) for x in o.data.get("issues", [])]
        k = next((k for k, x in enumerate(iss) if x.get("i", k) == idx), None)
        if k is None: raise Err(404, "Problem not found")
        iss[k].update(open=False, note=_clean(note, 300), resolved_ts=time.time()); _set(o, issues=iss); _log(s, o, "issue.resolved", user, {"i": idx}); s.commit()
        m = s.get(db.User, iss[k]["uid"])
        if m: notify.send(m.phone, f"Order #{o.id}: your leader saw your problem and sorted it. " + (iss[k]["note"] if iss[k]["note"] else ""))
        return {"ok": True}

# ---------- members ----------
def update_member(user, mid, name=None, skills=None, capacity=None, lang=None, active=None, phone=None):
    _leader_only(user)
    with db.S() as s:
        m = s.get(db.User, mid)
        if not m or m.role != "seller" or m.group_id != user["group_id"]: raise Err(404, "Member not found")
        if name is not None:
            if not _clean(name, 80): raise Err(422, "Name cannot be empty")
            m.name = _clean(name, 80)
        if skills is not None: m.skills = [k for k in dict.fromkeys(str(x).strip().lower()[:30] for x in skills) if k]
        if capacity is not None: m.capacity = _int(capacity, 0, 10000, "Daily capacity must be a number of pieces")
        if lang is not None:
            if lang not in ("en", "hi", "hing"): raise Err(422, "Language must be en, hi or hing")
            m.lang = lang
        if phone is not None and phone != m.phone:
            from auth import norm
            if len("".join(c for c in str(phone) if c.isdigit())) < 10: raise Err(422, "Please give a 10-digit phone number")
            p = norm(phone)
            if s.scalars(select(db.User).where(db.User.phone == p, db.User.id != m.id)).first(): raise Err(409, "Phone already registered")
            m.phone = p
        if active is not None and bool(active) != bool(m.active):
            if not active:
                busy = [o.id for o in s.scalars(select(db.Order).where(db.Order.group_id == m.group_id, db.Order.status.in_(("planned", "in_production")))) if any(x["member"] == m.id for x in (o.data.get("plan") or {}).get("assignments", []))]
                if busy: raise Err(409, f"{m.name} still has work on order #{busy[0]}. Move her pieces to someone else first (Orders > Work > Move pieces).")
            m.active = bool(active)
            LED.append("member.deactivated" if not active else "member.updated", {"name": m.name, "id": m.id, "group": m.group_id}, _actor(user), s=s)
        else: LED.append("member.updated", {"name": m.name, "id": m.id, "group": m.group_id}, _actor(user), s=s)
        s.commit(); return {"ok": True, "id": m.id}

def send_message(user, to, text=None, template=None):
    _leader_only(user); gid = user["group_id"]
    with db.S() as s:
        q = select(db.User).where(db.User.group_id == gid, db.User.role == "seller", db.User.active == True)  # noqa: E712
        if to != "all": q = q.where(db.User.id == _int(to, 1, 10**9, "Choose who to message"))
        ms = list(s.scalars(q))
        if not ms: raise Err(404, "No such member")
        if template:
            if template not in hub.TEMPLATES: raise Err(422, "Unknown template")
        else:
            text = _clean(text, 500)
            if not text: raise Err(422, "Write a message first")
        batch, res = uuid.uuid4().hex[:8], []
        for m in ms:
            body = hub.TEMPLATES[template].get(m.lang or "en", hub.TEMPLATES[template]["en"]) if template else text
            r = notify.send_ex(m.phone, f"{user['name']} (group leader): {body}")
            st = "sent" if r["ok"] else ("not_connected" if r.get("reason") == "not_configured" else "failed")
            s.add(db.Message(group_id=gid, sender_id=user["id"], to_id=m.id, body=body, status=st, sid=r.get("sid"), ts=time.time(), batch=batch)); res.append({"member": m.id, "name": m.name, "status": st})
        LED.append("message.sent", {"to": to, "n": len(ms), "group": gid}, _actor(user), s=s); s.commit()
        return {"batch": batch, "results": res}
_RANK = {"queued": 0, "not_connected": 0, "sent": 1, "delivered": 2, "read": 3}
def message_status(sid, status):
    """Twilio status callback: queued -> sent -> delivered -> read, or failed. Never moves a message backwards."""
    st = "failed" if status in ("failed", "undelivered") else status
    if not sid or st not in ("queued", "sent", "delivered", "read", "failed"): return False
    with db.S() as s:
        m = s.scalars(select(db.Message).where(db.Message.sid == sid)).first()
        if not m: return False
        if st == "failed" or _RANK.get(st, 0) > _RANK.get(m.status, 0): m.status = st; s.commit()
        return True

def set_prefs(user, whatsapp_alerts):
    _leader_only(user)
    with db.S() as s:
        u = s.get(db.User, user["id"]); u.prefs = {**(u.prefs or {}), "whatsapp_alerts": bool(whatsapp_alerts)}; s.commit(); return u.prefs
def get_prefs(user):
    with db.S() as s: return {"whatsapp_alerts": (s.get(db.User, user["id"]).prefs or {}).get("whatsapp_alerts", True)}

# ---------- buyers ----------
def update_buyer(user, key, notes=None, phone=None, address=None):
    _leader_only(user)
    with db.S() as s:
        ctx = hub.Ctx(s, user); cs = hub._buyers(ctx).get(key)
        if not cs: raise Err(404, "Buyer not found")
        b = cs[0]["buyer"]; p = _profile(s, user["group_id"], key, b["name"])
        if notes is not None: p.notes = str(notes)[:2000]
        if phone not in (None, ""):
            if b["registered"]: raise Err(409, "This buyer registered with their own number; it cannot be changed here")
            from auth import norm
            if len("".join(c for c in str(phone) if c.isdigit())) < 10: raise Err(422, "Please give a 10-digit phone number")
            p.phone = norm(phone)
        if address and _clean(address, 160) not in (p.addresses or []): p.addresses = list(p.addresses or []) + [_clean(address, 160)]
        LED.append("buyer.updated", {"group": user["group_id"]}, _actor(user), s=s); s.commit(); return {"ok": True}

def rate_group(user, oid, stars, comment=""):
    """The buyer rates the group after delivery (1 to 5). The leader sees it on the buyer's profile."""
    if user["role"] != "buyer": raise Err(403, "Only the buyer can rate the group")
    n = _int(stars, 1, 5, "Rate from 1 to 5")
    with db.S() as s:
        o = _order(s, oid, user)
        if o.status != "closed": raise Err(409, "You can rate the group once the order is delivered")
        if o.data.get("rating"): raise Err(409, "You already rated this order. Thank you!")
        _set(o, rating={"stars": n, "comment": _clean(comment, 300), "ts": time.time()}); _log(s, o, "buyer.rated", user, {"stars": n}); s.commit()
        _tell(s, o.group_id, "leader", f"The buyer rated order #{o.id}: {n}/5.")
        return {"ok": True, "stars": n}

# ---------- materials (optional) ----------
def _mat(s, user, mid):
    m = s.get(db.Material, mid)
    if not m or m.group_id != user["group_id"]: raise Err(404, "Material not found")
    return m
def _mlog(s, user, what): LED.append("material.updated", {"what": what, "group": user["group_id"]}, _actor(user), s=s)
def save_material(user, mid=None, name=None, unit=None, qty=None, per_piece=None, active=None):
    _leader_only(user)
    with db.S() as s:
        if mid: m = _mat(s, user, mid)
        else:
            if not _clean(name, 60): raise Err(422, "Give the material a name, for example cloth")
            m = db.Material(group_id=user["group_id"], name=_clean(name, 60), unit=_clean(unit, 12) or "pcs", qty=0, per_piece={}); s.add(m); s.flush()
        if mid and name is not None: m.name = _clean(name, 60) or m.name
        if unit is not None and mid: m.unit = _clean(unit, 12) or m.unit
        if qty is not None: m.qty = _num(qty, "Quantity must be a number", allow_zero=True)
        if per_piece is not None:
            if not isinstance(per_piece, dict): raise Err(422, "Say how much is used per piece for each product")
            m.per_piece = {k: _num(v, "Use per piece must be a number", allow_zero=True) for k, v in per_piece.items() if k in defaults.TEMPLATES and v not in (None, "")}
        if active is not None: m.active = bool(active)
        _mlog(s, user, f"{m.name} saved"); s.commit(); return {"ok": True, "id": m.id}
def save_supplier(user, sid=None, name=None, phone=None, note=None):
    _leader_only(user)
    with db.S() as s:
        if sid:
            x = s.get(db.Supplier, sid)
            if not x or x.group_id != user["group_id"]: raise Err(404, "Supplier not found")
        else:
            if not _clean(name, 60): raise Err(422, "Give the supplier a name")
            x = db.Supplier(group_id=user["group_id"], name=_clean(name, 60)); s.add(x); s.flush()
        if sid and name is not None: x.name = _clean(name, 60) or x.name
        if phone is not None: x.phone = _clean(phone, 20)
        if note is not None: x.note = _clean(note, 200)
        _mlog(s, user, f"supplier {x.name}"); s.commit(); return {"ok": True, "id": x.id}
def add_purchase(user, material_id, qty, cost, supplier_id=None, supplier_name=None, note=""):
    _leader_only(user)
    with db.S() as s:
        m = _mat(s, user, material_id); q = _num(qty, "Quantity must be above 0"); c = _num(cost, "Total cost must be a number", allow_zero=True)
        sup = None
        if supplier_id:
            sup = s.get(db.Supplier, supplier_id)
            if not sup or sup.group_id != user["group_id"]: raise Err(404, "Supplier not found")
        elif _clean(supplier_name, 60):
            sup = db.Supplier(group_id=user["group_id"], name=_clean(supplier_name, 60)); s.add(sup); s.flush()
        s.add(db.Purchase(group_id=user["group_id"], material_id=m.id, supplier_id=sup.id if sup else None, qty=q, cost=c, ts=time.time(), note=_clean(note, 120))); m.qty = (m.qty or 0) + q
        _mlog(s, user, f"bought {q:g} {m.unit} {m.name}"); s.commit(); return {"ok": True}
def use_material(user, oid, material_id, qty):
    """Record material actually used on an order: reduces stock and adds to that order's actual material cost (at the average price paid)."""
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user); m = _mat(s, user, material_id); q = _num(qty, "Quantity must be above 0")
        if q > (m.qty or 0) + 1e-9: raise Err(409, f"Only {m.qty:g} {m.unit} of {m.name} in stock")
        avg = hub._avg_price(hub.Ctx(s, user)).get(m.id, 0.0); cost = round(q * avg, 2)
        used = list(o.data.get("materials_used", [])); used.append({"material_id": m.id, "name": m.name, "unit": m.unit, "qty": q, "cost": cost, "ts": time.time()}); _set(o, materials_used=used)
        m.qty = (m.qty or 0) - q; _mlog(s, user, f"used {q:g} {m.unit} {m.name} on order #{o.id}"); s.commit(); return {"ok": True, "cost": cost}
