"""Role-scoped projections of append-only ledger events for dashboard streams."""
from sqlalchemy import select
import db

_LABELS = {
    "order.submitted": "New order",
    "quote.sent": "Quote sent",
    "quote.accepted": "Quote accepted",
    "plan.proposed": "Work plan updated",
    "production.started": "Production started",
    "progress.reported": "Pieces reported",
    "quality.sample_submitted": "Sample submitted",
    "quality.decided": "Sample decision recorded",
    "payout.proposed": "Payout proposed",
    "payout.approved": "Payout approval recorded",
    "payout.settled": "Payout approved",
    "dispute.opened": "Pay dispute opened",
    "dispute.resolved": "Pay dispute resolved",
    "delivery.ready": "Order ready for delivery",
    "delivery.dispatched": "Order dispatched",
    "delivery.signed_off": "Delivery confirmed",
    "order.blocked": "Order blocked",
    "order.unblocked": "Order unblocked",
    "order.cancelled": "Order cancelled",
}

def _value(event, key):
    return getattr(event, key) if hasattr(event, key) else event.get(key)

def _order_for(event, order):
    if order is not None:
        return order
    order_id = _value(event, "order_id")
    if order_id is None:
        return None
    with db.S() as s:
        return s.get(db.Order, order_id)

def _refresh(type_):
    if type_.startswith(("progress.", "quality.", "plan.", "production.", "task.", "member.")):
        return ["orders", "team", "activity"]
    if type_.startswith(("payout.", "dispute.")):
        return ["orders", "payouts", "activity"]
    if type_.startswith("ratecard."):
        return ["pricing", "activity"]
    return ["orders", "activity"]

def project(event, user, order=None, actor_group=None):
    """Project one ledger row; never return its raw payload or hashed fields."""
    seq, type_, oid = _value(event, "i"), _value(event, "type") or "", _value(event, "order_id")
    ts = _value(event, "ts") or 0
    role, uid, group_id = user.get("role"), user.get("id"), user.get("group_id")
    projected_type, refresh = type_, _refresh(type_)
    if role == "officer":
        return {"seq": seq, "type": "metrics_changed", "order_id": None, "ts": ts,
                "summary": "Programme metrics updated", "refresh": ["metrics"]}
    o = _order_for(event, order) if oid is not None else None
    if oid is not None and not o:
        return None
    if role == "leader":
        if o and o.group_id != group_id:
            return None
        if not o and actor_group != group_id:
            return None
        summary = _LABELS.get(type_, "Group activity")
        if oid is not None:
            summary += f" · Order #{oid}"
    elif role == "buyer":
        if not o or o.buyer_id != uid:
            return None
        if type_ == "quote.sent":
            projected_type = "buyer.quote_ready"
            summary = "A quote is ready for your order"
        elif type_.startswith("progress."):
            projected_type = "buyer.progress_updated"
            plan = (o.data or {}).get("plan") or {}
            progress = (o.data or {}).get("progress") or {}
            assignments = plan.get("assignments") or []
            total = sum(int(a.get("units") or 0) for a in assignments)
            done = sum(min(int(progress.get(f"{a['member']}:{a['stage']}", 0)), int(a.get("units") or 0)) for a in assignments)
            pct = int(done * 100 / total) if total else 0
            summary = f"Your order is about {pct}% complete"
        else:
            projected_type = "buyer.order_updated"
            summary = "Your order was updated"
    elif role == "seller":
        if not o:
            if not type_.startswith("ratecard.") or actor_group != group_id:
                return None
        else:
            data = o.data or {}
            in_plan = any(int(a.get("member", -1)) == uid for a in ((data.get("plan") or {}).get("assignments") or []))
            in_payout = any(int(line.get("member", -1)) == uid for line in ((data.get("payout") or {}).get("lines") or []))
            payload = _value(event, "payload") or {}
            named_member = payload.get("member") == uid
            actor = _value(event, "actor") or ""
            own_actor = actor.endswith(f":{uid}") and actor.startswith("seller:")
            if type_.startswith("progress."):
                relevant = own_actor
            elif type_.startswith("quality."):
                samples = data.get("quality", [])
                sample = samples[payload["i"]] if isinstance(payload.get("i"), int) and 0 <= payload["i"] < len(samples) else None
                relevant = bool(sample and int(sample.get("member", -1)) == uid) or named_member
            elif type_.startswith("payout."):
                relevant = in_payout
            elif type_.startswith("dispute."):
                disputes = data.get("disputes", [])
                dispute = disputes[payload["idx"]] if isinstance(payload.get("idx"), int) and 0 <= payload["idx"] < len(disputes) else None
                relevant = own_actor or bool(dispute and dispute.get("uid") == uid)
            elif type_.startswith(("plan.", "production.")):
                relevant = in_plan
            elif type_.startswith("task."):
                relevant = own_actor
            else:
                relevant = own_actor or named_member
            if not relevant:
                return None
        summary = "An update needs your attention" if type_ in ("plan.proposed", "quality.decided", "payout.proposed") else "Your work was updated"
    else:
        return None
    if role == "buyer": refresh = ["orders"]
    elif role == "seller": refresh = ["tasks", "earnings"]
    return {"seq": seq, "type": projected_type, "order_id": oid, "ts": ts,
            "summary": summary, "refresh": refresh}

def project_many(rows, user):
    """Load order and actor scope in batches so one stream poll uses bounded DB reads."""
    if not rows:
        return []
    order_ids = {row.order_id for row in rows if row.order_id is not None}
    actor_ids = set()
    for row in rows:
        actor = row.actor or ""
        try:
            kind, raw_id = actor.split(":", 1)
            if kind in ("leader", "seller", "buyer"):
                actor_ids.add(int(raw_id))
        except (ValueError, AttributeError):
            continue
    with db.S() as s:
        orders = {o.id: o for o in s.scalars(select(db.Order).where(db.Order.id.in_(order_ids)))} if order_ids else {}
        actor_groups = {u.id: u.group_id for u in s.scalars(select(db.User).where(db.User.id.in_(actor_ids)))} if actor_ids else {}
    projected = []
    for row in rows:
        actor = row.actor or ""
        actor_group = None
        if actor.startswith("group:"):
            try: actor_group = int(actor.split(":", 1)[1])
            except ValueError: pass
        elif actor.startswith(("leader:", "seller:")):
            try: actor_group = actor_groups.get(int(actor.split(":", 1)[1]))
            except ValueError: pass
        item = project(row, user, orders.get(row.order_id), actor_group)
        if item is not None:
            projected.append(item)
    return projected

def project_joined(entries, user):
    """Project rows already joined to their order so each stream tick needs one DB query."""
    projected = []
    for row, order in entries:
        actor = row.actor or ""
        actor_group = None
        if actor.startswith("group:"):
            try: actor_group = int(actor.split(":", 1)[1])
            except ValueError: pass
        elif user.get("id") is not None and actor.endswith(f":{user['id']}"):
            actor_group = user.get("group_id")
        item = project(row, user, order, actor_group)
        if item is not None:
            projected.append(item)
    return projected
