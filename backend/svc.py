"""Business rules shared by the REST dashboards and the WhatsApp bot. Every function checks the caller's role and scope."""
import hashlib, math, os, time
from sqlalchemy import func, select
import db, engine as E, i18n, llm, notify, screening
import defaults, ratecards

LED = db.DBLedger()
QUORUM = int(os.getenv("PAYOUT_MEMBER_QUORUM", "2"))   # members who must approve besides the leader
ALL_STAGES = list(dict.fromkeys(st["name"] for t in defaults.TEMPLATES.values() for st in t["stages"]))
def _card(o):
    """The rate card this order was priced on (snapshot). Orders made before rate cards existed use the cloth-bag default."""
    return o.data.get("rate_card") or ratecards.default_card(defaults.DEFAULT_PRODUCT)
def _product_key(value):
    value = str(value or "").strip().lower()
    return value if value in defaults.TEMPLATES else defaults.detect(value)
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
        g = s.get(db.Group, o.group_id)
        payments = _buyer_payment_rows(s, o.id)
        rating = s.scalars(select(db.BuyerRating).where(db.BuyerRating.order_id == o.id)).first()
        return {**v, "group": g.name if g else "", "progress_pct": pg["pct"],
                "buyer_payment": {"status": payments[-1]["status"], "amount": payments[-1]["amount"],
                                  "note": payments[-1]["note"]} if payments else {"status": "not_recorded"},
                "rating": {"stars": rating.stars, "comment": rating.comment} if rating else None}
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

def order_health(order, now=None):
    """Return the leader-facing health label from the order's persisted facts."""
    now = time.time() if now is None else now
    status = order.get("status")
    if status not in ("closed", "cancelled") and order.get("due_at") is not None and order["due_at"] < now:
        return "Late"
    if status == "blocked" or (order.get("delivery") or {}).get("blocked"):
        return "Blocked"
    if (order.get("risk") or {}).get("shortfall"):
        return "At risk"
    if order.get("needs_leader"):
        return "Waiting"
    stale_hours = max(1, int(os.getenv("STALE_HOURS", "24")))
    if status == "in_production":
        last_report = order.get("last_reported_at") or order.get("started_at") or order.get("created")
        if last_report is None or now - last_report >= stale_hours * 3600:
            return "Stale"
    return "On track"

def leader_overview(user, now=None, summary_only=False):
    """Leader-scoped overview data assembled from the same orders and ledger as WhatsApp."""
    _leader_only(user)
    now = time.time() if now is None else now
    day_start = now - ((now + 5 * 3600 + 30 * 60) % 86400)
    with db.S() as s:
        group = s.get(db.Group, user["group_id"])
        latest_seq = s.scalar(select(func.max(db.LedgerEvent.i)))
        orders = list(s.scalars(select(db.Order).where(db.Order.group_id == user["group_id"]).order_by(db.Order.id.desc())))
        order_ids = [o.id for o in orders]
        events = list(s.scalars(select(db.LedgerEvent).where(db.LedgerEvent.order_id.in_(order_ids)).order_by(db.LedgerEvent.i))) if order_ids else []
        latest_report, started_at, pieces_today = {}, {}, {}
        previous_done = {}
        for event in events:
            if event.type == "production.started":
                started_at[event.order_id] = event.ts
            if event.type != "progress.reported":
                continue
            latest_report[event.order_id] = max(latest_report.get(event.order_id, 0), event.ts or 0)
            payload = event.payload or {}
            key = (event.order_id, event.actor, payload.get("stage"))
            done = int(payload.get("done") or 0)
            if (event.ts or 0) >= day_start:
                pieces_today[event.order_id] = pieces_today.get(event.order_id, 0) + max(0, done - previous_done.get(key, 0))
            previous_done[key] = done

        buyers = {u.id: u.name for u in s.scalars(select(db.User).where(db.User.id.in_({o.buyer_id for o in orders})))} if orders else {}
        rows, queue = [], []
        active_statuses = {"submitted", "quoted", "accepted", "planned", "in_production", "payout_pending", "settled", "ready_for_delivery", "dispatched", "partially_delivered", "blocked"}
        queue_status = {"submitted": ("Send quote", "quote"), "planned": ("Approve and start plan", "start"),
                        "payout_pending": ("Review payout approvals", "payout")}
        for o in orders:
            v = _view(s, o, user)
            quality_waiting = [q for q in v.get("quality", []) if q.get("status") == "pending"]
            disputes = [d for d in v.get("disputes", []) if d.get("open")]
            declined = v.get("declined", [])
            needs_leader = o.status in queue_status or bool(quality_waiting or disputes or (declined and o.status == "planned"))
            v["buyer_name"] = buyers.get(o.buyer_id, (v.get("order") or {}).get("buyer_name", "Buyer"))
            v["last_reported_at"] = latest_report.get(o.id)
            v["started_at"] = started_at.get(o.id)
            v["needs_leader"] = needs_leader
            v["health"] = order_health(v, now)
            v["pieces_finished_today"] = pieces_today.get(o.id, 0)
            assignments = (v.get("progress") or {}).get("rows", [])
            by_stage = {}
            for assignment in assignments:
                stage = assignment["stage"]
                entry = by_stage.setdefault(stage, {"name": stage, "done": 0, "units": 0})
                entry["done"] += assignment.get("done", 0)
                entry["units"] += assignment.get("units", 0)
            v["stages"] = [{**stage, "pct": int(stage["done"] * 100 / stage["units"]) if stage["units"] else 0} for stage in by_stage.values()]
            rows.append(v)
            if o.status in queue_status:
                label, action = queue_status[o.status]
                queue.append({"order_id": o.id, "kind": o.status, "label": label, "action": action})
            queue.extend({"order_id": o.id, "kind": "sample", "label": f"Review {q.get('name', 'member')}'s sample", "action": "quality"}
                         for q in quality_waiting)
            queue.extend({"order_id": o.id, "kind": "dispute", "label": f"Resolve pay dispute from {d.get('name', 'member')}", "action": "payout"}
                         for d in disputes)
            if declined and o.status == "planned":
                queue.append({"order_id": o.id, "kind": "declined", "label": "Replan after a task decline", "action": "replan"})
            if o.status == "blocked":
                queue.append({"order_id": o.id, "kind": "blocked", "label": "Unblock or review blocked delivery", "action": "delivery"})
        proposed = list(s.scalars(select(db.RateCard).where(db.RateCard.group_id == user["group_id"], db.RateCard.status == "proposed")))
        queue.extend({"order_id": None, "ratecard_id": card.id, "kind": "ratecard", "label": f"Rate-card vote needed: {card.product} v{card.version}", "action": "pricing"} for card in proposed)
        active_orders = [o for o in orders if o.status in active_statuses]
        group_name = group.name if group else ""
        buyer_money_due = 0.0
        for order in orders:
            payments = _buyer_payment_rows(s, order.id)
            if not payments: continue
            latest = payments[-1]
            billed = float(order.data.get("quote") or order.data["order"].get("offered_price") or 0) * int(order.data["order"]["qty"])
            if latest["status"] == "unpaid": buyer_money_due += float(latest["amount"] or billed)
            elif latest["status"] == "advance": buyer_money_due += max(0, billed - float(latest["amount"] or 0))
        activity = [{"seq": event.i, "type": event.type, "order_id": event.order_id,
                     "summary": event.type.replace(".", " ").replace("_", " ").capitalize(),
                     "ts": event.ts} for event in events[-8:]]
        response = {"group": group_name, "kpis": {
                    "active_orders": len(active_orders),
                    "pieces_finished_today": sum(pieces_today.values()),
                    "orders_on_track": sum(1 for o in rows if o["status"] in active_statuses and o["health"] == "On track"),
                    "waiting_for_leader": len(queue),
                    "payout_waiting": sum(o.status == "payout_pending" for o in active_orders),
                    "buyer_money_due": round(buyer_money_due, 2)},
                "decision_queue": queue[:5], "decision_count": len(queue), "orders": rows,
                "activity": activity, "updated_at": now, "last_seq": -1 if latest_seq is None else latest_seq}
        if summary_only:
            response.pop("orders")
        return response

