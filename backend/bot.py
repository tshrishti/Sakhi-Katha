"""WhatsApp commands for buyers, leaders and members. Same service layer and permissions as the dashboards.
Like MaaSakhi: short messages, the number identifies the user, language is chosen on first contact, photos and voice notes work.
Unknown numbers get nothing unless OPEN_REGISTRATION=1 (then they register themselves like in MaaSakhi); otherwise the leader adds members."""
from sqlalchemy import select
import auth, bootstrap, convo, db, i18n, svc

SEEN, PREF, ONB = set(), {}, {}
SKILLS = {"1": "cutting", "2": "stitching", "3": "finishing", "4": "packing"}
HELP = {"buyer": "Send your order as text or voice (quantity, price per piece, days). STATUS lists orders. ACCEPT <id> accepts a quote. RECEIVED <id> <pieces> confirms delivery (add FINAL to accept a shortfall). CANCEL <id> before work starts. LANG 1/2/3 changes language.",
        "leader": "ORDERS, QUOTE <id> [price], PLAN <id>, START <id>, PASS <id> <name>, REWORK <id> <name> <pieces> <reason>, RISK <id> [days left], PROGRESS <id>, CHECKLIST <id>, PAYOUT <id> [name rework-count], APPROVE <id>, RESOLVE <id> <note>, READY <id> <packed pieces>, DISPATCH <id> <courier> [tracking], BLOCK <id> <reason>, UNBLOCK <id>, CANCEL <id> <reason>."}

def _id(w):
    try: return int(w[1])
    except Exception: raise svc.Err(422, "Please send the order number, e.g. ACCEPT 12")
def _st(v): return v["status"].replace("_", " ")
def _fmt(v):
    extra = f" {v['progress_pct']}% of work done" if v.get("progress_pct") and v["status"] == "in_production" else ""
    return f"#{v['id']} {v['order']['qty']} pcs Rs {v['order']['offered_price']} [{_st(v)}]" + (f" quote Rs {v['quote']}" if v.get("quote") else "") + extra
def _mine(u, oid): return next((x for x in svc.my_orders(u) if x["id"] == oid), None)

def _buyer(u, t, w, media):
    c = w[0] if w else ""
    if c == "status": return "\n".join(_fmt(v) for v in svc.my_orders(u)) or "No orders yet."
    if c == "accept": v = svc.accept_quote(u, _id(w)); return f"Order #{v['id']} confirmed at Rs {v['quote']}. The group will start soon."
    if c == "received":
        if len(w) < 3 or not w[2].isdigit(): raise svc.Err(422, "Send RECEIVED <order> <pieces that arrived>, e.g. RECEIVED 12 190")
        v = svc.signoff(u, _id(w), w[2], " ".join(t.split()[3:]), "final" in w[3:]); d = v["delivery"]
        return f"Thank you. Order #{v['id']}: {d['delivered']} of {d['ordered']} pieces received. Status: {_st(v)}."
    if c == "cancel": v = svc.cancel(u, _id(w), " ".join(t.split()[2:])); return f"Order #{v['id']} cancelled."
    if c == "group": PREF[u["id"]] = _id(w); return "Group selected."
    if (len(w) == 1 and c in ("help", "hi", "hello", "namaste")) or not t: return HELP["buyer"]
    g = PREF.get(u["id"]) or svc.groups()[0]["id"]; v = svc.submit_order(u, g, t)
    return f"Order #{v['id']} sent to {v['group']}. You will get a quote here." + (f"\nPlease clarify: {'; '.join(v['unclear'])}" if v["unclear"] else "")

def _checklist(u, w, t):
    """CHECKLIST <order> shows the buyer-specification checklist. Leader only: CHECKLIST <order> SET item 1; item 2 replaces it."""
    oid = _id(w); rest = t.split(None, 2)[2] if len(t.split(None, 2)) > 2 else ""
    if rest.lower().startswith("set ") and u["role"] == "leader": svc.set_checklist(u, oid, rest[4:].split(";"))
    return f"Quality checklist for order #{oid}:\n" + "\n".join(f"{i}. {c['text']}" for i, c in enumerate(svc.get_checklist(u, oid), 1)) + ("\n(Leader: CHECKLIST %d SET first point; second point  replaces this list.)" % oid if u["role"] == "leader" else "")

