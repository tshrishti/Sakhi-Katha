"""The leader hub: ONE read-model over the shared workflow. Every dashboard tab is a different projection of the same Ctx:
the same facts about each order (workflow.pending), the same ledger, the same money and capacity maths. Nothing here writes.
Leader only; every number is derived from the orders and the hash-chained ledger, so the dashboard cannot disagree with WhatsApp."""
import csv, datetime, io, math, os, re, time
from collections import defaultdict
from decimal import Decimal as D
from functools import cached_property
from sqlalchemy import or_, select
import db, defaults, engine as E, fairness, money as M, ratecards, screening, svc, workflow as W

DAY = 86400
TZ_MIN = int(os.getenv("TZ_OFFSET_MIN", "330"))                  # buckets "today" and the weekly chart by local day (default India)
STALE_H = svc.STALE_H
def day_of(ts): return int((ts + TZ_MIN * 60) // DAY)
def _d(x): return D(str(x))
def _s(x): return str(E.q(x))
def _bkey(name): return "n:" + re.sub(r"\s+", " ", str(name or "").strip().lower())

class Ctx:
    """Loads the group's orders, people and ledger once; every view below reads from it."""
    def __init__(self, s, user, now=None):
        self.s, self.user, self.gid = s, user, user["group_id"]; self.now = time.time() if now is None else now
        self.cursor = (s.scalars(select(db.LedgerEvent.i).order_by(db.LedgerEvent.i.desc())).first() or 0)      # read BEFORE the data: a later event is then never missed
        self.orders = list(s.scalars(select(db.Order).where(db.Order.group_id == self.gid).order_by(db.Order.id.desc())))
        self.byid = {o.id: o for o in self.orders}
        bids = {o.buyer_id for o in self.orders}
        self.users = {u.id: u for u in s.scalars(select(db.User).where(or_(db.User.group_id == self.gid, db.User.id.in_(bids))))}
        self.sellers = sorted([u for u in self.users.values() if u.role == "seller" and u.group_id == self.gid], key=lambda u: u.id)
        self.members = [u for u in self.sellers if u.active]
        self.cap = {u.id: (u.capacity or 1) for u in self.users.values() if u.group_id == self.gid}
        self.profiles = {p.key: p for p in s.scalars(select(db.BuyerProfile).where(db.BuyerProfile.group_id == self.gid))}
        self.group = s.get(db.Group, self.gid); self._f, self._c, self._p = {}, {}, {}

    # ----- ledger-derived -----
    @cached_property
    def led(self):
        if not self.byid: return []
        return list(self.s.scalars(select(db.LedgerEvent).where(db.LedgerEvent.order_id.in_(list(self.byid))).order_by(db.LedgerEvent.i)))
    @cached_property
    def last_event_ts(self):
        out = {}
        for e in self.led: out[e.order_id] = e.ts
        return out
    @cached_property
    def last_report(self):
        out = {}
        for e in self.led:
            if e.type == "progress.reported": out[(e.order_id, int(e.actor.split(":")[1]))] = e.ts
        return out
    @cached_property
    def series(self):
        """-> (finished, member): finished = [(ts, oid, delta pieces that completed EVERY stage)], member = [(ts, oid, member id, delta pieces she reported)]."""
        by = defaultdict(list)
        for e in self.led:
            if e.type == "progress.reported": by[e.order_id].append(e)
        fin, mem = [], []
        for oid, evs in by.items():
            o = self.byid[oid]; stages = {x["stage"] for x in (o.data.get("plan") or {}).get("assignments", [])}; latest, prev = {}, 0
            for e in evs:
                m, st, done = int(e.actor.split(":")[1]), e.payload.get("stage"), int(e.payload.get("done", 0))
                dlt = done - latest.get((m, st), 0); latest[(m, st)] = done; mem.append((e.ts, oid, m, dlt))
                tot = defaultdict(int)
                for (mm, ss), v in latest.items(): tot[ss] += v
                f = min((tot.get(ss, 0) for ss in stages), default=0); fin.append((e.ts, oid, f - prev)); prev = f
        return fin, mem
    def name(self, actor):
        role, _, ident = str(actor).partition(":")
        if ident.isdigit() and int(ident) in self.users: return self.users[int(ident)].name
        return {"system": "The system", "group": "The group", "owner": "Owner", "leader": "Leader", "seller": "A member", "buyer": "A buyer", "officer": "Mission officer"}.get(role, role)

    # ----- one order, seen through the workflow -----
    def price(self, o):
        if o.id in self._p: return self._p[o.id]
        od = o.data["order"]; p = o.data.get("price") or E.price_check(od["qty"], **svc._card(o)["cost"], offered=od["offered_price"]); self._p[o.id] = p; return p
    def buyer(self, o):
        od, u = o.data["order"], self.users.get(o.buyer_id)
        if u and u.role == "buyer": key, name, phone, reg = f"u:{u.id}", (od.get("buyer_name") or u.name), u.phone, True
        else:
            name = od.get("buyer_name") or "Walk-in buyer"; key, reg = _bkey(name), False
            phone = (self.profiles.get(key).phone if self.profiles.get(key) else None)
        return {"key": key, "name": name, "phone": phone, "registered": reg}
    def facts(self, o):
        if o.id in self._f: return self._f[o.id]
        d, od, now, st = o.data, o.data["order"], self.now, o.status
        asg = (d.get("plan") or {}).get("assignments", []); pg = svc._progress(o); due_at = d.get("due_at"); qty = int(od["qty"])
        est = (o.created or now) + int(od.get("deadline_days") or 0) * DAY; due = due_at if due_at is not None else est; dl = math.ceil((due - now) / DAY)
        rows = pg["rows"]; all_done = bool(rows) and all(r["done"] >= r["units"] for r in rows)
        qs, ds, iss = d.get("quality", []), d.get("disputes", []), d.get("issues", [])
        pend = [{"i": q["i"], "member": q["member"], "name": q["name"], "stage": q["stage"], "ts": q.get("ts", 0), "escalated": bool(q.get("escalated")), "flags": sum(1 for it in ((q.get("screening") or {}).get("items") or []) if it.get("result") == "possible_defect")} for q in qs if q["status"] == "pending"]
        ms = M.summary(o, now); ap = d.get("approvals") or {"leader": False, "members": []}
        lines = (d.get("payout") or {}).get("lines", []); quorum = min(svc.QUORUM, len({l["member"] for l in lines})) if lines else 0
        stale = None
        if st == "in_production" and due_at is not None and not all_done:
            start = due_at - int(od["deadline_days"]) * DAY; last = max([start] + [t for (oid, _m), t in self.last_report.items() if oid == o.id])
            stale = last if now - last >= STALE_H * 3600 else None
        short = 0
        if st == "in_production" and asg and not all_done:
            try: short = svc._risk_calc(self.s, o, max(dl, 0), cap=self.cap)["shortfall"]
            except Exception: short = 0
        pl = d.get("plan") or {}; p = self.price(o); blocked = d.get("blocked") or {}
        dec = [e.ts for e in self.led if e.order_id == o.id and e.type == "member.declined"]
        f = {"id": o.id, "status": st, "qty": qty, "created": o.created, "due": due, "days_left": dl, "late": st in W.ACTIVE and st != "blocked" and now > due, "finished": pg["finished"], "all_done": all_done,
             "disputes_open": [{"i": i, "name": x["name"], "reason": x.get("reason", ""), "ts": x.get("ts", 0), "escalated": bool(x.get("escalated"))} for i, x in enumerate(ds) if x["open"]],
             "samples_pending": pend, "samples_waiting_long": any(now - q["ts"] >= svc.SAMPLE_NUDGE_MIN * 60 for q in pend),
             "issues_open": [{"i": x.get("i", i), "name": x["name"], "text": x["text"], "ts": x.get("ts", 0)} for i, x in enumerate(iss) if x.get("open")],
             "stale": stale is not None, "stale_hours": int((now - stale) / 3600) if stale else 0, "stale_since": stale, "shortfall": short,
             "overdue_amount": ms["balance"] if ms["overdue"] else None, "overdue_days": ms["days_overdue"], "money_due_ts": ms["due_ts"],
             "underpriced": _d(od["offered_price"]) < _d(p["per_unit"]["floor"]), "suggested": p.get("suggested_counter"), "on_behalf": bool(d.get("on_behalf")),
             "declined_unplanned": st == "accepted" and bool(d.get("declined")) and not pl, "declined_ts": max(dec) if dec else None,
             "plan_feasible": pl.get("feasible", True), "plan_issues": pl.get("issues", []), "plan_options": pl.get("options", []),
             "accepted_count": len(d.get("task_accepted", [])), "members_count": svc._n_members(o), "leader_approved": bool(ap.get("leader")), "payout_total": (d.get("payout") or {}).get("total"),
             "member_approvals": len(ap.get("members", [])), "member_quorum": quorum, "remaining": max(qty - (d.get("delivery") or {}).get("delivered", 0), 0),
             "counted": ((d.get("delivery") or {}).get("qc") or {}).get("counted"), "blocked_reason": blocked.get("reason"), "blocked_ts": blocked.get("ts"), "last_event_ts": self.last_event_ts.get(o.id), "pg": pg, "money": ms}
        self._f[o.id] = f; return f
    def items(self, o): return W.pending(self.facts(o))
    def card(self, o):
        if o.id in self._c: return self._c[o.id]
        f, d, od = self.facts(o), o.data, o.data["order"]; its = W.pending(f); hk, why = W.health(f); pg = f["pg"]; ms = f["money"]
        nxt = next((i for i in its if i["owner"] == "leader"), None) or (its[0] if its else None)
        stages = []
        for r in pg["rows"]:
            sg = next((x for x in stages if x["stage"] == r["stage"]), None)
            if not sg: sg = {"stage": r["stage"], "done": 0, "units": 0, "members": []}; stages.append(sg)
            sg["done"] += r["done"]; sg["units"] += r["units"]; sg["members"].append(r["name"])
        for sg in stages: sg["pct"] = int(100 * sg["done"] / sg["units"]) if sg["units"] else 0
        key = (d.get("rate_card") or {}).get("product", defaults.DEFAULT_PRODUCT); p = self.price(o)
        c = {"id": o.id, "status": o.status, "status_label": W.STATUS_LABEL.get(o.status, o.status), "column": W.BOARD_OF.get(o.status, "new"), "product": od.get("product") or defaults.TEMPLATES[key]["label"], "product_key": key,
             "qty": od["qty"], "offered": od["offered_price"], "quote": d.get("quote"), "buyer": self.buyer(o), "created": o.created, "due_at": f["due"], "due_estimated": d.get("due_at") is None, "days_left": f["days_left"] if o.status not in W.FINISHED else None,
             "deadline_days": od.get("deadline_days"), "health": hk, "health_reasons": why, "progress": {"pct": pg["pct"], "finished": pg["finished"], "needed": pg["needed"], "stages": stages}, "members_count": f["members_count"],
             "next": nxt, "needs_you": bool(nxt and nxt["owner"] == "leader"), "items": [i for i in its if i["owner"] == "leader"], "floor": p["per_unit"]["floor"], "underpriced": f["underpriced"],
             "money": {k: ms[k] for k in ("total", "paid", "balance", "status", "overdue", "estimated")}, "on_behalf": f["on_behalf"], "location": od.get("location"), "last_event_ts": f["last_event_ts"],
             "blocked": d.get("blocked"), "partial": o.status == "partially_delivered", "delivered": (d.get("delivery") or {}).get("delivered", 0)}
        self._c[o.id] = c; return c
    @cached_property
    def cards(self): return [self.card(o) for o in self.orders]

    # ----- alerts: every leader-owned pending item across the group, plus group-level ones -----
    @cached_property
    def rate_proposals(self):
        return [r for r in self.s.scalars(select(db.RateCard).where(db.RateCard.group_id == self.gid, db.RateCard.status == "proposed"))]
    @cached_property
    def alerts(self):
        out = []
        for o in self.orders:
            out += [i for i in self.items(o) if i["owner"] == "leader"]
        for r in self.rate_proposals:
            out.append({"id": f"ratecard:{r.id}:", "kind": "ratecard", "label": W.KIND_LABEL["ratecard"], "priority": W.PRI["ratecard"], "owner": "member", "order_id": None, "title": f"{defaults.TEMPLATES[r.product]['label']} rate card v{r.version} is waiting for member approval",
                        "detail": f"{len(r.member_ok or [])} of {min(ratecards.QUORUM, len(self.members))} members approved", "tab": "pricing", "action": "open", "cmd": "", "ts": r.created, "escalated": False, "hint": {}})
        mv = materials_status(self)
        for m in mv["short"]:
            out.append({"id": f"material:{m['id']}:", "kind": "material", "label": W.KIND_LABEL["material"], "priority": W.PRI["material"], "owner": "leader", "order_id": None, "title": f"Short of {m['name']}: need {m['short']:g} more {m['unit']}",
                        "detail": "Open orders need more than you have in stock.", "tab": "materials", "action": "open", "cmd": "STOCK", "ts": self.now, "escalated": False, "hint": {}})
        out.sort(key=lambda a: (a["priority"], a["ts"] or 0)); return out
    @cached_property
    def needs(self): return [a for a in self.alerts if a["owner"] == "leader"]
    @cached_property
    def badges(self):
        b = {t: 0 for t in W.TABS}
        for a in self.needs:
            if a["kind"] == "material": b["materials"] += 1
            else: b[a["tab"]] = b.get(a["tab"], 0) + 1
        b["overview"] = b["alerts"] = len(self.needs); b["pricing"] = len(self.rate_proposals)
        b["buyers"] = len({self.card(o)["buyer"]["key"] for o in self.orders if self.facts(o)["money"]["overdue"]})
        return b
    def envelope(self):
        return {"now": self.now, "cursor": self.cursor, "badges": self.badges, "group": {"id": self.gid, "name": self.group.name if self.group else ""}}

# ---------- helpers on top of Ctx ----------
def _active(ctx): return [c for c in ctx.cards if c["status"] in W.ACTIVE]
def week_chart(ctx, days=7):
    fin, _ = ctx.series; today = day_of(ctx.now); by = defaultdict(int)
    for ts, _o, dlt in fin: by[day_of(ts)] += max(dlt, 0)
    return [{"date": (datetime.date.fromordinal(datetime.date(1970, 1, 1).toordinal() + today - k)).isoformat(), "pieces": by.get(today - k, 0)} for k in range(days - 1, -1, -1)]

def task_state(ctx, o, x, m_id):
    d, now = o.data, ctx.now; done = min(int(d.get("progress", {}).get(f"{m_id}:{x['stage']}", 0)), x["units"])
    if o.status != "in_production": return "planned", done
    if done >= x["units"]: return "done", done
    due = d.get("due_at"); start = due - int(d["order"]["deadline_days"]) * DAY; f = max(0.0, min(1.0, (now - start) / max(due - start, 1))); last = ctx.last_report.get((o.id, m_id), start)
    if now > due or (f >= .25 and done < .6 * f * x["units"]) or (f > 0 and now - last >= STALE_H * 3600): return "behind", done
    return "working", done

def member_card(ctx, m):
    tasks, st, reasons = [], "idle", []
    for o in ctx.orders:
        if o.status not in ("planned", "in_production", "payout_pending"): continue
        for x in (o.data.get("plan") or {}).get("assignments", []):
            if x["member"] != m.id: continue
            ts, done = task_state(ctx, o, x, m.id)
            tasks.append({"order_id": o.id, "order_status": o.status, "stage": x["stage"], "units": x["units"], "done": done, "expected_pay": x["expected_pay"], "last_report": ctx.last_report.get((o.id, m.id)), "state": ts, "accepted": m.id in o.data.get("task_accepted", [])})
    live = [t for t in tasks if t["order_status"] == "in_production" and t["state"] in ("working", "behind")]
    if any(t["state"] == "behind" for t in live): st = "behind"; reasons = [f"Order #{t['order_id']} {t['stage']}: {t['done']}/{t['units']}" for t in live if t["state"] == "behind"]
    elif live: st = "working"
    fin, mem = ctx.series; wk = ctx.now - 7 * DAY
    pieces = sum(max(dl, 0) for ts, _o, mm, dl in mem if mm == m.id and ts >= wk)
    declines = sum(1 for e in ctx.led if e.type == "member.declined" and e.actor == f"seller:{m.id}" and e.ts >= wk)
    issues = sum(1 for e in ctx.led if e.type == "member.issue" and e.actor == f"seller:{m.id}" and e.ts >= wk)
    firsts = []
    for o in ctx.orders:
        seen = set()
        for q in sorted(o.data.get("quality", []), key=lambda q: q["i"]):
            if q["member"] == m.id and q["status"] in ("passed", "rework") and (o.id, q["stage"]) not in seen: seen.add((o.id, q["stage"])); firsts.append(q["status"] == "passed")
    exp = sum(float(fairness.order_earnings(o).get(m.id, 0)) for o in ctx.orders if o.status in ("planned", "in_production", "payout_pending"))
    last = max([t["last_report"] for t in tasks if t["last_report"]] or [0]) or None
    phone = m.phone or ""; wa = re.sub(r"\D", "", phone)
    return {"id": m.id, "name": m.name, "phone": phone, "wa": f"https://wa.me/{wa}" if wa else None, "skills": m.skills or [], "capacity": m.capacity or 0, "lang": m.lang or "en", "active": bool(m.active), "status": st if m.active else "inactive",
            "status_reasons": reasons, "tasks": tasks, "last_report": last, "week": {"pieces": pieces, "expected_earnings": f"{exp:.2f}", "first_pass_rate": int(100 * sum(firsts) / len(firsts)) if firsts else None, "first_pass_n": len(firsts), "declines": declines, "issues": issues}}

# ---------- capacity ----------
def member_loads(ctx, horizon=60):
    """{member id: [pieces/day for day 0..horizon-1]}: what each person is already committed to on open orders, spread over the days left."""
    load = {m.id: [0.0] * horizon for m in ctx.members}
    for o in ctx.orders:
        if o.status not in ("planned", "in_production"): continue
        due = o.data.get("due_at"); n = max(math.ceil(((due if due is not None else ctx.now + int(o.data["order"]["deadline_days"]) * DAY) - ctx.now) / DAY), 1)
        for x in (o.data.get("plan") or {}).get("assignments", []):
            if x["member"] not in load: continue
            rem = x["units"] - (min(int(o.data.get("progress", {}).get(f"{x['member']}:{x['stage']}", 0)), x["units"]) if o.status == "in_production" else 0)
            for k in range(min(n, horizon)): load[x["member"]][k] += rem / n
    return load

def calendar(ctx, days=7):
    load = member_loads(ctx, days); today = day_of(ctx.now); rows = []
    dates = [(datetime.date.fromordinal(datetime.date(1970, 1, 1).toordinal() + today + k)).isoformat() for k in range(days)]
    for m in ctx.members:
        cap = m.capacity or 0
        rows.append({"member": m.id, "name": m.name, "capacity": cap, "days": [{"date": dates[k], "load": math.ceil(load[m.id][k]), "over": load[m.id][k] > cap + 0.01} for k in range(days)]})
    return {"dates": dates, "rows": rows}

def can_take(ctx, product, qty, days):
    try: qty, days = int(qty), int(days); assert qty > 0 and 1 <= days <= 365
    except Exception: raise svc.Err(422, "Give a quantity above 0 and a number of days from 1 to 365")
    if product not in defaults.TEMPLATES: raise svc.Err(422, "Choose a product")
    card = ratecards.active(ctx.s, ctx.gid, product); load = member_loads(ctx, 60)
    def attempt(q, dd):
        dd = min(dd, 60); pool = {m.id: sum(max(0.0, (m.capacity or 0) - load[m.id][k]) for k in range(dd)) for m in ctx.members}; rows = []
        for st in sorted(card["stages"], key=lambda st: sum(1 for m in ctx.members if st["name"] in (m.skills or []))):       # scarcest skill first; one person's time is not counted twice
            el = [m for m in ctx.members if st["name"] in (m.skills or [])]; avail = sum(pool[m.id] for m in el); need = q
            for m in sorted(el, key=lambda m: -pool[m.id]):
                take = min(pool[m.id], need); pool[m.id] -= take; need -= take
            rows.append({"stage": st["name"], "need": q, "available": int(avail), "ok": need <= 1e-9, "people": len(el), "short": int(math.ceil(max(need, 0)))})
        return rows
    rows = attempt(qty, days); ok = all(r["ok"] for r in rows)
    lo, hi = 0, qty
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if all(r["ok"] for r in attempt(mid, days)): lo = mid
        else: hi = mid - 1
    earliest = next((dd for dd in range(days, 61) if all(r["ok"] for r in attempt(qty, dd))), None)
    missing = [r["stage"] for r in rows if r["people"] == 0]
    return {"ok": ok, "product": product, "qty": qty, "days": days, "stages": rows, "max_qty_by_deadline": lo, "earliest_days": earliest, "missing_skills": missing,
            "basis": "Declared daily capacity minus the work already booked on open orders. An estimate: the plan itself is still made when the order is accepted."}

# ---------- views ----------
def overview(ctx):
    act = _active(ctx); wk = week_chart(ctx); today = wk[-1]["pieces"]
    ontrack = sum(1 for c in act if c["health"] == "on_track"); pay_wait = [c for c in act if c["status"] == "payout_pending" and not ctx.facts(ctx.byid[c["id"]])["leader_approved"]]
    due = sum((_d(c["money"]["balance"]) for c in ctx.cards if c["status"] in M.OWED_FROM), D(0))
    team = [member_card(ctx, m) for m in ctx.members]
    return {**ctx.envelope(), "kpis": {"active_orders": len(act), "finished_today": today, "on_track": ontrack, "waiting_decision": len(ctx.needs), "payout_awaiting": len(pay_wait),
            "payout_awaiting_amount": _s(sum((_d(ctx.facts(ctx.byid[c["id"]])["payout_total"] or 0) for c in pay_wait), D(0))), "money_due": _s(due)},
            "needs": ctx.needs[:5], "needs_total": len(ctx.needs), "live_orders": sorted(act, key=lambda c: (c["due_at"] or 0))[:10],
            "due_soon": sorted([c for c in act if c["days_left"] is not None and c["days_left"] <= 3 and c["status"] != "blocked"], key=lambda c: c["days_left"]),
            "team": {"working": sum(m["status"] == "working" for m in team), "behind": sum(m["status"] == "behind" for m in team), "idle": sum(m["status"] == "idle" for m in team)},
            "week": wk, "activity": activity(ctx, limit=8)["rows"]}

def orders_view(ctx):
    cols = [{"key": k, "statuses": sts} for k, sts in W.BOARD]
    return {**ctx.envelope(), "orders": ctx.cards, "columns": cols, "buyers": sorted({c["buyer"]["name"] for c in ctx.cards}), "products": sorted({c["product"] for c in ctx.cards}), "products_catalog": svc.products()}

def team_view(ctx):
    ms = [member_card(ctx, m) for m in ctx.sellers]; hist, n = fairness.history(ctx.s, ctx.gid); tot = sum(hist.values(), D(0))
    fair = [{"member": m.id, "name": m.name, "amount": _s(hist.get(m.id, 0)), "share_pct": int(100 * hist.get(m.id, 0) / tot) if tot else 0} for m in ctx.members]
    act = [m for m in ms if m["active"]]
    msgs = list(ctx.s.scalars(select(db.Message).where(db.Message.group_id == ctx.gid).order_by(db.Message.id.desc()).limit(30)))
    return {**ctx.envelope(), "members": ms, "counts": {k: sum(m["status"] == k for m in act) for k in ("working", "behind", "idle")}, "calendar": calendar(ctx), "fair": {"rows": fair, "orders_considered": n},
            "stages": svc.ALL_STAGES, "templates": [{"key": k, "text": v["en"]} for k, v in TEMPLATES.items()],
            "messages": [{"id": x.id, "to": ctx.users[x.to_id].name if x.to_id in ctx.users else "?", "body": x.body, "status": x.status, "ts": x.ts, "batch": x.batch} for x in msgs]}

TEMPLATES = {"daily": {"en": "Please send today's DONE count for your task.", "hi": "कृपया अपने काम की आज की DONE गिनती भेजें।", "hing": "Kripya apne kaam ki aaj ki DONE ginti bhejein."},
             "photo": {"en": "Please send a photo of your first finished piece.", "hi": "कृपया अपना पहला तैयार पीस का फोटो भेजें।", "hing": "Kripya apna pehla tayyar piece ka photo bhejein."},
             "pay": {"en": "Your payout is ready. Please check it and approve, or tell me what looks wrong.", "hi": "आपका भुगतान तैयार है। कृपया देखें और मंज़ूर करें, या बताएं कि क्या गलत है।", "hing": "Aapka payout ready hai. Kripya dekhein aur approve karein, ya batayein kya galat hai."},
             "call": {"en": "Please call me when you are free.", "hi": "फ्री होने पर कृपया मुझे फोन करें।", "hing": "Free hone par kripya mujhe phone karein."},
             "ready": {"en": "Work starts tomorrow. Please be ready.", "hi": "काम कल से शुरू होगा। कृपया तैयार रहें।", "hing": "Kaam kal se shuru hoga. Kripya taiyaar rahein."}}

def member_detail(ctx, mid):
    m = next((x for x in ctx.sellers if x.id == mid), None)
    if not m: raise svc.Err(404, "Member not found")
    card = member_card(ctx, m); reps, samples, disputes, earn = [], [], [], []
    _f, mem = ctx.series
    for ts, oid, mm, dlt in sorted(mem, reverse=True):
        if mm == m.id: reps.append({"ts": ts, "order_id": oid, "pieces": dlt})
    for o in ctx.orders:
        for q in o.data.get("quality", []):
            if q["member"] == m.id: samples.append({"order_id": o.id, "i": q["i"], "stage": q["stage"], "status": q["status"], "ts": q.get("ts"), "reason": q.get("reason", ""), "rework": q.get("rework", 0), "has_photo": bool(q.get("photo"))})
        for i, dd in enumerate(o.data.get("disputes", [])):
            if dd["uid"] == m.id: disputes.append({"order_id": o.id, "i": i, "reason": dd["reason"], "open": dd["open"], "ts": dd.get("ts")})
        for l in (o.data.get("payout") or {}).get("lines", []):
            if l["member"] == m.id: earn.append({"order_id": o.id, "status": o.status, "stage": l["stage"], "paid": l["paid"], "pending": l["pending_amount"], "accepted": l["accepted"], "rework": l.get("pending", 0)})
    return {**ctx.envelope(), "member": card, "reports": reps[:60], "samples": sorted(samples, key=lambda x: -(x["ts"] or 0)), "disputes": disputes, "earnings": earn}

def quality_view(ctx):
    queue, hist, firsts, by_stage, dec_t, rework_pcs = [], [], defaultdict(list), defaultdict(list), [], 0
    for o in ctx.orders:
        seen, name = set(), (o.data["order"].get("product") or "")
        for q in sorted(o.data.get("quality", []), key=lambda q: q["i"]):
            sc = q.get("screening") or {}
            row = {"order_id": o.id, "i": q["i"], "member": q["member"], "name": q["name"], "stage": q["stage"], "note": q.get("note", ""), "status": q["status"], "ts": q.get("ts"), "waiting_s": int(ctx.now - q.get("ts", ctx.now)) if q["status"] == "pending" else None,
                   "photo_url": f"/api/leader/orders/{o.id}/quality/{q['i']}/photo" if q.get("photo") else None, "screening": sc or None, "flags": [x for x in (sc.get("items") or []) if x.get("result") == "possible_defect"],
                   "cannot_tell": [x for x in (sc.get("items") or []) if x.get("result") == "cannot_tell"], "escalated": bool(q.get("escalated")), "rework": q.get("rework", 0), "reason": q.get("reason", ""), "decided_ts": q.get("decided_ts"),
                   "ai_flagged": q.get("ai_flagged"), "product": name}
            (queue if q["status"] == "pending" else hist).append(row)
            if q["status"] in ("passed", "rework"):
                if q.get("decided_ts"): dec_t.append(q["decided_ts"] - q.get("ts", q["decided_ts"]))
                rework_pcs += q.get("rework", 0) if q["status"] == "rework" else 0
                firsts[q["member"]].append(q["status"] == "passed"); by_stage[q["stage"]].append(q["status"] == "passed")
    queue.sort(key=lambda r: r["ts"] or 0); hist.sort(key=lambda r: -(r["decided_ts"] or 0))
    def rate(l): return int(100 * sum(l) / len(l)) if l else None
    stats = {"by_member": [{"member": m.id, "name": m.name, "rate": rate(firsts.get(m.id, [])), "n": len(firsts.get(m.id, []))} for m in ctx.sellers if firsts.get(m.id)],
             "by_stage": [{"stage": k, "rate": rate(v), "n": len(v)} for k, v in sorted(by_stage.items())], "rework_pieces": rework_pcs, "avg_decision_min": int(sum(dec_t) / len(dec_t) / 60) if dec_t else None, "decided": len(dec_t)}
    cl = [{"order_id": c["id"], "status": c["status"], "product": c["product"], "buyer": c["buyer"]["name"], "checklist": svc._checklist(ctx.byid[c["id"]])} for c in ctx.cards if c["status"] in ("accepted", "planned", "in_production", "submitted", "quoted")]
    return {**ctx.envelope(), "queue": queue, "history": hist[:80], "stats": stats, "checklists": cl}

def delivery_view(ctx):
    pipe = []
    for key, label, sts in W.PIPELINE:
        cs = [c for c in ctx.cards if c["status"] in sts]
        pipe.append({"key": key, "label": label, "count": len(cs), "orders": [_dcard(ctx, c) for c in cs]})
    today = day_of(ctx.now); cal = defaultdict(list)
    for c in ctx.cards:
        if c["status"] in W.FINISHED or c["due_at"] is None: continue
        k = day_of(c["due_at"]) - today
        if k <= 14: cal[max(k, 0) if k >= 0 else -1].append({"order_id": c["id"], "buyer": c["buyer"]["name"], "qty": c["qty"], "status": c["status"], "health": c["health"]})
    days = [{"date": (datetime.date.fromordinal(datetime.date(1970, 1, 1).toordinal() + today + k)).isoformat(), "offset": k, "orders": cal.get(k, [])} for k in range(0, 15)]
    return {**ctx.envelope(), "pipeline": pipe, "calendar": days, "overdue": cal.get(-1, [])}
def _dcard(ctx, c):
    o = ctx.byid[c["id"]]; dv = svc._dview(o); qc = dv["qc"] or {}
    return {**{k: c[k] for k in ("id", "status", "product", "qty", "due_at", "days_left", "health")}, "buyer": c["buyer"]["name"], "address": c["location"], "owed": dv["remaining"], "packed": qc.get("counted"), "delivered": dv["delivered"], "qc": qc or None,
            "shipments": dv["shipments"], "blocked": dv["blocked"], "partial": c["partial"], "next": c["next"]}

def margin(ctx, o):
    ms = ctx.facts(o)["money"]; rev = _d(ms["total"]) if o.status != "cancelled" else D(0); d = o.data
    pay = _d((d.get("payout") or {}).get("total")) if d.get("payout") else sum((v for v in fairness.order_earnings(o).values()), D(0))
    used = d.get("materials_used") or []; planned, basis = planned_material_cost(ctx, o)
    mat = sum((_d(u["cost"]) for u in used), D(0)) if used else planned; basis = "actual" if used else basis
    mg = rev - pay - mat
    return {"revenue": _s(rev), "member_pay": _s(pay), "materials": _s(mat), "materials_basis": basis, "margin": _s(mg), "margin_pct": int(100 * mg / rev) if rev else None, "estimate": True}

def payouts_view(ctx):
    props, ready, summ = [], [], {m.id: {"member": m.id, "name": m.name, "paid": D(0), "awaiting": D(0), "rework_pending": D(0), "expected": D(0)} for m in ctx.sellers}
    for o in ctx.orders:
        d, c = o.data, ctx.card(o); f = ctx.facts(o)
        if d.get("payout"):
            lines = d["payout"]["lines"]
            for l in lines:
                r = summ.setdefault(l["member"], {"member": l["member"], "name": l["name"], "paid": D(0), "awaiting": D(0), "rework_pending": D(0), "expected": D(0)})
                if o.status == "payout_pending": r["awaiting"] += _d(l["paid"])
                elif o.status in svc.POST_PAY: r["paid"] += _d(l["paid"])
                r["rework_pending"] += _d(l["pending_amount"])
            props.append({"order_id": o.id, "status": o.status, "buyer": c["buyer"]["name"], "product": c["product"], "lines": lines, "total": d["payout"]["total"], "leader_approved": f["leader_approved"],
                          "members_approved": [ctx.users[i].name if i in ctx.users else str(i) for i in (d.get("approvals") or {}).get("members", [])], "have": f["member_approvals"], "needed": f["member_quorum"],
                          "disputes": [{"i": i, "name": x["name"], "reason": x["reason"], "open": x["open"], "note": x.get("note", ""), "escalated": bool(x.get("escalated"))} for i, x in enumerate(d.get("disputes", []))], "proposed_at": d.get("payout_at")})
        elif o.status == "in_production":
            rw = d.get("rework", {}); rows = []
            for x in d["plan"]["assignments"]:
                k = f"{x['member']}:{x['stage']}"; r = min(int(rw.get(k, 0)), x["units"]); done = min(int(d.get("progress", {}).get(k, 0)), x["units"])
                rows.append({"member": x["member"], "name": x["name"], "stage": x["stage"], "units": x["units"], "done": done, "accepted": x["units"] - r, "pending": r, "rate": x["rate"]})
            ready.append({"order_id": o.id, "buyer": c["buyer"]["name"], "product": c["product"], "pct": c["progress"]["pct"], "all_done": f["all_done"], "rows": rows})
        if o.status in ("planned", "in_production") and not d.get("payout"):
            for x in (d.get("plan") or {}).get("assignments", []): summ.setdefault(x["member"], {"member": x["member"], "name": x["name"], "paid": D(0), "awaiting": D(0), "rework_pending": D(0), "expected": D(0)})["expected"] += _d(x["expected_pay"])
    mrows = [{**r, **{k: _s(r[k]) for k in ("paid", "awaiting", "rework_pending", "expected")}} for r in summ.values()]
    money_in = []
    for c in ctx.cards:
        o = ctx.byid[c["id"]]; ms = ctx.facts(o)["money"]
        if o.status in M.OWED_FROM or (o.status == "cancelled" and _d(ms["paid"]) > 0):
            money_in.append({"order_id": o.id, "buyer": c["buyer"]["name"], "status": o.status, "total": ms["total"], "advance": ms["advance"], "paid": ms["paid"], "balance": ms["balance"], "pay_status": ms["status"], "due_ts": ms["due_ts"], "overdue": ms["overdue"],
                             "days_overdue": ms["days_overdue"], "estimated": ms["estimated"], "payments": ms["payments"], "reminders": ms["reminders"], "refund_due": ms["refund_due"], "can_record": o.status in M.OWED_FROM and _d(ms["balance"]) > 0,
                             "buyer_phone_known": bool(c["buyer"]["phone"])})
    marg = [{"order_id": c["id"], "buyer": c["buyer"]["name"], "status": c["status"], **margin(ctx, ctx.byid[c["id"]])} for c in ctx.cards if c["status"] not in ("submitted", "quoted", "cancelled")]
    return {**ctx.envelope(), "proposals": props, "ready_to_propose": ready, "member_summary": sorted(mrows, key=lambda r: r["name"]), "money_in": money_in, "margins": marg, "quorum": svc.QUORUM,
            "totals": {"balance_due": _s(sum((_d(r["balance"]) for r in money_in), D(0))), "received": _s(sum((_d(r["paid"]) for r in money_in), D(0)))}}

# ---------- buyers ----------
def _buyers(ctx):
    g = defaultdict(list)
    for c in ctx.cards: g[c["buyer"]["key"]].append(c)
    return g
def _closed_ts(o):
    sh = (o.data.get("delivery") or {}).get("shipments", []); return o.data.get("closed_at") or (max([x["signoff"]["ts"] for x in sh if x.get("signoff")] or [0]) or None)
def _buyer_stats(ctx, cs):
    ords = [ctx.byid[c["id"]] for c in cs]; closed = [o for o in ords if o.status == "closed"]; ontime = [o for o in closed if _closed_ts(o) and o.data.get("due_at") and _closed_ts(o) <= o.data["due_at"] + DAY]
    days = []
    for o in closed:
        pays = (o.data.get("money") or {}).get("payments", []); ms = ctx.facts(o)["money"]; cts = _closed_ts(o)
        if pays and cts and _d(ms["paid"]) >= _d(ms["total"]) > 0: days.append(max(0, (max(p["ts"] for p in pays) - cts) / DAY))
    rates = [o.data["rating"]["stars"] for o in ords if o.data.get("rating")]
    bal = sum((_d(ctx.facts(o)["money"]["balance"]) for o in ords if o.status in M.OWED_FROM), D(0))
    return {"orders": len(cs), "active": sum(c["status"] in W.ACTIVE for c in cs), "settled": len(closed), "on_time_rate": int(100 * len(ontime) / len(closed)) if closed else None, "avg_rating": round(sum(rates) / len(rates), 1) if rates else None,
            "ratings": len(rates), "balance_due": _s(bal), "avg_days_to_pay": round(sum(days) / len(days), 1) if days else None, "overdue": any(ctx.facts(o)["money"]["overdue"] for o in ords)}
def buyers_view(ctx):
    rows = []
    for key, cs in _buyers(ctx).items():
        b = cs[0]["buyer"]; prof = ctx.profiles.get(key)
        rows.append({"key": key, "name": b["name"], "business": b["name"], "phone": b["phone"], "registered": b["registered"], "last_order": max(c["created"] or 0 for c in cs), **_buyer_stats(ctx, cs)})
    rows.sort(key=lambda r: (-int(r["overdue"]), -r["last_order"]))
    return {**ctx.envelope(), "buyers": rows}
def buyer_profile(ctx, key):
    cs = _buyers(ctx).get(key)
    if not cs: raise svc.Err(404, "Buyer not found")
    b = cs[0]["buyer"]; prof = ctx.profiles.get(key); ords = [ctx.byid[c["id"]] for c in cs]; addr = []
    for o in ords:
        a = (o.data["order"].get("location") or "").strip()
        if a and a not in addr: addr.append(a)
    for a in (prof.addresses if prof else []) or []:
        if a not in addr: addr.append(a)
    specs, seen = [], set()
    for o in ords:                                                              # newest first: one saved specification per product, from the latest order for it
        od = o.data["order"]; k = (od.get("product") or "").lower()
        if k in seen: continue
        seen.add(k); specs.append({"order_id": o.id, "product": od.get("product"), "qty": od["qty"], "offered_price": od["offered_price"], "size": od.get("size"), "colour": od.get("colour"), "specs": od.get("specs"), "packaging": od.get("packaging"), "location": od.get("location")})
    return {**ctx.envelope(), "buyer": {**b, **_buyer_stats(ctx, cs)}, "phone_visibility": "Only the group leader can see this number. The buyer is told this when ordering." if b["registered"] else "Saved by you; not shared with members.",
            "addresses": addr, "timeline": [{**{k: c[k] for k in ("id", "status", "status_label", "product", "qty", "offered", "quote", "created", "health")}, "money": c["money"], "rating": ctx.byid[c["id"]].data.get("rating")} for c in cs], "specs": specs,
            "notes": (prof.notes if prof else "") or "", "wa": ("https://wa.me/" + re.sub(r"\D", "", b["phone"])) if b["phone"] else None}

# ---------- pricing ----------
def pricing_view(ctx):
    rc = svc.ratecards_list(ctx.user); under = []
    for c in ctx.cards:
        o = ctx.byid[c["id"]]; p = ctx.price(o); od = o.data["order"]
        if _d(od["offered_price"]) < _d(p["per_unit"]["floor"]):
            under.append({"order_id": c["id"], "buyer": c["buyer"]["name"], "product": c["product"], "offered": od["offered_price"], "floor": p["per_unit"]["floor"], "gap": _s(_d(p["per_unit"]["floor"]) - _d(od["offered_price"])), "pay_gap_pct": p["pay_gap_pct"],
                          "effective_hourly": p["effective_hourly_at_offer"], "target_hourly": p["target_hourly"], "quote": c["quote"], "status": c["status_label"], "created": c["created"], "qty": c["qty"]})
    members = len(ctx.members)
    for h in rc["history"]: h["needed_total"] = min(ratecards.QUORUM, members)
    return {**ctx.envelope(), **rc, "products": svc.products(), "underpriced": under, "members": members}
def price_calc(ctx, product, qty, offered):
    try: qty, offered = int(qty), _d(offered); assert qty > 0 and offered > 0
    except Exception: raise svc.Err(422, "Give a quantity and the buyer's offered price per piece")
    if product not in defaults.TEMPLATES: raise svc.Err(422, "Choose a product")
    card = ratecards.active(ctx.s, ctx.gid, product); p = E.price_check(qty, **card["cost"], offered=offered)
    return {"rate_card": {"product": product, "version": card["version"], "source": card["source"]}, "price": p, "floor": p["per_unit"]["floor"], "suggested_counter": p["suggested_counter"], "effective_hourly": p["effective_hourly_at_offer"], "target_hourly": p["target_hourly"],
            "pay_gap_pct": p["pay_gap_pct"], "below_floor": offered < _d(p["per_unit"]["floor"]), "can_take": can_take(ctx, product, qty, 7)["ok"] if ctx.members else False}

# ---------- materials ----------
def _avg_price(ctx):
    tot, qty = defaultdict(float), defaultdict(float)
    for p in ctx.s.scalars(select(db.Purchase).where(db.Purchase.group_id == ctx.gid)): tot[p.material_id] += p.cost; qty[p.material_id] += p.qty
    return {k: tot[k] / qty[k] for k in qty if qty[k]}
def planned_material_cost(ctx, o):
    mats = list(ctx.s.scalars(select(db.Material).where(db.Material.group_id == ctx.gid, db.Material.active == True)))  # noqa: E712
    key = (o.data.get("rate_card") or {}).get("product", defaults.DEFAULT_PRODUCT); qty = o.data["order"]["qty"]; avg = _avg_price(ctx)
    if any((m.per_piece or {}).get(key) for m in mats): return sum((D(str(round((m.per_piece or {}).get(key, 0) * qty * avg.get(m.id, 0), 2))) for m in mats), D(0)), "planned"
    return D(str((o.data.get("rate_card") or svc._card(o))["cost"]["material"])) * qty, "rate card"
def materials_status(ctx):
    mats = list(ctx.s.scalars(select(db.Material).where(db.Material.group_id == ctx.gid, db.Material.active == True)))  # noqa: E712
    if not mats: return {"materials": [], "per_order": [], "short": []}
    avail = {m.id: m.qty or 0 for m in mats}; per, short = [], defaultdict(float)
    opens = sorted([o for o in ctx.orders if o.status in ("accepted", "planned", "in_production")], key=lambda o: ctx.facts(o)["due"])
    for o in opens:
        key = (o.data.get("rate_card") or {}).get("product", defaults.DEFAULT_PRODUCT); used = defaultdict(float)
        for u in o.data.get("materials_used", []): used[u["material_id"]] += u["qty"]
        rows = []
        for m in mats:
            pp = (m.per_piece or {}).get(key, 0)
            if not pp: continue
            need = max(pp * o.data["order"]["qty"] - used.get(m.id, 0), 0); have = min(avail[m.id], need); avail[m.id] -= have; sh = need - have; short[m.id] += sh
            rows.append({"material": m.id, "name": m.name, "unit": m.unit, "per_piece": pp, "need": round(need, 2), "short": round(sh, 2)})
        per.append({"order_id": o.id, "product": ctx.card(o)["product"], "qty": o.data["order"]["qty"], "rows": rows, "short": any(r["short"] > 0 for r in rows)})
    return {"materials": mats, "per_order": per, "short": [{"id": m.id, "name": m.name, "unit": m.unit, "short": round(short[m.id], 2)} for m in mats if short[m.id] > 1e-9]}
def materials_view(ctx):
    st = materials_status(ctx); avg = _avg_price(ctx)
    sup = list(ctx.s.scalars(select(db.Supplier).where(db.Supplier.group_id == ctx.gid).order_by(db.Supplier.name)))
    pur = list(ctx.s.scalars(select(db.Purchase).where(db.Purchase.group_id == ctx.gid).order_by(db.Purchase.id.desc()).limit(50)))
    names, snames = {m.id: m for m in st["materials"]}, {s.id: s.name for s in sup}
    last_sup = {}
    for p in reversed(pur):
        if p.supplier_id: last_sup[p.material_id] = snames.get(p.supplier_id)
    plan_actual = []
    for c in ctx.cards:
        o = ctx.byid[c["id"]]
        if c["status"] in ("submitted", "quoted", "cancelled"): continue
        planned, basis = planned_material_cost(ctx, o); used = o.data.get("materials_used", []); act = sum((_d(u["cost"]) for u in used), D(0))
        plan_actual.append({"order_id": o.id, "status": c["status"], "planned": _s(planned), "basis": basis, "actual": _s(act) if used else None, "diff": _s(act - planned) if used else None, "used": used})
    mats = [{"id": m.id, "name": m.name, "unit": m.unit, "qty": round(m.qty or 0, 2), "per_piece": m.per_piece or {}, "avg_price": round(avg.get(m.id, 0), 2) or None} for m in st["materials"]]
    return {**ctx.envelope(), "materials": mats, "per_order": st["per_order"], "shopping": [{**x, "supplier": last_sup.get(x["id"])} for x in st["short"]], "suppliers": [{"id": s.id, "name": s.name, "phone": s.phone, "note": s.note} for s in sup],
            "purchases": [{"id": p.id, "material": names[p.material_id].name if p.material_id in names else "?", "unit": names[p.material_id].unit if p.material_id in names else "", "qty": p.qty, "cost": p.cost, "supplier": snames.get(p.supplier_id), "ts": p.ts, "note": p.note} for p in pur],
            "plan_vs_actual": plan_actual, "products": [{"key": k, "label": t["label"]} for k, t in defaults.TEMPLATES.items()]}

# ---------- alerts, reminders, activity ----------
def alerts_view(ctx):
    escs = [e for e in ctx.s.scalars(select(db.LedgerEvent).where(db.LedgerEvent.type == "escalation.raised"))]
    rules = [{"to": "Members", "what": "Task due", "rule": f"{svc.TASK_LEAD_DAYS} day(s) before the delivery date, then at most once every {svc.COOLDOWN_H} hours"},
             {"to": "You", "what": "Deadline risk", "rule": "When the work pace shows a shortfall, at most once every %d hours" % svc.COOLDOWN_H},
             {"to": "You", "what": "Sample waiting for review", "rule": f"After {svc.SAMPLE_NUDGE_MIN} minutes"}, {"to": "You and members", "what": "Payout waiting for approval", "rule": f"After {svc.PAYOUT_NUDGE_H} hours"},
             {"to": "You", "what": "Stale order", "rule": f"No progress report for {int(STALE_H)} hours while work is open"}, {"to": "You", "what": "Buyer payment overdue", "rule": "When the balance is past its due date"},
             {"to": "Mission officer", "what": "Escalation", "rule": f"Pay disputes and samples that wait more than {svc.ESC_MIN} minutes for you are flagged. Member names and pay are not shared."}]
    kinds = defaultdict(int)
    for a in ctx.needs: kinds[a["kind"]] += 1
    return {**ctx.envelope(), "alerts": ctx.alerts, "kinds": {k: {"label": W.KIND_LABEL[k], "count": n} for k, n in kinds.items()}, "rules": rules,
            "escalation": {"after_minutes": svc.ESC_MIN, "times_raised": len(escs), "last": max([e.ts for e in escs] or [0]) or None, "scheduler_minutes": int(os.getenv("SCHEDULER_MINUTES", "15"))}}

def _event_group(ctx, e, cache):
    if e.order_id: return ctx.s.get(db.Order, e.order_id).group_id if e.order_id not in ctx.byid else ctx.gid
    p = e.payload or {}
    if p.get("group") is not None: return p["group"]
    role, _, ident = str(e.actor).partition(":")
    if role == "group" and ident.isdigit(): return int(ident)
    if ident.isdigit():
        if ident not in cache: u = ctx.s.get(db.User, int(ident)); cache[ident] = u.group_id if u else None
        return cache[ident]
    return None
def _row(ctx, e):
    ev = {"type": e.type, "actor": e.actor, "order_id": e.order_id, "payload": e.payload}
    return {"i": e.i, "ts": e.ts, "type": e.type, "order_id": e.order_id, "actor": e.actor, "who": ctx.name(e.actor), "text": W.describe(ev, ctx.name), "hash": e.hash[:12], "tabs": W.tabs_for(e.type)}
def activity(ctx, order=None, member=None, type_=None, limit=200):
    q = select(db.LedgerEvent).where(or_(db.LedgerEvent.order_id.in_(list(ctx.byid) or [-1]), db.LedgerEvent.order_id.is_(None))).order_by(db.LedgerEvent.i.desc())
    rows, cache = [], {}
    for e in ctx.s.scalars(q.limit(max(limit * 6, 400))):
        if e.order_id is None and _event_group(ctx, e, cache) != ctx.gid: continue
        if order and e.order_id != order: continue
        if type_ and not e.type.startswith(type_): continue
        if member and not (e.actor == f"seller:{member}" or (e.payload or {}).get("member") == member): continue
        rows.append(_row(ctx, e))
        if len(rows) >= limit: break
    return {**ctx.envelope(), "rows": rows, "types": sorted({r["type"].split(".")[0] for r in rows})}
def activity_csv(ctx, **kw):
    out = io.StringIO(); w = csv.writer(out); w.writerow(["event", "time", "type", "order", "who", "what", "hash"])
    for r in reversed(activity(ctx, limit=5000, **kw)["rows"]): w.writerow([r["i"], datetime.datetime.fromtimestamp(r["ts"]).isoformat(timespec="seconds"), r["type"], r["order_id"] or "", r["who"], r["text"], r["hash"]])
    return out.getvalue()
def events_since(s, user, after):
    """The live feed. Cheap when nothing happened (one indexed query). The dashboard re-reads only the tabs listed in `tabs`."""
    top = s.scalars(select(db.LedgerEvent.i).order_by(db.LedgerEvent.i.desc())).first() or 0
    if after is None: return {"cursor": top, "events": [], "tabs": []}
    if top <= after: return {"cursor": top, "events": [], "tabs": []}
    ctx = Ctx(s, user); cache, out, tabs = {}, [], set()
    for e in s.scalars(select(db.LedgerEvent).where(db.LedgerEvent.i > after).order_by(db.LedgerEvent.i).limit(300)):
        if (e.order_id in ctx.byid) or (e.order_id is None and _event_group(ctx, e, cache) == ctx.gid):
            r = _row(ctx, e); out.append(r); tabs.update(r["tabs"])
    return {"cursor": top, "events": out[-30:], "tabs": sorted(tabs), "badges": ctx.badges, "now": ctx.now}

def timeline(ctx, oid):
    if oid not in ctx.byid: raise svc.Err(404, "Order not found")
    rows = [_row(ctx, e) for e in ctx.led if e.order_id == oid]
    return {"rows": rows, "ledger": svc.LED.verify()}

def search(ctx, q):
    q = (q or "").strip().lower().lstrip("#")
    if len(q) < 1: return []
    out = []
    for c in ctx.cards:
        if q == str(c["id"]) or q in c["buyer"]["name"].lower() or q in (c["product"] or "").lower(): out.append({"type": "order", "id": c["id"], "label": f"Order #{c['id']}: {c['product']} × {c['qty']} for {c['buyer']['name']}", "tab": "orders", "status": c["status_label"]})
    for m in ctx.sellers:
        if q in m.name.lower() or q in (m.phone or "").replace("+", ""): out.append({"type": "member", "id": m.id, "label": f"Member: {m.name}", "tab": "team"})
    seen = set()
    for key, cs in _buyers(ctx).items():
        if q in cs[0]["buyer"]["name"].lower() and key not in seen: seen.add(key); out.append({"type": "buyer", "id": key, "label": f"Buyer: {cs[0]['buyer']['name']}", "tab": "buyers"})
    return out[:20]

def order_detail(ctx, oid):
    o = ctx.byid.get(oid)
    if not o: raise svc.Err(404, "Order not found")
    c, f, d, od = ctx.card(o), ctx.facts(o), o.data, o.data["order"]; asg = (d.get("plan") or {}).get("assignments", []); rows = []
    for x in asg:
        ts, done = task_state(ctx, o, x, x["member"])
        rows.append({"member": x["member"], "name": x["name"], "stage": x["stage"], "units": x["units"], "done": done, "state": ts, "last_report": ctx.last_report.get((o.id, x["member"])), "expected_pay": x["expected_pay"], "rate": x["rate"],
                     "accepted": x["member"] in d.get("task_accepted", []), "why": x.get("why", "")})
    p = ctx.price(o); rc = svc._card(o); ms = f["money"]
    qual = [{"i": q["i"], "member": q["member"], "name": q["name"], "stage": q["stage"], "status": q["status"], "ts": q.get("ts"), "note": q.get("note", ""), "has_photo": bool(q.get("photo")), "screening": q.get("screening"), "rework": q.get("rework", 0), "reason": q.get("reason", "")} for q in d.get("quality", [])]
    ev = d.get("evidence") or {}
    return {**ctx.envelope(), "card": c, "order": od, "unclear": d.get("unclear", []), "evidence": {"source": ev.get("source"), "text": (ev.get("text") or ev.get("transcript") or ev.get("extracted_text") or "")[:1200], "has_photo": bool(ev.get("photo"))},
            "work": {"rows": rows, "declined": [ctx.users[i].name if i in ctx.users else str(i) for i in d.get("declined", [])], "issues": d.get("issues", []), "plan_issues": (d.get("plan") or {}).get("issues", []), "options": (d.get("plan") or {}).get("options", []),
                     "reassignable": o.status in ("planned", "in_production"), "members": [{"id": m.id, "name": m.name, "skills": m.skills or [], "capacity": m.capacity} for m in ctx.members], "risk": d.get("risk")},
            "quality": {"samples": qual, "checklist": svc._checklist(o)},
            "money": {"offered": od["offered_price"], "floor": p["per_unit"]["floor"], "quote": d.get("quote"), "effective_hourly_at_offer": p["effective_hourly_at_offer"], "target_hourly": p["target_hourly"], "pay_gap_pct": p["pay_gap_pct"], "loss_making": p["loss_making"],
                      "rate_card": {"product": rc["product"], "version": rc["version"], "source": rc.get("source")}, "payout": d.get("payout"), "approvals": d.get("approvals"), "leader_approved": f["leader_approved"], "have": f["member_approvals"], "needed": f["member_quorum"],
                      "buyer": {k: ms[k] for k in ("total", "paid", "advance", "balance", "status", "overdue", "days_overdue", "estimated", "payments", "due_ts", "reminders")}, "margin": margin(ctx, o)},
            "delivery": {**svc._dview(o), "counted": f["counted"], "next": c["next"]}, "notes": d.get("notes", []), "rating": d.get("rating"), "buyer": ctx.buyer(o)}