def leader_order_detail(user, oid):
    _leader_only(user)
    with db.S() as s:
        o = _order(s, oid, user)
        v = _view(s, o, user)
        buyer = s.get(db.User, o.buyer_id)
        v["buyer_name"] = buyer.name if buyer else (v.get("order") or {}).get("buyer_name", "Buyer")
        payments = _buyer_payment_rows(s, o.id)
        history = list(s.scalars(select(db.Order).where(
            db.Order.buyer_id == o.buyer_id, db.Order.group_id == user["group_id"],
            db.Order.id != o.id).order_by(db.Order.id.desc()).limit(5)))
        v["buyer"] = {"id": buyer.id if buyer else None, "name": buyer.name if buyer else "Buyer",
                      "past_orders": [{"id": previous.id, "status": previous.status,
                                       "product": (previous.data.get("order") or {}).get("product", "Order"),
                                       "qty": (previous.data.get("order") or {}).get("qty")}
                                      for previous in history]}
        v["buyer_payment"] = {"status": payments[-1]["status"] if payments else "not_recorded",
                              "payments": payments}
        v["notes"] = [{"id": note.id, "note": note.note, "created": note.created}
                      for note in s.scalars(select(db.OrderNote).where(db.OrderNote.order_id == o.id)
                                           .order_by(db.OrderNote.created.desc()))]
        latest_report = s.scalars(select(db.LedgerEvent).where(
            db.LedgerEvent.order_id == o.id, db.LedgerEvent.type == "progress.reported"
        ).order_by(db.LedgerEvent.i.desc())).first()
        started = s.scalars(select(db.LedgerEvent).where(
            db.LedgerEvent.order_id == o.id, db.LedgerEvent.type == "production.started"
        ).order_by(db.LedgerEvent.i.desc())).first()
        v["last_reported_at"] = latest_report.ts if latest_report else None
        v["started_at"] = started.ts if started else None
        v["needs_leader"] = (o.status in ("submitted", "planned", "payout_pending")
                             or any(q.get("status") == "pending" for q in v.get("quality", []))
                             or any(d.get("open") for d in v.get("disputes", [])))
        v["health"] = order_health(v)
        by_stage = {}
        for assignment in (v.get("progress") or {}).get("rows", []):
            entry = by_stage.setdefault(assignment["stage"], {"name": assignment["stage"], "done": 0, "units": 0})
            entry["done"] += assignment["done"]
            entry["units"] += assignment["units"]
        v["stages"] = [{**stage, "pct": int(stage["done"] * 100 / stage["units"]) if stage["units"] else 0}
                       for stage in by_stage.values()]
        return v

def leader_team(user, now=None):
    """Return group-scoped workload and availability without exposing individual pay."""
    _leader_only(user)
    now = time.time() if now is None else now
    tolerance = max(0, int(os.getenv("BEHIND_TOLERANCE_PCT", "15"))) / 100
    with db.S() as s:
        members = list(s.scalars(select(db.User).where(
            db.User.group_id == user["group_id"], db.User.role == "seller", db.User.active == True
        )).all())  # noqa: E712
        orders = list(s.scalars(select(db.Order).where(
            db.Order.group_id == user["group_id"],
            db.Order.status.in_(("planned", "in_production"))
        )).all())
        member_rows = {m.id: {"id": m.id, "name": m.name, "phone": m.phone, "language": m.lang or "en",
                              "skills": m.skills or [],
                              "capacity": m.capacity or 0, "last_active_at": m.last_active_at,
                              "tasks": [], "status": "Idle"} for m in members}
        order_ids = [o.id for o in orders]
        reports = list(s.scalars(select(db.LedgerEvent).where(
            db.LedgerEvent.order_id.in_(order_ids), db.LedgerEvent.type == "progress.reported"
        ).order_by(db.LedgerEvent.i.desc())).all()) if order_ids else []
        latest_report = {}
        for event in reports:
            latest_report.setdefault((event.order_id, event.actor), event.ts)
        starts = {}
        for event in s.scalars(select(db.LedgerEvent).where(
            db.LedgerEvent.order_id.in_(order_ids), db.LedgerEvent.type == "production.started"
        ).order_by(db.LedgerEvent.i)).all() if order_ids else []:
            starts.setdefault(event.order_id, event.ts)

        for order in orders:
            for assignment in (order.data.get("plan") or {}).get("assignments", []):
                row = member_rows.get(assignment["member"])
                if row is None:
                    continue
                done = min(int((order.data.get("progress") or {}).get(_key(assignment), 0)), assignment["units"])
                due_at = order.data.get("due_at")
                started_at = starts.get(order.id)
                elapsed = (now - started_at) / (due_at - started_at) if started_at and due_at and due_at > started_at else 0
                pace = (done / assignment["units"]) if assignment["units"] else 1
                behind = order.status == "in_production" and pace + tolerance < min(max(elapsed, 0), 1)
                row["tasks"].append({"order_id": order.id, "stage": assignment["stage"],
                                     "units": assignment["units"], "done": done,
                                     "due_at": due_at, "status": order.status,
                                     "work_status": "Behind" if behind else "Working",
                                     "last_reported_at": latest_report.get((order.id, f"seller:{assignment['member']}"))})
        for row in member_rows.values():
            if row["tasks"]:
                row["status"] = "Behind" if any(t["work_status"] == "Behind" for t in row["tasks"]) else "Working"
        return list(member_rows.values())

