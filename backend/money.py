"""Money owed BY the buyer to the group (the other direction from payouts, which are what the group pays its members).
Pure functions over an order's JSON, shared by the leader hub, the buyer view and WhatsApp. Decimal throughout; nothing here moves money:
it only records what the leader says she received."""
import math, os
from decimal import Decimal as D
import engine as E

TERMS_DAYS = int(os.getenv("PAYMENT_TERMS_DAYS", "0"))        # days after the delivery date that the balance is due
OWED_FROM = ("accepted", "planned", "in_production", "payout_pending", "settled", "ready_for_delivery", "dispatched", "partially_delivered", "closed", "blocked")
DAY = 86400

def due_ts(o):
    m = (o.data.get("money") or {})
    if m.get("due_date"):
        import datetime
        try: return datetime.datetime.fromisoformat(m["due_date"]).timestamp() + DAY - 1      # due at the end of that day
        except Exception: pass
    base = o.data.get("due_at") or ((o.created or 0) + int(o.data["order"].get("deadline_days") or 0) * DAY)
    return base + TERMS_DAYS * DAY

def summary(o, now):
    od, d = o.data["order"], o.data
    q = d.get("quote"); price = D(str(q if q is not None else od["offered_price"]))
    total = E.q(price * int(od["qty"])); pays = (d.get("money") or {}).get("payments", [])
    paid = E.q(sum((D(str(p["amount"])) for p in pays), D(0)))
    adv = E.q(sum((D(str(p["amount"])) for p in pays if p.get("kind") == "advance"), D(0)))
    if o.status == "cancelled": return {"total": "0.00", "paid": str(paid), "advance": str(adv), "balance": "0.00", "refund_due": str(paid), "status": "cancelled", "estimated": False,
                                         "due_ts": None, "overdue": False, "days_overdue": 0, "payments": pays, "reminders": (d.get("money") or {}).get("reminders", [])}
    bal = max(total - paid, D(0)); owed = o.status in OWED_FROM
    status = "paid" if (paid >= total and total > 0) else ("advance" if paid > 0 else "unpaid")
    due = due_ts(o); over = bool(owed and bal > 0 and now > due)
    return {"total": str(total), "paid": str(paid), "advance": str(adv), "balance": str(bal if owed else D(0)), "refund_due": "0.00", "status": status, "estimated": q is None, "owed": owed,
            "due_ts": due, "overdue": over, "days_overdue": max(0, math.ceil((now - due) / DAY)) if over else 0, "payments": pays, "reminders": (d.get("money") or {}).get("reminders", [])}

def public(o, now):
    """What the BUYER sees about the money: totals only."""
    s = summary(o, now); return {k: s[k] for k in ("total", "paid", "balance", "status", "estimated")}
