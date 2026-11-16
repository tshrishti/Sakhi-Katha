"""WhatsApp commands for buyers, leaders and members. Same service layer and permissions as the dashboards.
Like MaaSakhi: short messages, the number identifies the user, language is chosen on first contact, photos and voice notes work.
Unknown numbers get nothing unless OPEN_REGISTRATION=1 (then they register themselves like in MaaSakhi); otherwise the leader adds members."""
import logging, time
from sqlalchemy import select
import auth, bootstrap, convo, db, i18n, svc

SEEN, PREF, ONB = set(), {}, {}
SKILLS = {"1": "cutting", "2": "stitching", "3": "finishing", "4": "packing"}
log = logging.getLogger("sakhi.bot")

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
    if c == "rate":
        if len(w) < 3: raise svc.Err(422, "Send RATE <order> <1-5> [comment]")
        result = svc.rate_buyer_group(u, _id(w), w[2], " ".join(t.split()[3:]))
        return f"Thank you for rating the group {result['stars']}/5."
    if c == "group":
        if len(w) < 2: return convo.change_group(u, auth.norm(u["phone"]))
        svc.set_preferred_group(u, _id(w)); return "Group selected."
    if (len(w) == 1 and c in ("help", "hi", "hello", "namaste")) or not t: return convo.role_menu(u)
    groups = svc.groups()
    group_id = u.get("preferred_group_id") or svc.preferred_group(u)
    if not group_id and len(groups) == 1: group_id = groups[0]["id"]
    if not group_id:
        return "Choose a group first. Send CHANGE GROUP to see the available groups."
    v = svc.submit_order(u, group_id, t)
    return f"Order #{v['id']} sent to {v['group']}. You will get a quote here." + (f"\nPlease clarify: {'; '.join(v['unclear'])}" if v["unclear"] else "")

def _checklist(u, w, t):
    """CHECKLIST <order> shows the buyer-specification checklist. Leader only: CHECKLIST <order> SET item 1; item 2 replaces it."""
    oid = _id(w); rest = t.split(None, 2)[2] if len(t.split(None, 2)) > 2 else ""
    if rest.lower().startswith("set ") and u["role"] == "leader": svc.set_checklist(u, oid, rest[4:].split(";"))
    return f"Quality checklist for order #{oid}:\n" + "\n".join(f"{i}. {c['text']}" for i, c in enumerate(svc.get_checklist(u, oid), 1)) + ("\n(Leader: CHECKLIST %d SET first point; second point  replaces this list.)" % oid if u["role"] == "leader" else "")

def _leader(u, t, w, media):
    c = w[0] if w else ""
    if c == "join":
        if len(w) != 3 or not w[1].isdigit() or w[2] not in ("1", "2"):
            raise svc.Err(422, "Send JOIN <request number> 1 to approve or 2 to reject")
        result = svc.resolve_join_request(u, int(w[1]), w[2] == "1")
        key = "join_request_approved" if result["status"] == "approved" else "join_request_rejected"
        return i18n.t(key, u["lang"], order=w[1])
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
    return convo.role_menu(u)

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
    if c == "notices":
        result = svc.member_notices(u)
        lines = [f"#{x['id']} {'Seen' if x['seen'] else 'New'}: {x['message']}" for x in result["messages"][:8]]
        lines.extend(f"Leave #{x['id']}: {x['status']} ({', '.join(x['days'])})" for x in result["leave_requests"][:4])
        return "\n".join(lines) or "No notices yet."
    if c == "seen":
        if len(w) < 2 or not w[1].isdigit(): raise svc.Err(422, "Send SEEN <message number>")
        svc.member_ack_notice(u, int(w[1])); return "Message marked as seen."
    if c == "leave":
        if len(w) < 2: raise svc.Err(422, "Send LEAVE yyyy-mm-dd [yyyy-mm-dd] [reason]")
        days = [word.strip(",") for word in w[1:] if len(word.strip(",")) == 10 and word.strip(",")[4:5] == "-"]
        day_set = set(days)
        reason = " ".join(word for word in w[1:] if word.strip(",") not in day_set)
        result = svc.member_request_leave(u, days, reason)
        return f"Leave request #{result['id']} sent to your leader."
    return convo.role_menu(u)

def _onboard_state(phone):
    with db.S() as s:
        state = s.get(db.ChatState, phone)
        if not state or not (state.step or "").startswith("onboard_"):
            return None
        if time.time() - (state.updated_at or 0) > convo.TTL:
            lang = state.lang or "en"
            state.step, state.data, state.updated_at = "onboard_lang", {}, time.time()
            s.commit()
            return {"step": "onboard_lang", "data": {}, "expired": True, "lang": lang}
        return {"step": state.step, "data": dict(state.data or {}), "expired": False,
                "lang": state.lang or "en"}

def _onboard_save(phone, step, data=None, lang="en"):
    with db.S() as s:
        state = s.get(db.ChatState, phone) or db.ChatState(
            phone=phone, step=step, data=data or {}, lang=lang, updated_at=time.time()
        )
        state.step, state.data, state.lang, state.updated_at = step, data or {}, lang, time.time()
        s.merge(state)
        s.commit()