# ---- buyer ----
def groups():
    with db.S() as s:
        out = []
        for g in s.scalars(select(db.Group)):
            orders = list(s.scalars(select(db.Order).where(db.Order.group_id == g.id)))
            closed = [o for o in orders if o.status == "closed"]
            ratings = list(s.scalars(select(db.BuyerRating).where(db.BuyerRating.group_id == g.id)))
            on_time = sum(1 for o in closed if (o.data or {}).get("due_at") and
                          max((x.get("ts", 0) for x in ((o.data.get("delivery") or {}).get("shipments") or [])), default=0) <= o.data["due_at"])
            first_samples = {}
            for order in closed:
                for sample in order.data.get("quality", []):
                    key = (order.id, sample.get("member"), sample.get("stage"))
                    if key not in first_samples or sample.get("i", 0) < first_samples[key].get("i", 0):
                        first_samples[key] = sample
            passed_first = sum(sample.get("status") == "passed" for sample in first_samples.values())
            sample_count = len(first_samples)
            out.append({"id": g.id, "name": g.name, "village": g.village, "orders_delivered": len(closed),
                        "on_time_pct": round(on_time * 100 / len(closed)) if len(closed) >= 3 else None,
                        "average_rating": round(sum(r.stars for r in ratings) / len(ratings), 1) if len(closed) >= 3 and ratings else None,
                        "first_pass_quality_pct": round(passed_first * 100 / sample_count) if len(closed) >= 3 and sample_count else None,
                        "rating_count": len(ratings) if len(closed) >= 3 else None,
                        "passport_note": "Based on recorded closed orders; fewer than 3 deliveries is too little data." if len(closed) < 3 else ""})
        return out

def preferred_group(user):
    with db.S() as s:
        u = s.get(db.User, user["id"])
        return u.preferred_group_id if u else None

def set_preferred_group(user, group_id):
    if user["role"] != "buyer": raise Err(403, "Only buyers can select an order group")
    try: group_id = int(group_id)
    except (TypeError, ValueError): raise Err(422, "Choose a listed group")
    with db.S() as s:
        if not s.get(db.Group, group_id): raise Err(404, "Group not found")
        u = s.get(db.User, user["id"])
        u.preferred_group_id = group_id
        s.commit()
    return {"ok": True, "group_id": group_id}

def last_order(user):
    if user["role"] != "buyer": raise Err(403, "Only buyers can repeat an order")
    with db.S() as s:
        order = s.scalars(select(db.Order).where(db.Order.buyer_id == user["id"]).order_by(db.Order.id.desc())).first()
        return {"order": {**dict(order.data.get("order") or {}),
                          "product_key": order.data.get("product_key")}, "group_id": order.group_id} if order else None

def buyer_home(user):
    if user["role"] != "buyer": raise Err(403, "Only buyers can view this dashboard")
    with db.S() as s:
        profile = s.get(db.User, user["id"])
        orders = list(s.scalars(select(db.Order).where(db.Order.buyer_id == user["id"])).all())
        order_ids = [order.id for order in orders]
        activity_rows = list(s.scalars(select(db.LedgerEvent).where(db.LedgerEvent.order_id.in_(order_ids))
                                       .order_by(db.LedgerEvent.i.desc()).limit(50))) if order_ids else []
        by_order = {order.id: order for order in orders}
        import events as event_projection
        activity = []
        for event in activity_rows:
            item = event_projection.project(event, user, by_order.get(event.order_id))
            if item: activity.append(item)
            if len(activity) == 5: break
        return {"orders": my_orders(user), "groups": groups(),
                "preferred_group_id": profile.preferred_group_id,
                "profile": {"name": profile.name, "language": profile.lang or "en",
                            "consented": bool(profile.consent_at)},
                "last_order": last_order(user), "activity": activity}

def buyer_products(user):
    if user["role"] != "buyer": raise Err(403, "Only buyers can view these products")
    return [{"key": key, "label": product["label"], "unit": product["unit"]}
            for key, product in defaults.TEMPLATES.items()]

def buyer_submit_order(user, group_id, details):
    if user["role"] != "buyer": raise Err(403, "Only buyers can place orders")
    try: group_id = int(group_id)
    except (TypeError, ValueError): raise Err(422, "Choose a group")
    try:
        qty = int(details.get("qty"))
        days = int(details.get("deadline_days"))
        offered = float(details.get("offered_price"))
    except (TypeError, ValueError):
        raise Err(422, "Enter a quantity, price per piece and delivery days")
    if qty < 1 or qty > 100000: raise Err(422, "Quantity must be between 1 and 100,000 pieces")
    if days < 1 or days > 365: raise Err(422, "Delivery time must be between 1 and 365 days")
    if not math.isfinite(offered) or offered <= 0: raise Err(422, "Price per piece must be positive")
    product_key = _product_key(details.get("product"))
    if product_key not in defaults.TEMPLATES: raise Err(422, "Choose a listed product")
    order = {"product": defaults.TEMPLATES[product_key]["label"], "product_key": product_key,
             "qty": qty, "offered_price": offered, "deadline_days": days,
             "colour": str(details.get("colour") or "")[:80],
             "specifications": str(details.get("specifications") or "")[:1000],
             "delivery_address": str(details.get("delivery_address") or "")[:300]}
    response = submit_order(user, group_id, "", {"order": order, "unclear": []})
    if user.get("id"):
        with db.S() as s:
            buyer = s.get(db.User, user["id"])
            if buyer and buyer.preferred_group_id != int(group_id):
                buyer.preferred_group_id = int(group_id); s.commit()
    return response

def handoff(user, group_id, message):
    """Notify only the chosen group's active leaders; never broadcast buyer details across groups."""
    if not group_id and user.get("role") == "buyer":
        rows = groups()
        if len(rows) == 1: group_id = rows[0]["id"]
    if not group_id: return 0
    with db.S() as s:
        leaders = list(s.scalars(select(db.User).where(db.User.group_id == group_id, db.User.role == "leader", db.User.active == True)))  # noqa: E712
        for leader in leaders: notify.send(leader.phone, message)
        return len(leaders)

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
            leaders = s.scalars(select(db.User).where(
                db.User.group_id == g.id, db.User.role == "leader", db.User.active == True
            ))  # noqa: E712
            for leader in leaders:
                notify.send(leader.phone, i18n.t(
                    "new_order_notice", leader.lang or "en",
                    order=o.id, buyer=od.get("buyer_name") or user.get("name") or "Buyer",
                    qty=od["qty"], product=od.get("product") or defaults.DEFAULT_PRODUCT,
                    price=od["offered_price"], days=od["deadline_days"],
                ))
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
    _leader_only(user)
    with db.S() as s:
        p = norm(phone)
        if s.scalars(select(db.User).where(db.User.phone == p)).first(): raise Err(409, "Phone already registered")
        s.add(db.User(phone=p, name=str(name)[:80], role="seller", group_id=user["group_id"], skills=list(skills or []), capacity=int(capacity or 0))); s.commit()
        return {"ok": True}
