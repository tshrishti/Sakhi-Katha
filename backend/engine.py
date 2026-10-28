"""Sakhi Khata engine: deterministic money, fairness and audit logic.
The LLM never touches these numbers. It only extracts, plans and explains."""
from decimal import Decimal as D, ROUND_HALF_UP
import hashlib, json, re, time
import defaults

def q(x):
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)

# ---------- 1. messy order -> structured order (rule-based fallback; plug an LLM in here) ----------
def parse_order(text):
    t = text.lower()
    qty = re.search(r"(\d+)\s*(?:" + "|".join(defaults.UNIT_WORDS) + r")\b", t)
    price = re.search(r"(?:rs\.?|₹|rupees?)\s*(\d+)", t) or re.search(r"(\d+)\s*(?:rs|rupees?|rupaye)", t)
    days = re.search(r"(\d+)\s*(?:days?|din)", t)
    week = re.search(r"week|hafte", t)
    colour = next((c for c in ("blue", "red", "green", "black", "white", "yellow") if c in t), None)
    order = {
        "qty": int(qty.group(1)) if qty else None,
        "offered_price": int(price.group(1)) if price else None,
        "deadline_days": int(days.group(1)) if days else (7 if week else None),
        "colour": colour,
    }
    unclear = [k for k, v in order.items() if v is None]
    if "same as" in t:
        unclear.append("spec: 'same as last time' - confirm size and design")
    return {"order": order, "unclear": unclear}

# ---------- 2. price check ----------
def price_check(qty, material, minutes, hourly, overhead_pct, margin_pct, offered):
    qty, material, offered = int(qty), D(str(material)), D(str(offered))
    labour = q(D(str(hourly)) * D(str(minutes)) / 60)
    cost = material + labour
    with_oh = q(cost * (1 + D(str(overhead_pct)) / 100))
    floor = q(with_oh * (1 + D(str(margin_pct)) / 100))
    eff_hourly = q((offered - material) / (D(str(minutes)) / 60))
    counter = (floor / 5).to_integral_value(rounding="ROUND_CEILING") * 5
    return {
        "per_unit": {"material": str(material), "labour": str(labour), "overhead": str(with_oh - cost), "floor": str(floor)},
        "order_total": {"offered": str(q(offered * qty)), "cost_before_margin": str(q(with_oh * qty)), "floor": str(q(floor * qty))},
        "offered": str(offered), "loss_making": offered < with_oh,
        "effective_hourly_at_offer": str(eff_hourly), "target_hourly": str(hourly),
        "pay_gap_pct": str(q((1 - eff_hourly / D(str(hourly))) * 100)),
        "suggested_counter": str(counter),
    }

# ---------- 3. fair allocation ----------
def allocate(units, stages, members, days=5, history=None, declined=()):
    history, load = history or {}, {m["id"]: D(0) for m in members}
    out, issues = [], []
    for st in stages:
        el = [m for m in members if st["name"] in m["skills"] and m["id"] not in declined]
        if not el:
            issues.append(f"No available member has the '{st['name']}' skill")
            continue
        el.sort(key=lambda m: (history.get(m["id"], 0), load[m["id"]]))
        pick, left = el[: st.get("workers", 1)], units
        tot = sum(m["capacity"] for m in pick)
        for i, m in enumerate(pick):
            n = left if i == len(pick) - 1 else int(units * m["capacity"] / tot)
            left -= n
            load[m["id"]] += D(n) / D(m["capacity"])
            out.append({"member": m["id"], "name": m["name"], "stage": st["name"], "units": n,
                        "rate": str(st["rate"]), "expected_pay": str(q(D(n) * D(str(st["rate"])))),
                        "why": f"Has {st['name']} skill, lowest recent load among eligible, free {days} days"})
    for m in members:
        if load[m["id"]] > days:
            issues.append(f"{m['name']} needs {q(load[m['id']])} days of work but has {days}")
    return {"assignments": out, "feasible": not issues, "issues": issues,
            "load_days": {m["name"]: str(q(load[m["id"]])) for m in members}}

# ---------- 3b. feasibility gate with options ----------
RUSH_PREMIUM_PCT = 10   # extra per piece when the group must stretch to hit a tight deadline

def _ok(units, stages, members, days, declined=()):
    return units > 0 and days > 0 and allocate(units, stages, members, days=days, declined=declined)["feasible"]