def _onboard_clear(phone):
    with db.S() as s:
        state = s.get(db.ChatState, phone)
        if state:
            state.step, state.data, state.updated_at = None, {}, time.time()
            s.commit()

def _onboard(phone, t, msg_id):
    """Persist registration progress in ChatState; role and consent are explicit."""
    state = _onboard_state(phone)
    if not state:
        _onboard_save(phone, "onboard_lang")
        return i18n.t("welcome_language", "en")
    data, step, lang = state["data"], state["step"], state["lang"]
    text = (t or "").strip()
    word = " ".join(text.lower().split())
    if state["expired"]:
        return i18n.t("session_expired", lang) + "\n" + i18n.t("welcome_language", lang)
    if word in ("english", "hindi", "hinglish"):
        lang = i18n.LANGS[word]
        if step == "onboard_lang":
            _onboard_save(phone, "onboard_name", {"lang": lang}, lang)
            return i18n.t("ask_name", lang)
        _onboard_save(phone, step, data, lang)
        prompt = {
            "onboard_lang": "welcome_language",
            "onboard_name": "ask_name",
            "onboard_role": "ask_role",
            "onboard_consent": "consent_buyer" if data.get("role") == "buyer" else "consent_member",
            "onboard_skill": "ask_skill",
            "onboard_group_code": "ask_group_code",
        }[step]
        return i18n.t(prompt, lang)
    if word in ("cancel", "stop", "exit"):
        _onboard_clear(phone)
        return i18n.t("cancelled", lang)
    if word in ("9", "menu", "help"):
        _onboard_save(phone, "onboard_lang", {}, lang)
        return i18n.t("welcome_language", lang)
    if word in ("0", "back"):
        previous = {
            "onboard_name": "onboard_lang",
            "onboard_role": "onboard_name",
            "onboard_consent": "onboard_role",
            "onboard_skill": "onboard_consent",
            "onboard_group_code": "onboard_skill",
        }.get(step, "onboard_lang")
        keep = {
            "onboard_lang": set(), "onboard_name": {"lang"},
            "onboard_role": {"lang", "name"}, "onboard_consent": {"lang", "name", "role"},
            "onboard_skill": {"lang", "name", "role", "consent"},
            "onboard_group_code": {"lang", "name", "role", "consent", "skill"},
        }[previous]
        data = {k: v for k, v in data.items() if k in keep}
        _onboard_save(phone, previous, data, lang)
        prompt = {
            "onboard_lang": i18n.t("welcome_language", lang),
            "onboard_name": i18n.t("ask_name", lang),
            "onboard_role": i18n.t("ask_role", lang),
            "onboard_consent": i18n.t("consent_buyer" if data.get("role") == "buyer" else "consent_member", lang),
            "onboard_skill": i18n.t("ask_skill", lang),
        }[previous]
        return prompt
    if step == "onboard_lang":
        code = i18n.LANGS.get(word)
        if not code:
            return i18n.t("invalid_choice", "en") + "\n" + i18n.t("welcome_language", "en")
        _onboard_save(phone, "onboard_name", {"lang": code}, code)
        return i18n.t("ask_name", code)
    if step == "onboard_name":
        if not text:
            return i18n.t("invalid_choice", lang) + "\n" + i18n.t("ask_name", lang)
        data["name"] = text[:40]
        _onboard_save(phone, "onboard_role", data, lang)
        return i18n.t("ask_role", lang)
    if step == "onboard_role":
        if word not in ("1", "2"):
            return i18n.t("invalid_choice", lang) + "\n" + i18n.t("ask_role", lang)
        data["role"] = "seller" if word == "1" else "buyer"
        _onboard_save(phone, "onboard_consent", data, lang)
        return i18n.t("consent_member" if word == "1" else "consent_buyer", lang)
    if step == "onboard_consent":
        if word in ("2", "no", "nahi", "nahin", "नहीं", "नही", "ಇಲ್ಲ"):
            _onboard_clear(phone)
            return i18n.t("declined_consent", lang)
        if word not in ("1", "yes", "haan", "ha", "हाँ", "हां", "ಹೌದು"):
            key = "consent_buyer" if data.get("role") == "buyer" else "consent_member"
            return i18n.t("invalid_choice", lang) + "\n" + i18n.t(key, lang)
        data["consent"] = True
        if data.get("role") == "buyer":
            try:
                svc.register_self(phone, data["name"], "buyer", lang=lang)
            except svc.Err as e:
                _onboard_clear(phone)
                return i18n.t("already_registered", lang) if e.code == 409 else i18n.t("unregistered", lang)
            _onboard_clear(phone)
            convo._save(phone, "menu", {})
            return i18n.t("registered_buyer", lang, name=data["name"])
        _onboard_save(phone, "onboard_skill", data, lang)
        return i18n.t("ask_skill", lang)
    if step == "onboard_skill":
        if word not in SKILLS:
            return i18n.t("invalid_choice", lang) + "\n" + i18n.t("ask_skill", lang)
        data["skill"] = SKILLS[word]
        _onboard_save(phone, "onboard_group_code", data, lang)
        return i18n.t("ask_group_code", lang)
    if step == "onboard_group_code":
        if not text:
            return i18n.t("invalid_choice", lang) + "\n" + i18n.t("ask_group_code", lang)
        try:
            svc.submit_join_request(phone, data["name"], data["skill"], lang, text, consent=True)
        except svc.Err as e:
            if e.code == 409:
                _onboard_clear(phone)
                return i18n.t("already_registered", lang)
            return i18n.t("group_code_invalid", lang)
        _onboard_clear(phone)
        return i18n.t("join_waiting", lang)