def members(user):
    _leader_only(user)
    with db.S() as s:
        return [{"id": m.id, "name": m.name, "phone": m.phone, "skills": m.skills,
                 "capacity": m.capacity, "language": m.lang or "en", "last_active_at": m.last_active_at}
                for m in s.scalars(select(db.User).where(db.User.group_id == user["group_id"], db.User.role == "seller", db.User.active == True))]  # noqa: E712

# ---- seller (SHG member): sees ONLY her own lines ----
def tasks(user):
    with db.S() as s:
        out = []
        for o in s.scalars(select(db.Order).where(db.Order.group_id == user["group_id"]).order_by(db.Order.id.desc())):
            assignments = (o.data.get("plan") or {}).get("assignments", [])
            stage_progress = {}
            for assignment in assignments:
                stage = assignment["stage"]
                progress = stage_progress.setdefault(stage, {"name": stage, "units": 0, "done": 0})
                progress["units"] += assignment["units"]
                progress["done"] += min(int((o.data.get("progress") or {}).get(_key(assignment), 0)), assignment["units"])
            journey = [{**stage, "pct": int(100 * stage["done"] / stage["units"]) if stage["units"] else 0}
                       for stage in stage_progress.values()]
            for x in assignments:
                if x["member"] == user["id"]: out.append({"order_id": o.id, "status": o.status, **x, "accepted": user["id"] in o.data.get("task_accepted", []), "done": min(int(o.data.get("progress", {}).get(_key(x), 0)), x["units"]),
                                "product": (o.data.get("rate_card") or {}).get("product", defaults.DEFAULT_PRODUCT),
                                "quantity": o.data["order"]["qty"], "due_at": o.data.get("due_at"),
                                "days_left": _days_left(o), "journey": journey,
                                "quality": next(({"status": q["status"], "reason": q.get("reason", ""),
                                                  "rework": q.get("rework", 0), "ts": q.get("ts")}
                                                 for q in reversed(o.data.get("quality", []))
                                                 if q.get("member") == user["id"] and q.get("stage") == x["stage"]), None),
                                "checklist": [c["text"] for c in _checklist(o)] if o.status == "in_production" else []})
        return out
def earnings(user):
    with db.S() as s:
        out = []; lang = _lang(s, user["id"])
        for o in s.scalars(select(db.Order).where(db.Order.group_id == user["group_id"]).order_by(db.Order.id.desc())):
            for l in _lines(o):
                if l["member"] == user["id"]:
                    disputes = [{"open": d["open"], "reason": d["reason"], "note": d.get("note", ""),
                                "ts": d.get("ts")} for d in o.data.get("disputes", []) if d.get("uid") == user["id"]]
                    out.append({"order_id": o.id, "status": o.status, "line": l, "explain": i18n.explain(l, lang),
                                "approved_by_me": user["id"] in o.data["approvals"]["members"],
                                "leader_approved": bool(o.data["approvals"]["leader"]),
                                "disputes": disputes})
        return out

def seller_home(user, now=None):
    """Build the member's private live summary from her own tasks, pay lines and ledger events."""
    if user["role"] != "seller": raise Err(403, "Only a member can view this dashboard")
    now = time.time() if now is None else now
    mine, pays = tasks(user), earnings(user)
    week_start = now - 7 * 86400
    with db.S() as s:
        profile = s.get(db.User, user["id"])
        leaders = list(s.scalars(select(db.User).where(
            db.User.group_id == user["group_id"], db.User.role == "leader", db.User.active == True
        )).all())  # noqa: E712
        orders = list(s.scalars(select(db.Order).where(db.Order.group_id == user["group_id"])).all())
        order_ids = [o.id for o in orders]
        logs = list(s.scalars(select(db.LedgerEvent).where(
            db.LedgerEvent.order_id.in_(order_ids), db.LedgerEvent.actor == f"seller:{user['id']}"
        ).order_by(db.LedgerEvent.i)).all()) if order_ids else []
        recent_events = list(s.scalars(select(db.LedgerEvent).where(
            db.LedgerEvent.order_id.in_(order_ids)
        ).order_by(db.LedgerEvent.i.desc()).limit(200)).all()) if order_ids else []
        latest_seq = s.scalar(select(func.max(db.LedgerEvent.i)))
        previous = {}
        pieces_this_week = 0
        for event in logs:
            if event.type != "progress.reported":
                continue
            payload = event.payload or {}
            key = (event.order_id, payload.get("stage"))
            done = int(payload.get("done") or 0)
            if (event.ts or 0) >= week_start:
                pieces_this_week += max(0, done - previous.get(key, 0))
            previous[key] = done
        import events as event_projection
        latest = []
        orders_by_id = {order.id: order for order in orders}
        for event in recent_events:
            message = event_projection.project(event, user, orders_by_id.get(event.order_id))
            if message:
                latest.append(message)
            if len(latest) == 3:
                break
        return {"tasks": mine, "earnings": pays,
                "profile": {"name": profile.name, "skills": profile.skills or [], "capacity": profile.capacity or 0,
                            "language": profile.lang or "en", "consented": bool(profile.consent_at)},
                "leader": [{"name": leader.name, "phone": leader.phone} for leader in leaders],
                "pieces_this_week": pieces_this_week,
                "pay_waiting": sum(float(entry["line"]["total_expected"]) for entry in pays
                                   if entry["status"] == "payout_pending"),
                "latest_updates": latest, "last_seq": -1 if latest_seq is None else latest_seq}
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
def report_done(user, oid, n, stage=None):
    """Member adds pieces she finished. Cumulative; capped at her task size."""
    try: n = int(n); assert n > 0
    except Exception: raise Err(422, "Send a positive number of pieces, e.g. DONE 1 20")
    with db.S() as s:
        o = _order(s, oid, user); _need(o, "in_production")
        mine = [x for x in (o.data.get("plan") or {}).get("assignments", []) if x["member"] == user["id"]]
        if not mine: raise Err(403, "You have no task in this order")
        pr = dict(o.data.get("progress", {}))
        if stage is not None:
            x = next((x for x in mine if x["stage"] == str(stage)), None)
            if x is None: raise Err(403, "You have no task for that stage")
        else:
            x = next((x for x in mine if pr.get(_key(x), 0) < x["units"]), mine[-1])
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
        u = s.get(db.User, user["id"]); u.lang = lang
        LED.append("user.language_changed", {"lang": lang}, f"{user['role']}:{user['id']}", s=s)
        s.commit()