def feasibility_options(units, stages, members, days, floor, offered, declined=()):
    """When an order is not viable, offer ways to make it viable instead of just saying no.
    Returns a list of {kind, label, ...} options, best first. Pure and deterministic; the leader decides."""
    units, days, floor, offered = int(units), int(days), D(str(floor)), D(str(offered))
    if _ok(units, stages, members, days, declined): return []
    skills = {s for m in members if m["id"] not in declined for s in m["skills"]}
    missing = [st["name"] for st in stages if st["name"] not in skills]
    if missing:
        return [{"kind": "recruit", "label": f"Nobody available has the {', '.join(missing)} skill. Recruit or train a member first; changing price, quantity or date will not fix this."}]
    opts = []
    lo, hi = 0, units                                   # largest quantity doable by the deadline
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if _ok(mid, stages, members, days, declined) else (lo, mid - 1)
    if lo > 0:
        opts.append({"kind": "smaller_quantity", "qty": lo, "remaining": units - lo, "deadline_days": days,
                     "label": f"Take {lo} pieces by day {days} and deliver the other {units - lo} in a second batch"})
    d = days + 1                                        # earliest deadline that fits the full quantity
    while d <= days + 365 and not _ok(units, stages, members, d, declined): d += 1
    if d <= days + 365:
        opts.append({"kind": "later_deadline", "deadline_days": d, "qty": units,
                     "label": f"Ask the buyer for {d} days instead of {days} ({d - days} more) to deliver all {units} pieces"})
    base = max(floor, offered)
    price = ((base * (1 + D(RUSH_PREMIUM_PCT) / 100)) / 5).to_integral_value(rounding="ROUND_CEILING") * 5
    opts.append({"kind": "revised_price", "price": str(price), "qty": units, "deadline_days": days,
                 "label": f"Keep {units} pieces in {days} days but quote Rs {price} per piece (includes a {RUSH_PREMIUM_PCT}% rush premium for stretching the group)"})
    return opts

# ---------- 4. deadline rescue ----------
def rescue(done, needed, per_day, days_left):
    projected = done + per_day * days_left
    short = max(0, needed - projected)
    opts = [] if not short else [
        f"Move {short} units to a member with spare capacity (leader + that member agree)",
        "Offer a small rush bonus, only if the group agrees",
        f"Deliver {projected} units first, rest later (buyer agrees)",
        "Ask the buyer for more time"]
    return {"projected": projected, "shortfall": short, "options": opts, "decided_by": "leader"}

# ---------- 5. payout + explanation ----------
def payout(lines):
    res, total = [], D(0)
    for l in lines:
        paid, pend = q(D(l["accepted"]) * D(str(l["rate"]))), q(D(l.get("pending", 0)) * D(str(l["rate"])))
        res.append({**l, "paid": str(paid), "pending_amount": str(pend), "total_expected": str(paid + pend)})
        total += paid + pend
    return {"lines": res, "total": str(total)}

def explain(l):
    made = l["accepted"] + l.get("pending", 0)
    s = f"{l['name']}, you made {made} {l['stage']} pieces. {l['accepted']} passed at Rs {l['rate']} each = Rs {l['paid']}."
    if l.get("pending"):
        s += f" {l['pending']} need rework; Rs {l['pending_amount']} is added when they pass."
    return s + f" Total expected: Rs {l['total_expected']}. Say 'I disagree' to question any entry."

# ---------- 6. append-only hash-chained ledger ----------
class Ledger:
    def __init__(self): self.events = []
    @staticmethod
    def _h(body): return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    def append(self, type_, payload, actor):
        prev = self.events[-1]["hash"] if self.events else "0" * 64
        body = {"i": len(self.events), "type": type_, "actor": actor, "payload": payload, "ts": round(time.time(), 2), "prev": prev}
        self.events.append({**body, "hash": self._h(body)})
        return self.events[-1]
    def verify(self):
        prev = "0" * 64
        for e in self.events:
            body = {k: v for k, v in e.items() if k != "hash"}
            if e["prev"] != prev or self._h(body) != e["hash"]:
                return {"valid": False, "broken_at": e["i"]}
            prev = e["hash"]
        return {"valid": True, "broken_at": None, "events": len(self.events)}
