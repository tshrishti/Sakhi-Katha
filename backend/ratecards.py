"""Per-group, versioned, group-approved rate cards. A card is PROPOSED by the leader and becomes ACTIVE once the leader
plus QUORUM members approve it; the previous active version is kept as 'superseded' for audit. Orders snapshot the
card they were priced on, so approving a new version never changes an order already quoted."""
import copy, os, time
from sqlalchemy import select
import db, defaults

QUORUM = int(os.getenv("RATECARD_MEMBER_QUORUM", os.getenv("PAYOUT_MEMBER_QUORUM", "2")))
COST_KEYS = ("material", "minutes", "hourly", "overhead_pct", "margin_pct")

class Bad(ValueError): pass

def validate(product, stages, cost):
    if product not in defaults.TEMPLATES: raise Bad("Unknown product. Choose one of: " + ", ".join(defaults.TEMPLATES))
    if not isinstance(stages, list) or not 1 <= len(stages) <= 10: raise Bad("Give 1 to 10 stages")
    out, seen = [], set()
    for s in stages:
        try:
            name, rate, w = str(s["name"]).strip().lower()[:30], float(s["rate"]), int(s.get("workers", 1))
        except Exception: raise Bad("Each stage needs a name, a rate per piece and a worker count")
        if not name or name in seen: raise Bad("Stage names must be unique and not empty")
        if rate <= 0 or not 1 <= w <= 20: raise Bad(f"Stage '{name}': rate must be above 0 and workers between 1 and 20")
        seen.add(name); out.append({"name": name, "rate": int(rate) if rate == int(rate) else rate, "workers": w})
    c = {}
    for k in COST_KEYS:
        try: v = float((cost or {})[k])
        except Exception: raise Bad(f"Cost needs a number for '{k}'")
        if v < 0 or (k in ("minutes", "hourly") and v <= 0) or (k.endswith("_pct") and v > 100): raise Bad(f"Cost value for '{k}' is out of range")
        c[k] = int(v) if v == int(v) else v
    return out, c

def default_card(product):
    t = defaults.TEMPLATES.get(product) or defaults.TEMPLATES[defaults.DEFAULT_PRODUCT]
    key = product if product in defaults.TEMPLATES else defaults.DEFAULT_PRODUCT
    return {"product": key, "version": 0, "source": "default", "stages": copy.deepcopy(t["stages"]), "cost": dict(t["cost"])}

def active(s, group_id, product):
    """The card an order for this product is priced on: the group's latest approved version, else the template default."""
    product = product if product in defaults.TEMPLATES else defaults.DEFAULT_PRODUCT
    r = s.scalars(select(db.RateCard).where(db.RateCard.group_id == group_id, db.RateCard.product == product,
                                            db.RateCard.status == "active")).first()
    if not r: return default_card(product)
    return {"product": product, "version": r.version, "source": "group", "stages": r.stages, "cost": r.cost, "card_id": r.id}

def view(r, viewer_id=None):
    return {"id": r.id, "product": r.product, "label": defaults.TEMPLATES[r.product]["label"], "version": r.version, "status": r.status,
            "stages": r.stages, "cost": r.cost, "note": r.note, "approvals": {"leader": r.leader_ok, "members": r.member_ok},
            "needed_members": QUORUM, "i_approved": viewer_id in (r.member_ok or []), "created": r.created, "approved_at": r.approved_at}