def _buyer_payment_rows(s, oid):
    rows = list(s.scalars(select(db.BuyerPayment).where(db.BuyerPayment.order_id == oid).order_by(db.BuyerPayment.id)))
    return [{"status": x.status, "amount": x.amount, "paid_at": x.paid_at, "note": x.note} for x in rows]

def leader_buyer_contact(user, buyer_id):
    _leader_only(user)
    with db.S() as s:
        buyer = s.get(db.User, int(buyer_id))
        order = s.scalars(select(db.Order).where(
            db.Order.group_id == user["group_id"], db.Order.buyer_id == int(buyer_id)
        ).limit(1)).first()
        if not buyer or not order: raise Err(404, "Buyer not found in your group")
        s.add(db.AccessLog(actor_id=user["id"], action="buyer_contact_viewed",
                           target_id=buyer.id, created=time.time()))
        LED.append("buyer.contact_viewed", {"buyer_id": buyer.id}, f"leader:{user['id']}", order.id, s=s)
        s.commit()
        return {"name": buyer.name, "phone": buyer.phone}

def leader_buyers(user):
    _leader_only(user)
    with db.S() as s:
        orders = list(s.scalars(select(db.Order).where(db.Order.group_id == user["group_id"]).order_by(db.Order.id.desc())))
        by_buyer = {}
        for order in orders:
            buyer = s.get(db.User, order.buyer_id)
            if not buyer: continue
            row = by_buyer.setdefault(buyer.id, {"id": buyer.id, "name": buyer.name,
                "orders": 0, "closed": 0, "on_time": 0, "on_time_known": 0,
                "balance_due": 0.0, "ratings": []})
            row["orders"] += 1
            row["closed"] += int(order.status == "closed")
            if order.status == "closed":
                due_at = order.data.get("due_at")
                shipments = (order.data.get("delivery") or {}).get("shipments", [])
                signed_at = [shipment.get("signoff", {}).get("ts") for shipment in shipments
                             if shipment.get("signoff", {}).get("ts") is not None]
                if due_at is not None and signed_at:
                    row["on_time_known"] += 1
                    row["on_time"] += int(max(signed_at) <= due_at)
            payments = _buyer_payment_rows(s, order.id)
            if payments:
                latest = payments[-1]
                billed = float(order.data.get("quote") or order.data["order"].get("offered_price") or 0) * int(order.data["order"]["qty"])
                if latest["status"] == "unpaid": row["balance_due"] += float(latest["amount"] or billed)
                elif latest["status"] == "advance": row["balance_due"] += max(0, billed - float(latest["amount"] or 0))
            rating = s.scalars(select(db.BuyerRating).where(db.BuyerRating.order_id == order.id)).first()
            if rating: row["ratings"].append(rating.stars)
        return [{"id": row["id"], "name": row["name"], "orders": row["orders"],
                 "closed": row["closed"], "balance_due": round(row["balance_due"], 2),
                 "on_time_pct": round(row["on_time"] * 100 / row["on_time_known"]) if row["on_time_known"] else None,
                 "average_rating": round(sum(row["ratings"]) / len(row["ratings"]), 1) if row["ratings"] else None}
                for row in by_buyer.values()]

def leader_add_order_note(user, oid, note):
    _leader_only(user); note = str(note or "").strip()
    if not note: raise Err(422, "Write a note before saving")
    if len(note) > 500: raise Err(422, "Notes must be 500 characters or fewer")
    with db.S() as s:
        order = _order(s, oid, user)
        row = db.OrderNote(order_id=oid, leader_id=user["id"], note=note, created=time.time())
        s.add(row); s.flush()
        LED.append("order.note_added", {"note_id": row.id}, f"leader:{user['id']}", oid, s=s)
        s.commit()
        return {"id": row.id, "note": row.note, "created": row.created}

def leader_get_order_notes(user, oid):
    _leader_only(user)
    with db.S() as s:
        _order(s, oid, user)
        return [{"id": row.id, "note": row.note, "created": row.created}
                for row in s.scalars(select(db.OrderNote).where(db.OrderNote.order_id == oid)
                                     .order_by(db.OrderNote.created.desc()))]

def leader_record_buyer_payment(user, oid, status, amount, note=""):
    _leader_only(user)
    if status not in ("unpaid", "advance", "paid"): raise Err(422, "Status must be unpaid, advance or paid")
    try: amount = float(amount)
    except (TypeError, ValueError): raise Err(422, "Enter a valid payment amount")
    if not math.isfinite(amount) or amount < 0: raise Err(422, "Payment amount cannot be negative")
    note = str(note or "").strip()[:250]
    with db.S() as s:
        order = _order(s, oid, user)
        if order.status not in POST_PAY: raise Err(409, "Record buyer payment after the group has proposed a payout")
        row = db.BuyerPayment(order_id=oid, status=status, amount=amount,
                              paid_at=time.time() if status in ("advance", "paid") else None,
                              note=note, actor_id=user["id"])
        s.add(row); s.flush()
        LED.append("buyer.payment_recorded", {"payment_id": row.id, "status": status, "amount": amount},
                   f"leader:{user['id']}", oid, s=s)
        s.commit()
        buyer = s.get(db.User, order.buyer_id)
        if buyer:
            notify.send(buyer.phone, _payment_message(status, amount, oid, buyer.lang or "en"))
        return {"status": status, "amount": amount, "paid_at": row.paid_at, "note": note}

def _payment_message(status, amount, oid, lang):
    if lang == "hi":
        labels = {"unpaid": f"ऑर्डर #{oid}: भुगतान बाकी दर्ज किया गया (₹{amount:g})।", "advance": f"ऑर्डर #{oid}: ₹{amount:g} अग्रिम भुगतान दर्ज हुआ।", "paid": f"ऑर्डर #{oid}: ₹{amount:g} भुगतान दर्ज हुआ।"}
    elif lang == "hing":
        labels = {"unpaid": f"Order #{oid}: ₹{amount:g} payment baaki record hua.", "advance": f"Order #{oid}: ₹{amount:g} advance payment record hua.", "paid": f"Order #{oid}: ₹{amount:g} payment record hua."}
    else:
        labels = {"unpaid": f"Order #{oid}: unpaid balance of Rs {amount:g} recorded.", "advance": f"Order #{oid}: Rs {amount:g} advance recorded.", "paid": f"Order #{oid}: Rs {amount:g} payment recorded."}
    return labels[status]