def handle(sender, text, msg_id=None, media=None):
    if msg_id:
        if msg_id in SEEN: return None                       # WhatsApp may redeliver: act once
        SEEN.add(msg_id)
    phone = auth.norm(sender)
    with db.S() as s:
        x = s.scalars(select(db.User).where(db.User.phone == phone, db.User.active == True)).first()  # noqa: E712
        if x:
            x.last_active_at = time.time()
            s.commit()
        u = {"id": x.id, "name": x.name, "role": x.role, "group_id": x.group_id, "preferred_group_id": x.preferred_group_id,
             "phone": x.phone, "lang": x.lang or "en"} if x else None
    t = (text or "").strip()
    if not u:
        if svc.pending_join_request(phone):
            with db.S() as s:
                pending = s.scalars(select(db.JoinRequest).where(
                    db.JoinRequest.phone == phone, db.JoinRequest.status == "pending"
                )).first()
            return i18n.t("join_waiting", pending.lang if pending else "en")
        if svc.open_registration():
            try: return _onboard(phone, t, msg_id)
            except svc.Err:
                return i18n.t("unregistered", "en")
        return i18n.t("unregistered", "en")
    w = t.lower().split()
    if u["role"] == "leader" and w:
        with db.S() as s:
            state = s.get(db.ChatState, phone)
            pending = dict(state.pending_action or {}) if state else {}
        ids = pending.get("ids", []) if pending.get("type") == "join_request" else []
        if ids and w[0] in ("1", "2", "yes", "no", "haan", "हाँ", "हां", "ಹೌದು", "ಸರಿ", "ಇಲ್ಲ"):
            approved = w[0] in ("1", "yes", "haan", "हाँ", "हां", "ಹೌದು", "ಸರಿ")
            result = svc.resolve_join_request(u, ids[0], approved)
            key = "join_request_approved" if result["status"] == "approved" else "join_request_rejected"
            return i18n.t(key, u["lang"], order=ids[0])
    if w and w[0] == "role" and bootstrap.owner_phone() == auth.norm(sender):         # owner only: act as each role from one phone
        try:
            r = svc.switch_owner_role(u, w[1] if len(w) > 1 else "")
            return i18n.t("owner_role_changed", u["lang"], role=r)
        except svc.Err as e: return f"Not done: {e.msg}"
    if w and w[0] in ("lang", "language", "bhasha", "भाषा") or (len(w) == 1 and w[0] in ("hindi", "english", "hinglish")):
        code = i18n.LANGS.get(w[-1]) if len(w) > 0 and w[-1] in i18n.LANGS else None
        if not code: return i18n.t("menu", u["lang"])
        svc.set_lang(u, code); return i18n.t("lang_set", code)
    if w and w[0] in ("web", "link", "login"):
        link = svc.issue_magic_link(phone)
        return (f"Your secure sign-in link (valid for {max(1, link['expires_in'] // 60)} minutes):\n{link['url']}"
                if link.get("url") else "If this number is registered, a sign-in link will be sent.")
    if u["role"] == "leader" and w and w[0] == "message":
        if len(w) < 3 or w[1] != "all": return "Send MESSAGE ALL <message>."
        result = svc.leader_broadcast(u, " ".join(t.split()[2:]))
        return f"Message sent to {result['sent']} member(s)."
    if u["role"] == "leader" and w and w[0] == "leaves":
        rows = svc.leader_leave_requests(u)
        return "\n".join(f"#{x['id']} {x['member']}: {', '.join(x['days'])} [{x['status']}]" for x in rows[:8]) or "No leave requests."
    if u["role"] == "leader" and w and w[0] == "leaveapprove":
        if len(w) < 2 or not w[1].isdigit(): raise svc.Err(422, "Send LEAVEAPPROVE <request> [note]")
        result = svc.leader_resolve_leave(u, int(w[1]), "approved", " ".join(t.split()[2:]))
        return f"Leave request #{result['id']} approved."
    try:
        r = convo.handle(u, phone, t, w, media)                                   # guided conversation first; typed commands remain the fallback
        if r is not None: return r
    except svc.Err as e:
        return f"Not done: {e.msg}"
    except Exception:
        log.exception("guided chatbot step failed")
        convo._clear(phone); return i18n.t("try_again", u["lang"])
    try:
        out = {"buyer": _buyer, "leader": _leader, "seller": _seller}.get(u["role"], lambda *_: "Please use the web dashboard.")(u, t, w, media)
        return out
    except svc.Err as e:
        return f"Not done: {e.msg}"
