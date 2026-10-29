"""Fair-work history used by the planner for rotation: how much each member earned across the group's last few orders.
The planner gives the next order's work first to whoever earned least recently (ties broken by current load)."""
from decimal import Decimal as D
from sqlalchemy import select
import db

def order_earnings(o):
    """{member_id: Decimal} for one order: the proposed payout if there is one, otherwise the planned expected pay."""
    out = {}
    pay = (o.data.get("payout") or {}).get("lines")
    if pay:
        for l in pay: out[l["member"]] = out.get(l["member"], D(0)) + D(str(l["total_expected"]))
    else:
        for x in (o.data.get("plan") or {}).get("assignments", []): out[x["member"]] = out.get(x["member"], D(0)) + D(str(x["expected_pay"]))
    return out

def history(s, group_id, exclude_oid=None, last=5):
    rows = [o for o in s.scalars(select(db.Order).where(db.Order.group_id == group_id, db.Order.status != "cancelled").order_by(db.Order.id.desc()))
            if o.id != exclude_oid and (o.data.get("plan") or {}).get("assignments")][:last]
    tot = {}
    for o in rows:
        for m, v in order_earnings(o).items(): tot[m] = tot.get(m, D(0)) + v
    return tot, len(rows)