def leader_alerts(user):
    _leader_only(user)
    overview = leader_overview(user)
    alerts = [{"kind": item["kind"], "order_id": item.get("order_id"), "label": item["label"],
               "action": item["action"], "age_minutes": None}
              for item in overview["decision_queue"]]
    with db.S() as s:
        issues = list(s.scalars(select(db.LedgerEvent).where(
            db.LedgerEvent.type == "member.issue").order_by(db.LedgerEvent.i.desc()).limit(100)))
        for event in issues:
            order = s.get(db.Order, event.order_id) if event.order_id else None
            if order and order.group_id == user["group_id"]:
                alerts.append({"kind": "member_issue", "order_id": order.id, "label": "Member reported an issue",
                               "action": "orders", "age_minutes": max(0, int((time.time() - (event.ts or time.time())) / 60))})
        leaves = list(s.scalars(select(db.LeaveRequest).where(
            db.LeaveRequest.group_id == user["group_id"], db.LeaveRequest.status == "pending")))
        alerts.extend({"kind": "leave", "order_id": None, "label": "Member leave request needs review",
                       "action": "team", "age_minutes": max(0, int((time.time() - item.created) / 60))}
                      for item in leaves)
    return alerts

def leader_broadcast(user, message, member_id=None):
    _leader_only(user); message = str(message or "").strip()
    if not message or len(message) > 500: raise Err(422, "Message must be 1 to 500 characters")
    if member_id is not None:
        try:
            member_id = int(member_id)
            if member_id < 1: raise ValueError()
        except (TypeError, ValueError): raise Err(422, "Choose a valid member")
    with db.S() as s:
        query = select(db.User).where(db.User.group_id == user["group_id"], db.User.role == "seller", db.User.active == True)  # noqa: E712
        if member_id is not None: query = query.where(db.User.id == int(member_id))
        recipients = list(s.scalars(query))
        if member_id is not None and not recipients: raise Err(404, "Active member not found in your group")
        now = time.time()
        for member in recipients:
            notice = db.Notice(group_id=user["group_id"], user_id=member.id, sender_id=user["id"],
                               message=message, created=now)
            s.add(notice); s.flush()
            LED.append("notice.created", {"notice_id": notice.id, "user_id": member.id},
                       f"leader:{user['id']}", s=s)
            notify.send(member.phone, _leader_notice_message(message, member.lang or "en"))
        s.commit()
        return {"sent": len(recipients)}

def leader_notices(user):
    _leader_only(user)
    with db.S() as s:
        rows = list(s.scalars(select(db.Notice).where(db.Notice.group_id == user["group_id"])
                              .order_by(db.Notice.created.desc()).limit(100)))
        return [{"id": row.id, "member_id": row.user_id,
                 "member": (s.get(db.User, row.user_id).name if s.get(db.User, row.user_id) else "Member"),
                 "message": row.message, "created": row.created, "seen": row.seen_at is not None}
                for row in rows]

def _leader_notice_message(message, lang):
    intro = {"hi": "आपकी समूह लीडर का संदेश: ", "hing": "Aapki group leader ka message: "}.get(lang, "Message from your group leader: ")
    return intro + message

def member_notices(user):
    if user["role"] != "seller": raise Err(403, "Only members can view these notices")
    with db.S() as s:
        notices = list(s.scalars(select(db.Notice).where(
            db.Notice.group_id == user["group_id"], db.Notice.user_id == user["id"]
        ).order_by(db.Notice.created.desc()).limit(100)))
        leave = list(s.scalars(select(db.LeaveRequest).where(
            db.LeaveRequest.user_id == user["id"]).order_by(db.LeaveRequest.created.desc()).limit(20)))
        orders = list(s.scalars(select(db.Order).where(db.Order.group_id == user["group_id"])))
        order_ids = [order.id for order in orders]
        rows = list(s.scalars(select(db.LedgerEvent).where(db.LedgerEvent.order_id.in_(order_ids))
                              .order_by(db.LedgerEvent.i.desc()).limit(100))) if order_ids else []
        by_order = {order.id: order for order in orders}
        import events as event_projection
        system = []
        for event in rows:
            item = event_projection.project(event, user, by_order.get(event.order_id))
            if item:
                system.append(item)
            if len(system) >= 20: break
        return {"messages": [{"id": n.id, "message": n.message, "created": n.created,
                              "seen": n.seen_at is not None} for n in notices],
                "system": system,
                "leave_requests": [{"id": x.id, "days": x.days, "reason": x.reason,
                                    "status": x.status, "resolution": x.resolution,
                                    "created": x.created} for x in leave]}

def member_ack_notice(user, notice_id):
    if user["role"] != "seller": raise Err(403, "Only members can acknowledge notices")
    with db.S() as s:
        row = s.get(db.Notice, int(notice_id))
        if not row or row.group_id != user["group_id"] or row.user_id != user["id"]:
            raise Err(404, "Notice not found")
        if row.seen_at is None:
            row.seen_at = time.time()
            LED.append("notice.seen", {"notice_id": row.id, "user_id": user["id"]},
                       f"seller:{user['id']}", s=s)
            s.commit()
        return {"ok": True, "seen": True}

def member_request_leave(user, days, reason=""):
    if user["role"] != "seller": raise Err(403, "Only members can request leave")
    from datetime import date
    if not isinstance(days, list) or not 1 <= len(days) <= 7: raise Err(422, "Choose one to seven days")
    try:
        parsed = sorted({date.fromisoformat(str(item)) for item in days})
    except (TypeError, ValueError): raise Err(422, "Choose valid calendar dates")
    if any((item - date.today()).days < 0 or (item - date.today()).days > 14 for item in parsed):
        raise Err(422, "Choose dates from today through the next 14 days")
    with db.S() as s:
        row = db.LeaveRequest(group_id=user["group_id"], user_id=user["id"], days=[x.isoformat() for x in parsed],
                              reason=str(reason or "").strip()[:250], status="pending",
                              created=time.time(), updated=time.time())
        s.add(row); s.flush()
        LED.append("leave.requested", {"leave_id": row.id, "user_id": user["id"]},
                   f"seller:{user['id']}", s=s)
        s.commit()
        _tell(s, user["group_id"], "leader",
              f"{user['name']} requested leave for {', '.join(row.days)}. Review in the dashboard or reply LEAVES.")
        return {"id": row.id, "status": row.status, "days": row.days}