def _leader(u, t, w, media):
    c = w[0] if w else ""
    if c == "accept": v = svc.accept_quote(u, _id(w)); return f"Recorded: the buyer accepted order #{v['id']} at Rs {v['quote']}. Send PLAN {v['id']}."
    if c == "orders": return "\n".join(_fmt(v) for v in svc.my_orders(u)[:8]) or "No orders."
    if c == "checklist": return _checklist(u, w, t)
    if c == "quote": v = svc.quote(u, _id(w), w[2] if len(w) > 2 else None); return f"Quote Rs {v['quote']} sent. Floor price is Rs {v['price']['per_unit']['floor']}. At the buyer's offer the women earn Rs {v['price']['effective_hourly_at_offer']}/hr."
    if c == "plan":
        v = svc.plan(u, _id(w)); a = v["plan"]
        return "\n".join(f"{x['name']}: {x['stage']} x{x['units']}" for x in a["assignments"]) + ("\nRISK: " + "; ".join(a["issues"]) if a["issues"] else "") + f"\nSend START {v['id']} to approve."
    if c == "start": svc.start(u, _id(w)); return "Production started. Members have been told."
    if c == "pass":
        if len(w) < 3: raise svc.Err(422, "Send PASS <order> <name>, e.g. PASS 1 lakshmi")
        svc.review_sample(u, _id(w), w[2], "pass"); return f"{w[2].title()}'s sample passed. She has been told."
    if c == "rework":
        if len(w) < 4: raise svc.Err(422, "Send REWORK <order> <name> <pieces> <reason>, e.g. REWORK 1 lakshmi 4 stitching loose")
        v = svc.review_sample(u, _id(w), w[2], "rework", w[3], " ".join(t.split()[4:])); return f"Noted: {w[3]} of {w[2].title()}'s pieces need rework. They are paid once they pass. The payout proposal will use this number."
    if c == "risk":
        oid = _id(w); r = svc.risk(u, oid, w[2] if len(w) > 2 else None)
        head = f"Order #{oid}: {r['done']} of {r['needed']} pieces finished through every stage, {r['days_left']} day(s) left. Slowest stage: {r['slowest_stage']} ({r['per_day']} pieces/day). Projected {r['projected']}."
        return head + ("\nOn time." if not r["shortfall"] else f"\nShort by {r['shortfall']} pieces. Options (you and the group decide):\n- " + "\n- ".join(r["options"]))
    if c == "progress":
        o = _mine(u, _id(w))
        if not o: raise svc.Err(404, "Order not found")
        return "\n".join(f"{r['name']}: {r['stage']} {r['done']}/{r['units']}" for r in o["progress"]["rows"]) or "No plan yet."
    if c == "payout":
        oid, res = _id(w), {}
        o = _mine(u, oid)
        for name, n in zip(w[2::2], w[3::2]):                      # PAYOUT 1 lakshmi 4  -> 4 of Lakshmi's pieces need rework (overrides the sample review)
            for a in ((o or {}).get("plan") or {}).get("assignments", []):
                if a["name"].lower() == name and n.isdigit(): res[f"{a['member']}:{a['stage']}"] = {"accepted": max(a["units"] - int(n), 0), "pending": int(n)}
        v = svc.propose_payout(u, oid, res); return f"Payout proposed, total Rs {v['payout']['total']}. Members will review. Send APPROVE {v['id']}."
    if c == "approve": v = svc.approve_payout(u, _id(w)); return f"Order status: {_st(v)}."
    if c == "ready":
        if len(w) < 3 or not w[2].isdigit(): raise svc.Err(422, "Send READY <order> <pieces packed>, e.g. READY 12 200")
        v = svc.mark_ready(u, _id(w), w[2]); q = v["delivery"]["qc"]
        return f"Count recorded: {q['counted']} of {q['owed']} owed pieces." + ("" if q["match"] else " This will be a partial delivery.") + f" Send DISPATCH {v['id']} <courier> [tracking]."
    if c == "dispatch":
        if len(w) < 3: raise svc.Err(422, "Send DISPATCH <order> <courier> [tracking], e.g. DISPATCH 12 bus AB123")
        v = svc.dispatch(u, _id(w), w[2], w[3] if len(w) > 3 else ""); return f"Dispatched. The buyer has been told. Status: {_st(v)}."
    if c == "block": v = svc.block(u, _id(w), " ".join(t.split()[2:])); return f"Order #{v['id']} is blocked. The buyer has been told."
    if c == "unblock": v = svc.unblock(u, _id(w)); return f"Order #{v['id']} is back to: {_st(v)}."
    if c == "cancel": v = svc.cancel(u, _id(w), " ".join(t.split()[2:])); return f"Order #{v['id']} cancelled. The buyer has been told."
    if c == "resolve":
        oid = _id(w); o = _mine(u, oid)
        i = next((k for k, d in enumerate((o or {}).get("disputes", [])) if d["open"]), None)
        if i is None: return "No open dispute on that order."
        v = svc.resolve_dispute(u, oid, i, " ".join(t.split()[2:]) or "resolved by leader"); return f"Dispute resolved. Order status: {_st(v)}."
    return HELP["leader"]

def _seller(u, t, w, media):
    c, L = (w[0] if w else ""), u["lang"]
    if media and media.get("kind") == "image":                         # a photo = a first sample for the order she is working on
        cur = next((x for x in svc.tasks(u) if x["status"] == "in_production"), None)
        if not cur: raise svc.Err(409, "You have no task in production right now")
        svc.submit_sample(u, cur["order_id"], t, media.get("photo")); return i18n.t("sample_ok", L, o=cur["order_id"], photo=" (photo)")
    if c == "tasks": return "\n".join(i18n.t("task_line", L, o=x["order_id"], stage=i18n.stage(x["stage"], L), n=x["units"], pay=x["expected_pay"], done=x["done"], st=x["status"].replace("_", " ")) for x in svc.tasks(u)) or i18n.t("no_tasks", L)
    if c in ("earnings", "kitna", "kamaya", "paise") or (c not in ("dispute", "approve", "accept", "decline", "tasks", "done", "sample") and any(x in w for x in ("kitna", "kamaya", "earnings", "paise", "earned", "कमाई", "कमाया"))):
        return "\n".join(f"Order #{e['order_id']}: {e['explain']}" for e in svc.earnings(u)) or i18n.t("no_earn", L)
    if c == "done":
        nums = [x for x in w[1:] if x.isdigit()]
        if not nums: raise svc.Err(422, "Send DONE <order> <pieces>, e.g. DONE 1 20")
        oid, n = (int(nums[0]), nums[1]) if len(nums) > 1 else (next((x["order_id"] for x in svc.tasks(u) if x["status"] == "in_production"), 0), nums[0])
        r = svc.report_done(u, oid, n); return i18n.t("done_ok", L, n=r["done"], tot=r["units"], stage=i18n.stage(r["stage"], L), o=r["order_id"])
    if c == "sample":
        oid = int(w[1]) if len(w) > 1 and w[1].isdigit() else next((x["order_id"] for x in svc.tasks(u) if x["status"] == "in_production"), 0)
        svc.submit_sample(u, oid, " ".join(t.split()[2:]) if len(w) > 1 and w[1].isdigit() else " ".join(t.split()[1:])); return i18n.t("sample_ok", L, o=oid, photo="")
    if c == "checklist": return _checklist(u, w, t)
    if c == "accept": r = svc.accept_task(u, _id(w)); return i18n.t("task_accepted", L, o=_id(w))
    if c == "decline": svc.decline(u, _id(w)); return i18n.t("declined", L)
    if c == "approve": v = svc.approve_payout(u, _id(w)); return i18n.t("thanks", L, st=_st(v))
    if c == "dispute": svc.dispute(u, _id(w), " ".join(w[2:]) or "no reason given"); return i18n.t("disputed", L)
    return i18n.t("help_seller", L)