def leader_leave_requests(user):
    _leader_only(user)
    with db.S() as s:
        rows = list(s.scalars(select(db.LeaveRequest).where(db.LeaveRequest.group_id == user["group_id"])
                              .order_by(db.LeaveRequest.created.desc()).limit(100)))
        return [{"id": row.id, "member_id": row.user_id,
                 "member": (s.get(db.User, row.user_id).name if s.get(db.User, row.user_id) else "Member"),
                 "days": row.days, "reason": row.reason, "status": row.status,
                 "resolution": row.resolution, "created": row.created} for row in rows]

def leader_resolve_leave(user, request_id, status, note=""):
    _leader_only(user)
    if status not in ("noted", "approved", "declined"): raise Err(422, "Choose noted, approved or declined")
    with db.S() as s:
        row = s.get(db.LeaveRequest, int(request_id))
        if not row or row.group_id != user["group_id"]: raise Err(404, "Leave request not found")
        if row.status != "pending": raise Err(409, "This leave request was already reviewed")
        row.status, row.resolution, row.updated = status, str(note or "").strip()[:250], time.time()
        LED.append("leave.resolved", {"leave_id": row.id, "user_id": row.user_id, "status": status},
                   f"leader:{user['id']}", s=s)
        s.commit()
        member = s.get(db.User, row.user_id)
        if member:
            notify.send(member.phone, _leave_resolution_message(status, row.days, member.lang or "en", row.resolution))
        return {"id": row.id, "status": row.status, "resolution": row.resolution}

def _leave_resolution_message(status, days, lang, note):
    dates = ", ".join(days)
    if lang == "hi":
        text = {"noted": f"आपकी छुट्टी की सूचना दर्ज हुई: {dates}.", "approved": f"आपकी छुट्टी मंज़ूर हुई: {dates}.", "declined": f"आपकी छुट्टी मंज़ूर नहीं हुई: {dates}."}[status]
        return text + (f" लीडर का संदेश: {note}" if note else "")
    if lang == "hing":
        text = {"noted": f"Leave note kar li: {dates}.", "approved": f"Leave approve hui: {dates}.", "declined": f"Leave approve nahi hui: {dates}."}[status]
        return text + (f" Leader ka note: {note}" if note else "")
    text = {"noted": f"Your leave was noted: {dates}.", "approved": f"Your leave was approved: {dates}.", "declined": f"Your leave was declined: {dates}."}[status]
    return text + (f" Leader's note: {note}" if note else "")

def issue_magic_link(phone):
    from auth import norm
    ttl = max(1, int(os.getenv("MAGIC_LINK_TTL_MIN", "10"))) * 60
    base = os.getenv("PUBLIC_URL", "").rstrip("/")
    if not base:
        if os.getenv("ENV", "prod") == "dev": base = "http://localhost:8000"
        else: raise Err(503, "Secure sign-in links are not configured")
    if os.getenv("ENV", "prod") != "dev" and not base.startswith("https://"):
        raise Err(503, "Secure sign-in links require an HTTPS public URL")
    with db.S() as s:
        user = s.scalars(select(db.User).where(db.User.phone == norm(phone), db.User.active == True)).first()  # noqa: E712
        if not user: return {"ok": True}
        recent = s.scalars(select(db.MagicLink).where(db.MagicLink.phone == user.phone)
                           .order_by(db.MagicLink.created.desc())).first()
        if recent and time.time() - recent.created < 60: raise Err(429, "Please wait before requesting another sign-in link")
        token = os.urandom(32).hex()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = db.MagicLink(phone=user.phone, token_hash=token_hash, expires=time.time() + ttl, created=time.time())
        s.add(row); s.flush()
        LED.append("login.magic_link_issued", {"user_id": user.id}, f"{user.role}:{user.id}", s=s)
        s.commit()
        landing = {"leader": "overview", "seller": "home", "buyer": "home", "officer": "overview"}.get(user.role, "home")
        return {"ok": True, "url": f"{base}/app/go?t={token}#{landing}", "expires_in": ttl}

def consume_magic_link(token):
    from sqlalchemy import update
    token = str(token or "")
    if len(token) != 64: raise Err(401, "Invalid or expired sign-in link")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db.S() as s:
        row = s.scalars(select(db.MagicLink).where(db.MagicLink.token_hash == token_hash)).first()
        if not row or row.used_at is not None or row.expires <= time.time():
            raise Err(401, "Invalid or expired sign-in link")
        user = s.scalars(select(db.User).where(db.User.phone == row.phone, db.User.active == True)).first()  # noqa: E712
        if not user: raise Err(401, "Invalid or expired sign-in link")
        now = time.time()
        consumed = s.execute(update(db.MagicLink).where(
            db.MagicLink.id == row.id, db.MagicLink.used_at.is_(None),
            db.MagicLink.expires > now).values(used_at=now))
        if consumed.rowcount != 1: raise Err(401, "Invalid or expired sign-in link")
        LED.append("login.magic_link_used", {"user_id": user.id}, f"{user.role}:{user.id}", s=s)
        s.commit()
        return {"id": user.id, "name": user.name, "role": user.role}

def leader_price_calculate(user, product, qty, offered):
    _leader_only(user)
    try:
        qty, offered = int(qty), float(offered)
        if qty < 1 or not math.isfinite(offered) or offered <= 0: raise ValueError()
    except (TypeError, ValueError): raise Err(422, "Enter a positive quantity and offer price")
    product_key = _product_key(product)
    if not product_key: raise Err(422, "Choose a product from the current rate cards")
    with db.S() as s:
        card = ratecards.active(s, user["group_id"], product_key)
        price = E.price_check(qty, **card["cost"], offered=offered)
        return {"product": card["product"], "version": card["version"], "price": price}

def member_raise_concern(user, message):
    if user["role"] != "seller": raise Err(403, "Only members can raise a concern")
    message = str(message or "").strip()
    if not message or len(message) > 500: raise Err(422, "Write a concern of 1 to 500 characters")
    with db.S() as s:
        officers = list(s.scalars(select(db.User).where(db.User.role == "officer", db.User.active == True)))  # noqa: E712
        concern = db.MemberConcern(group_id=user["group_id"], user_id=user["id"],
                                   message=message, created=time.time())
        s.add(concern); s.flush()
        LED.append("concern.raised", {"concern_id": concern.id, "group_id": user["group_id"]},
                   f"seller:{user['id']}", s=s)
        s.commit()
        for officer in officers:
            notify.send(officer.phone, f"Private member concern from {user['name']} in group {user['group_id']}: {message}. This is not shared with the group leader.")
        return {"ok": True}