def _onboard(phone, t, msg_id):
    """MaaSakhi-style registration over WhatsApp: language -> name -> role -> skill. Only when OPEN_REGISTRATION=1."""
    st = ONB.get(phone); w = t.strip().lower()
    if not st: ONB[phone] = {"step": "lang"}; return i18n.t("menu", "en")
    L = st.get("lang", "en")
    if st["step"] == "lang":
        if w not in ("1", "2", "3"): return i18n.t("menu", "en")
        st.update(lang=i18n.LANGS[w], step="name"); return i18n.t("ask_name", st["lang"])
    if st["step"] == "name":
        st.update(name=t.strip()[:40] or "Guest", step="role"); return i18n.t("ask_role", L)
    if st["step"] == "role":
        if w not in ("1", "2"): return i18n.t("ask_role", L)
        if w == "2": ONB.pop(phone); svc.register_self(phone, st["name"], "buyer", lang=L); return i18n.t("joined", L, name=st["name"]) + "\n" + HELP["buyer"]
        st["step"] = "skill"; return i18n.t("ask_skill", L)
    if st["step"] == "skill":
        if w not in SKILLS: return i18n.t("ask_skill", L)
        ONB.pop(phone); svc.register_self(phone, st["name"], "seller", SKILLS[w], L); return i18n.t("joined", L, name=st["name"]) + "\n" + i18n.t("help_seller", L)

def handle(sender, text, msg_id=None, media=None):
    if msg_id:
        if msg_id in SEEN: return None                       # WhatsApp may redeliver: act once
        SEEN.add(msg_id)
    with db.S() as s:
        x = s.scalars(select(db.User).where(db.User.phone == auth.norm(sender), db.User.active == True)).first()  # noqa: E712
        u = {"id": x.id, "name": x.name, "role": x.role, "group_id": x.group_id, "lang": x.lang or "en"} if x else None
    t = (text or "").strip()
    if not u:
        if svc.open_registration():
            try: return _onboard(auth.norm(sender), t, msg_id)
            except svc.Err as e: return f"Not done: {e.msg}"
        return "This number is not registered. Please ask your group leader or mission coordinator to add you."
    w = t.lower().split()
    if w and w[0] == "role" and bootstrap.owner_phone() == auth.norm(sender):         # owner only: act as each role from one phone
        try: r = svc.switch_owner_role(u, w[1] if len(w) > 1 else ""); return f"You are now: {r}. Send HELP to see what you can do."
        except svc.Err as e: return f"Not done: {e.msg}"
    if w and w[0] in ("lang", "language", "bhasha", "भाषा") or (len(w) == 1 and w[0] in ("hindi", "english", "hinglish")):
        code = i18n.LANGS.get(w[-1]) if len(w) > 0 and w[-1] in i18n.LANGS else None
        if not code: return i18n.t("menu", u["lang"])
        svc.set_lang(u, code); return i18n.t("lang_set", code)
    try:
        r = convo.handle(u, auth.norm(sender), t, w, media)                                   # guided conversation first; typed commands remain the fallback
        if r is not None: return r
    except svc.Err as e:
        return f"Not done: {e.msg}"
    except Exception:
        convo._clear(auth.norm(sender)); return "Sorry, something went wrong on my side. Send MENU to start again."
    try:
        out = {"buyer": _buyer, "leader": _leader, "seller": _seller}.get(u["role"], lambda *_: "Please use the web dashboard.")(u, t, w, media)
        return out + ("\n\nSend MENU for the guided menu." if w == ["help"] else "")
    except svc.Err as e:
        return f"Not done: {e.msg}"