def officer_member_concerns(user):
    if user["role"] != "officer": raise Err(403, "Only mission officers can view member concerns")
    with db.S() as s:
        rows = s.execute(select(db.MemberConcern, db.User, db.Group)
                         .join(db.User, db.User.id == db.MemberConcern.user_id)
                         .join(db.Group, db.Group.id == db.MemberConcern.group_id)
                         .order_by(db.MemberConcern.created.desc()).limit(100)).all()
        return [{"id": concern.id, "member": member.name, "group": group.name,
                 "message": concern.message, "created": concern.created}
                for concern, member, group in rows]
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

def submit_join_request(phone, name, skill, lang, code, consent=False):
    """Create a pending member request only after consent and valid invite-code checks."""
    from auth import norm
    phone = norm(phone)
    code = str(code or "").strip()
    if not consent: raise Err(422, "Consent is required before requesting group membership")
    with db.S() as s:
        invite = s.scalars(select(db.GroupInvite).where(db.GroupInvite.code == code, db.GroupInvite.active == True)).first()  # noqa: E712
        if not invite: raise Err(422, "That group code was not recognized. Ask your leader for the current code.")
        if s.scalars(select(db.User).where(db.User.phone == phone)).first():
            raise Err(409, "This number is already registered. Please contact your group leader.")
        pending = s.scalars(select(db.JoinRequest).where(db.JoinRequest.phone == phone, db.JoinRequest.status == "pending")).first()
        if pending: raise Err(409, "Your request is already waiting for a leader.")
        request = db.JoinRequest(phone=phone, name=str(name).strip()[:40] or "Member", group_id=invite.group_id,
                                 skill=str(skill or "")[:40], lang=lang, status="pending",
                                 created=time.time(), consent_at=time.time())
        s.add(request); s.flush()
        LED.append("consent.recorded", {"purpose": "work and pay visibility", "via": "whatsapp",
                                        "request_id": request.id},
                   f"join_request:{request.id}", s=s)
        leaders = list(s.scalars(select(db.User).where(db.User.group_id == invite.group_id, db.User.role == "leader", db.User.active == True)))  # noqa: E712
        for leader in leaders:
            state = s.get(db.ChatState, leader.phone) or db.ChatState(phone=leader.phone, updated_at=time.time())
            pending_action = dict(state.pending_action or {})
            ids = list(pending_action.get("ids", [])) if pending_action.get("type") == "join_request" else []
            if request.id not in ids: ids.append(request.id)
            state.pending_action = {"type": "join_request", "ids": ids}
            state.updated_at = time.time()
            s.merge(state)
            if len(ids) == 1:
                notify.send(leader.phone, i18n.t("leader_join_request", leader.lang,
                                                 name=request.name, skill=i18n.stage(request.skill, leader.lang) or "not specified"))
        s.commit()
        return {"ok": True, "request_id": request.id, "group_id": invite.group_id}

def pending_join_request(phone):
    from auth import norm
    with db.S() as s:
        return bool(s.scalars(select(db.JoinRequest).where(db.JoinRequest.phone == norm(phone),
                                                           db.JoinRequest.status == "pending")).first())

def resolve_join_request(user, request_id, approve):
    """A group's leader alone can approve or reject its pending member requests."""
    if user["role"] != "leader": raise Err(403, "Only the group leader can review join requests")
    with db.S() as s:
        request = s.get(db.JoinRequest, int(request_id))
        if not request or request.group_id != user["group_id"] or request.status != "pending":
            raise Err(404, "Join request not found")
        existing = s.scalars(select(db.User).where(db.User.phone == request.phone)).first()
        if existing: raise Err(409, "That phone number is already registered")
        if approve:
            member = db.User(phone=request.phone, name=request.name, role="seller", group_id=request.group_id,
                             skills=[request.skill] if request.skill else [], capacity=30, active=True, lang=request.lang)
            s.add(member); s.flush()
            member_state = s.get(db.ChatState, request.phone) or db.ChatState(
                phone=request.phone, updated_at=time.time()
            )
            member_state.step, member_state.data = "menu", {}
            member_state.lang, member_state.updated_at = request.lang, time.time()
            s.merge(member_state)
            LED.append("member.join_approved", {"request_id": request.id, "skill": request.skill}, f"leader:{user['id']}", s=s)
            request.status = "approved"
            notify.send(request.phone, i18n.t("join_approved", request.lang, name=request.name))
        else:
            request.status = "rejected"
            LED.append("member.join_rejected", {"request_id": request.id}, f"leader:{user['id']}", s=s)
            notify.send(request.phone, i18n.t("join_rejected", request.lang))
        leader = s.get(db.User, user["id"])
        state = s.get(db.ChatState, leader.phone) if leader else None
        if state and (state.pending_action or {}).get("type") == "join_request":
            ids = [rid for rid in state.pending_action.get("ids", []) if rid != request.id]
            state.pending_action = {"type": "join_request", "ids": ids} if ids else None
            state.updated_at = time.time()
            if ids:
                following = s.get(db.JoinRequest, ids[0])
                if following and following.status == "pending":
                    notify.send(leader.phone, i18n.t("leader_join_request", leader.lang,
                                                     name=following.name, skill=i18n.stage(following.skill, leader.lang) or "not specified"))
        s.commit()
        return {"ok": True, "status": request.status, "name": request.name}

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

def signoff(user, oid, received, note="", final=False, rating=None, rating_note=""):
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
        if done and rating is not None:
            rate_buyer_group(user, oid, rating, rating_note)
        _tell(s, o.group_id, "leader", f"Order #{o.id}: buyer received {n} of {cur['qty']} pieces ({tot}/{qty} in total). " + ("Order closed." if done else f"{qty - tot} still owed: pack them and send READY {o.id} <pieces>."))
        return _view(s, o, user)

def rate_buyer_group(user, oid, stars, comment=""):
    if user["role"] != "buyer": raise Err(403, "Only the buyer can rate this group")
    try: stars = int(stars)
    except (TypeError, ValueError): raise Err(422, "Choose a rating from 1 to 5")
    if stars < 1 or stars > 5: raise Err(422, "Choose a rating from 1 to 5")
    with db.S() as s:
        order = _order(s, oid, user)
        if order.status != "closed": raise Err(409, "Rate the group after the order is closed")
        row = s.scalars(select(db.BuyerRating).where(db.BuyerRating.order_id == oid)).first()
        if row and row.buyer_id != user["id"]: raise Err(404, "Order not found")
        now = time.time()
        if row:
            row.stars, row.comment, row.created = stars, str(comment or "").strip()[:300], now
        else:
            row = db.BuyerRating(order_id=oid, buyer_id=user["id"], group_id=order.group_id,
                                 stars=stars, comment=str(comment or "").strip()[:300], created=now)
            s.add(row)
        s.flush()
        _log(s, order, "buyer.rating_submitted", user, {"stars": stars})
        s.commit()
        return {"ok": True, "stars": stars}

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
