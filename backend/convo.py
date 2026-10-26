"""Guided WhatsApp conversation: welcome menu -> buyer name -> order (text / voice / photo / PDF) -> missing details -> confirm -> pricing and
leader review -> member tasks -> earnings. It remembers the step per phone number (db.Convo). It only calls the same service layer as the
dashboards and the typed commands, so permissions and the ledger are unchanged. handle() returns None when the old command handler should answer.
The AI recommends and explains; the leader and the group decide. Nothing here sets anyone's pay."""
import datetime, re, time
from decimal import Decimal as D
from sqlalchemy import select
import db, engine as E, i18n, llm, svc

TTL = 2 * 3600                                           # a half-finished conversation is forgotten after 2 hours of silence
GREET = {"hi", "hii", "hiii", "hello", "hey", "hlo", "namaste", "namaskar", "menu", "start", "नमस्ते", "हेलो", "हाय", "मेनू"}
STOP = {"cancel", "back", "exit", "stop", "reset"}
YES, EDIT, NO = {"1", "confirm", "confirm order", "yes", "y", "ok", "okay", "haan", "ha", "हाँ", "हां"}, {"2", "edit", "edit details", "change", "badlo"}, {"3", "cancel", "cancel order", "no", "nahi", "nahin"}
CMDS = {"buyer": {"status", "accept", "group", "help"},
        "leader": {"orders", "quote", "plan", "start", "pass", "rework", "risk", "progress", "payout", "approve", "resolve", "accept", "help"},
        "seller": {"tasks", "earnings", "done", "sample", "decline", "approve", "dispute", "help", "lang", "language"}}
EARN_WORDS = ("kitna", "kamaya", "earnings", "paise", "earned", "कमाई", "कमाया")
MENUS = {"buyer": [("new", "Place a new order"), ("check", "Check an existing order")],
         "leader": [("new", "Place a new order for a buyer"), ("check", "Check an existing order"), ("review", "Review orders waiting for me")],
         "seller": [("work", "Manage my assigned work"), ("earn", "Check my earnings")]}
WORDS = [("check", ("check", "status", "existing")), ("earn", ("earn", "kitna", "kamai", "paise", "pay")), ("work", ("work", "task", "kaam", "काम")),
         ("review", ("review", "manage", "pending")), ("new", ("order", "new", "place", "naya", "नया"))]
STATUS = {"submitted": "waiting for the leader's quote", "quoted": "quote sent, waiting for the buyer", "accepted": "accepted, needs a work plan",
          "planned": "work plan ready for approval", "in_production": "in production", "payout_pending": "payout waiting for approvals", "settled": "settled"}
REQUIRED = [("product", "What product is it? (for example, cloth bags)", None), ("qty", "How many pieces do you need?", "{n} pieces"),
            ("offered_price", "What price per piece are you offering? (in ₹)", "Rs {n} per piece"), ("deadline_days", "By when do you need delivery? (a date, or a number of days)", "{n} days")]

# ---------- state ----------
def _load(phone):
    with db.S() as s:
        c = s.get(db.Convo, phone)
        if not c or not c.step or time.time() - (c.ts or 0) > TTL: return None
        return {"step": c.step, "data": dict(c.data or {})}
def _save(phone, step, data):
    with db.S() as s:
        c = s.get(db.Convo, phone) or db.Convo(phone=phone); c.step, c.data, c.ts = step, data, time.time(); s.merge(c); s.commit()
def _clear(phone):
    with db.S() as s:
        c = s.get(db.Convo, phone)
        if c: c.step, c.data = None, {}; s.commit()
def _go(phone, step, data, text): _save(phone, step, data); return text

# ---------- small helpers ----------
def _num(t):
    t = re.sub(r"(?i)rs\.?|₹|rupees?|,|\s", "", t or ""); return int(t) if t.isdigit() else None
def _date(iso):
    try: return datetime.date.fromisoformat(iso).strftime("%d %b %Y")
    except Exception: return None
def _name(v):
    o = v["order"]; return (o.get("product") or "Order").strip().capitalize()
def _line(v): return f"#{v['id']} {_name(v)} × {v['order']['qty']} - {STATUS.get(v['status'], v['status'])}"
def _menu_text(role): return "\n".join(f"{i}. {lab}" for i, (_, lab) in enumerate(MENUS[role], 1))
def _welcome(role): return ("Namaste! 🙏 Welcome to Sakhi Khata.\n\nI help you manage orders, coordinate work among SHG members, check product quality, and track everyone's earnings.\n\n"
                            f"What would you like to do today?\n\n{_menu_text(role)}\n\nYou can reply by typing or sending a voice note. Send MENU any time to come back here.")
def _choose(options, t):
    t = (t or "").strip().lower()
    if t.isdigit(): i = int(t) - 1; return options[i] if 0 <= i < len(options) else None
    for key, syn in WORDS:
        if key in options and any(x in t for x in syn): return key
    return None
def _is_command(role, w):
    if not w: return False
    return w[0] in CMDS.get(role, ()) or (role == "seller" and any(x in w for x in EARN_WORDS))
def _looks_like_order(t):
    return E.parse_order(t or "")["order"]["qty"] is not None or (any(c.isdigit() for c in (t or "")) and len((t or "").split()) >= 5)
def _opts(pairs): return "\n".join(f"{i}. {lab}" for i, (_, lab) in enumerate(pairs, 1))
def _pick_order(rows, t):
    t = (t or "").strip().lstrip("#"); return next((x for x in rows if str(x["id"]) == t or str(x.get("order_id")) == t), None)

# ---------- reading what the user sent: text, voice transcript, photo, PDF ----------
def _ingest(t, media):
    """-> (text, evidence, error). Never pretends a photo, PDF or voice note was understood when it was not."""
    kind = (media or {}).get("kind")
    if kind == "image":
        txt = llm.read_image(media.get("photo_hi") or media.get("photo"))
        if not txt: return None, None, "I could not read that photo clearly. Please send a clearer, well-lit photo, or send a voice note or type the order."
        return (txt + ("\n" + t if t else "")), {"source": "photo", "extracted_text": txt, "photo": media.get("photo"), "media_url": media.get("url")}, None
    if kind == "pdf":
        if not media.get("text"): return None, None, "I could not read any text in that PDF. Please send a photo of the order, a voice note, or type it."
        return (media["text"] + ("\n" + t if t else "")), {"source": "pdf", "extracted_text": media["text"][:3000], "media_url": media.get("url")}, None
    if kind == "voice": return t, {"source": "voice", "transcript": t, "media_url": media.get("url")}, None
    return t, {"source": "text", "text": t}, None

# ---------- new order ----------
def _ask_missing(phone, d, missing):
    d["missing"] = [k for k, _, _ in missing]; qs = [q for _, q, _ in missing]
    body = qs[0] if len(qs) == 1 else "\n".join(f"{i}. {q}" for i, q in enumerate(qs, 1)) + "\n\nYou can answer everything in one message."
    return _go(phone, "missing", d, ("I still need one detail:\n" if len(qs) == 1 else "Thank you! I still need a few details:\n") + body)

def _summary(d):
    o = d["order"]; rows = [("Buyer", d.get("buyer_name")), ("Product", o.get("product")), ("Quantity", f"{o['qty']} pieces" if o.get("qty") else None), ("Size", o.get("size")),
        ("Colour", o.get("colour")), ("Specifications", o.get("specs")), ("Packaging", o.get("packaging")),
        ("Delivery date", (_date(o.get("delivery_date")) or "") + (f" (in {o['deadline_days']} days)" if o.get("deadline_days") else "") or None),
        ("Delivery location", o.get("location")), ("Offered price", f"₹{o['offered_price']} per piece" if o.get("offered_price") else None)]
    txt = "Here is the order I understood:\n\n" + "\n".join(f"{k}: {v}" for k, v in rows if v)
    ev = d.get("evidence") or {}
    if ev.get("source") in ("voice", "photo", "pdf"):
        said = ev.get("transcript") or ev.get("extracted_text") or ""
        txt += f"\n\n({ {'voice': 'What I heard', 'photo': 'What I read in the photo', 'pdf': 'What I read in the PDF'}[ev['source']] }: “{said[:260]}{'…' if len(said) > 260 else ''}”)"
    if d.get("unclear"): txt += "\n\nNot clear, please check: " + "; ".join(d["unclear"][:4])
    return txt + "\n\nPlease confirm these details.\n\nReply with:\n1. Confirm order\n2. Edit details\n3. Cancel order"

def _process(phone, d):
    """Understand everything said so far; ask only for what is still missing; otherwise show the summary for confirmation."""
    ex = llm.extract_details(d["parts"]); d["order"], d["unclear"] = ex["order"], ex["unclear"]
    missing = [r for r in REQUIRED if not d["order"].get(r[0])]
    d["tries"] = d.get("tries", 0) + (1 if missing else 0)
    if missing and d["tries"] > 4: _clear(phone); return "I am still missing some details, so I have stopped this order. Send MENU to start again, or ask your group leader for help."
    if missing: return _ask_missing(phone, d, missing)
    return _go(phone, "confirm", d, _summary(d))

def _start_order(u, phone, t, media):
    d = {"parts": [], "evidence": None, "buyer_name": None}
    if t or media:
        txt, ev, err = _ingest(t, media)
        if err: _go(phone, "buyer_name", d, ""); return err + "\n\nFirst, please tell me the buyer's name or business name."
        d["parts"], d["evidence"] = [txt], ev
    return _go(phone, "buyer_name", d, "Sure! Let's create a new order.\n\nFirst, please tell me the buyer's name or business name.")

def _step_buyer_name(u, phone, st, t, w, media):
    d = st["data"]; name = (t or "").strip()[:60]
    if not name: return "Please tell me the buyer's name or business name."
    d["buyer_name"] = name
    if d["parts"]: return _process(phone, d)
    return _go(phone, "details", d, f"Thank you! Now please share the order details. You can:\n\n- Send a voice note explaining the order.\n- Send a photo of a handwritten order.\n- Upload a PDF or order document.\n- Type the order details directly.")

def _step_details(u, phone, st, t, w, media):
    d = st["data"]; txt, ev, err = _ingest(t, media)
    if err: return err
    if not (txt or "").strip(): return "Please send the order details as a voice note, photo, PDF or text."
    d["parts"], d["evidence"] = [txt], ev
    return _process(phone, d)

def _step_missing(u, phone, st, t, w, media):
    d = st["data"]; txt, ev, err = _ingest(t, media)
    if err: return err
    miss = d.get("missing", []); n = _num(txt)
    if miss == ["product"] and txt: d["parts"] = d["parts"] + [f"Product: {txt.strip()[:60]}"]; return _process(phone, d)
    lab = next((x for x in REQUIRED if x[0] == miss[0]), None) if len(miss) == 1 else None
    d["parts"] = d["parts"] + [lab[2].format(n=n) if (lab and lab[2] and n) else f"(Answering: {'; '.join(x[1] for x in REQUIRED if x[0] in miss)}) {txt}"]
    return _process(phone, d)

def _step_edit(u, phone, st, t, w, media):
    d = st["data"]; txt, ev, err = _ingest(t, media)
    if err: return err
    d["parts"] = d["parts"] + [f"Correction from the buyer: {txt}"]; d["tries"] = 0
    return _process(phone, d)

def _step_confirm(u, phone, st, t, w, media):
    d, a = st["data"], (t or "").strip().lower()
    if a in YES: return _place(u, phone, d)
    if a in EDIT: return _go(phone, "edit", d, "No problem. What would you like to change? For example: quantity 250, or price 65, or delivery 20 November.")
    if a in NO or a in STOP: _clear(phone); return "Okay, the order is cancelled. Send MENU whenever you want to start again."
    return "Please reply with:\n1. Confirm order\n2. Edit details\n3. Cancel order"

def _money(x): return f"{D(str(x)):.2f}".rstrip("0").rstrip(".")
def _pricing_block(r, offered):
    p, off = r["price"], D(str(offered)); floor = D(p["per_unit"]["floor"]); lines = [f"Cost to make one piece (material, labour, overhead): ₹{_money(D(p['per_unit']['material']) + D(p['per_unit']['labour']) + D(p['per_unit']['overhead']))}",
                                                                                 f"Minimum sustainable price (with margin): ₹{_money(floor)} per piece", f"Offered price: ₹{_money(off)} per piece"]
    if off < floor: lines.append(f"⚠️ Shortfall: ₹{_money(floor - off)} per piece. At this price the women would earn about ₹{_money(D(p['effective_hourly_at_offer']))} an hour against a target of ₹{_money(D(p['target_hourly']))}.")
    else: lines.append("✅ The offer covers the minimum sustainable price.")
    lines.append("Delivery: " + ("✅ the group has the skills and capacity to deliver on time." if r["feasible"] else "⚠️ " + "; ".join(r["issues"])))
    return "\n".join(lines), off < floor, p["suggested_counter"]

def _place(u, phone, d):
    o = dict(d["order"]); o["buyer_name"] = d["buyer_name"]
    g = svc.groups()
    if not g: return "No SHG group is set up yet. Please ask the programme team."
    v = svc.submit_order(u, g[0]["id"], "\n".join(d["parts"]), pre={"order": o, "unclear": d.get("unclear", []), "evidence": d.get("evidence")})
    if u["role"] != "leader":
        _clear(phone)
        return f"✅ Order #{v['id']} is confirmed and sent to {v['group']}.\n\nThe SHG leader will check the price and the work plan, and you will get a quote here. Send STATUS or MENU any time."
    r = svc.review(u, v["id"]); block, under, counter = _pricing_block(r, o["offered_price"])
    opts = ([("quote_auto", f"Send a counteroffer of ₹{counter} per piece")] if under else [("quote_auto", f"Accept the offered price (send quote ₹{o['offered_price']})")]) + [("quote_own", "Send a quote at my own price"), ("later", "Review it later")]
    return _go(phone, "after_submit", {"oid": v["id"], "opts": [k for k, _ in opts], "auto": str(counter if under else o["offered_price"])},
               f"✅ Order #{v['id']} for {d['buyer_name']} is recorded.\n\nHere is the price and delivery check (worked out by fixed calculations, not by AI):\n\n{block}\n\nThe group decides. What would you like to do?\n{_opts(opts)}")

def _step_after_submit(u, phone, st, t, w, media):
    d = st["data"]; k = _choose(d["opts"], t) if (t or "").strip().isdigit() else None
    return _leader_do(u, phone, d, k, t) if k else "Please reply with a number from the list, or send MENU."

# ---------- leader: price actions on an order ----------
def _leader_do(u, phone, d, k, t):
    oid = d["oid"]
    if k == "later": _clear(phone); return f"Okay. Order #{oid} is on your dashboard. Send MENU to review it later."
    if k == "quote_own": return _go(phone, "quote_price", d, "What price per piece (in ₹) should I quote?")
    return _quote(u, phone, oid, d["auto"] if k == "quote_auto" else None)

def _quote(u, phone, oid, price):
    v = svc.quote(u, oid, price); _clear(phone)
    tail = f"\n\nWhen the buyer agrees, send ACCEPT {oid}." if _on_behalf(oid) else "\n\nThe buyer has been sent this quote."
    return f"✅ Quote of ₹{v['quote']} per piece recorded for order #{oid}." + tail
def _on_behalf(oid):
    with db.S() as s: o = s.get(db.Order, oid); return bool(o and o.data.get("on_behalf"))
def _mine(u, oid): return next((x for x in svc.my_orders(u) if x["id"] == oid), None)

def _step_quote_price(u, phone, st, t, w, media):
    n = _num(t)
    if not n: return "Please send just the price per piece in rupees, for example 85."
    return _quote(u, phone, st["data"]["oid"], str(n))

# ---------- check an existing order ----------
def _card(u, v):
    o, st = v["order"], STATUS.get(v["status"], v["status"]); rows = [f"Order #{v['id']} - {_name(v)}", f"Status: {st}", f"Quantity: {o['qty']} pieces", f"Offered price: ₹{o['offered_price']} per piece"]
    if v.get("quote"): rows.append(f"Quote: ₹{v['quote']} per piece")
    when = _date(o.get("delivery_date")); rows.append("Delivery by: " + (when or f"{o['deadline_days']} days from the order date"))
    if u["role"] == "buyer": return "\n".join(rows + ([f"Work completed: {v['progress_pct']}%"] if v.get("progress_pct") else []))
    prog = v.get("progress") or {}; prs = prog.get("rows", [])
    if prs: rows.append(f"Work completed: {sum(r['done'] for r in prs)} of {sum(r['units'] for r in prs)} task pieces ({int(100 * sum(r['done'] for r in prs) / max(1, sum(r['units'] for r in prs)))}%)")
    pend = [r for r in prs if r["done"] < r["units"]]
    if pend: rows.append("Pending tasks: " + "; ".join(f"{r['name']} {r['stage']} {r['done']}/{r['units']}" for r in pend[:6]))
    ql = v.get("quality") or []
    if ql: rows.append(f"Quality reviews: {sum(x['status'] == 'pending' for x in ql)} waiting, {sum(x['status'] == 'passed' for x in ql)} passed, {sum(x['status'] == 'rework' for x in ql)} need rework")
    issues = [d for d in v.get("disputes", []) if d.get("open")]
    rows.append(f"Unresolved issues: {len(issues)} open pay dispute(s)" if issues else "Unresolved issues: none")
    if v.get("unclear"): rows.append("Unclear details: " + "; ".join(v["unclear"][:3]))
    return "\n".join(rows)

def _check(u, phone):
    rows = svc.my_orders(u)
    if not rows: _clear(phone); return "You have no orders yet. Send MENU to place one."
    if len(rows) == 1: _clear(phone); return _card(u, rows[0]) + "\n\nSend MENU for more options."
    return _go(phone, "check_pick", {}, "Which order would you like to check? Reply with the order number:\n\n" + "\n".join(_line(v) for v in rows[:10]))

def _step_check_pick(u, phone, st, t, w, media):
    v = _pick_order(svc.my_orders(u), t)
    if not v: return "I could not find that order number. Please reply with a number from the list, or send MENU."
    _clear(phone); return _card(u, v) + "\n\nSend MENU for more options."

# ---------- leader: review and manage ----------
def _review(u, phone):
    rows = svc.my_orders(u)
    if not rows: _clear(phone); return "There are no orders in your group yet. Send MENU to place one."
    return _go(phone, "review_pick", {}, "Orders in your group:\n\n" + "\n".join(_line(v) for v in rows[:10]) + "\n\nReply with an order number to open it, or send MENU.")

def _actions(v):
    s = v["status"]; back = ("back", "Back to the order list")
    if s == "submitted": return [("quote_auto", "Send a quote at the suggested price"), ("quote_own", "Send a quote at my own price"), back]
    if s == "quoted": return ([("accept_for_buyer", "Record that the buyer accepted")] if _on_behalf(v["id"]) else []) + [("quote_own", "Change the quote"), back]
    if s == "accepted": return [("plan", "Propose a work plan"), back]
    if s == "planned": return [("start", "Approve the plan and start production"), ("plan", "Propose a new plan"), back]
    if s == "in_production": return [("progress", "Show progress"), ("risk", "Check deadline risk"), ("payout", "Prepare the payout proposal"), back]
    if s == "payout_pending": return [("approve", "Approve the payout")] + ([("resolve", "Resolve a pay dispute")] if any(d.get("open") for d in v.get("disputes", [])) else []) + [back]
    return [back]

def _open(u, phone, oid, extra=""):
    v = _mine(u, oid)
    if not v: _clear(phone); return "I could not find that order."
    txt = _card(u, v); acts = _actions(v); sug = None
    if v["status"] == "submitted":
        r = svc.review(u, oid); block, under, counter = _pricing_block(r, v["order"]["offered_price"]); txt += "\n\n" + block; sug = str(counter if under else v["order"]["offered_price"])
        acts[0] = ("quote_auto", f"Send a quote at ₹{sug} per piece")
    if v["status"] == "planned" and v.get("plan"):
        pl = v["plan"]; txt += "\n\nProposed work:\n" + "\n".join(f"{x['name']}: {x['stage']} × {x['units']} (₹{x['expected_pay']})" for x in pl["assignments"]) + ("\n⚠️ " + "; ".join(pl["issues"]) if pl["issues"] else "")
    if v["status"] == "in_production" and any(x["status"] == "pending" for x in v.get("quality", [])): txt += "\n\nSamples are waiting for you. Send PASS <order> <name>, or REWORK <order> <name> <pieces> <reason>."
    return _go(phone, "review_act", {"oid": oid, "opts": [k for k, _ in acts], "auto": sug}, (extra + "\n\n" if extra else "") + txt + f"\n\nWhat would you like to do?\n{_opts(acts)}")

def _step_review_pick(u, phone, st, t, w, media):
    v = _pick_order(svc.my_orders(u), t)
    return _open(u, phone, v["id"]) if v else "I could not find that order number. Please reply with a number from the list, or send MENU."

def _risk_text(r):
    head = f"{r['done']} of {r['needed']} pieces finished through every stage, {r['days_left']} day(s) left. Slowest stage: {r['slowest_stage']} ({r['per_day']} pieces/day). Projected {r['projected']}."
    return head + ("\nOn time." if not r["shortfall"] else f"\nShort by {r['shortfall']} pieces. Options (you and the group decide):\n- " + "\n- ".join(r["options"]))

def _step_review_act(u, phone, st, t, w, media):
    d = st["data"]; k = _choose(d["opts"], t) if (t or "").strip().isdigit() else None
    if not k: return "Please reply with a number from the list, or send MENU."
    oid = d["oid"]
    if k == "back": return _review(u, phone)
    if k == "quote_own": return _go(phone, "quote_price", {"oid": oid}, "What price per piece (in ₹) should I quote?")
    if k == "resolve": return _go(phone, "resolve_note", {"oid": oid}, "Please tell me how this dispute was resolved (a short note).")
    msg = ""
    if k == "quote_auto": v = svc.quote(u, oid, d.get("auto")); msg = f"✅ Quote of ₹{v['quote']} per piece recorded." + (f" When the buyer agrees, choose 'Record that the buyer accepted'." if _on_behalf(oid) else " The buyer has been sent this quote.")
    elif k == "accept_for_buyer": svc.accept_quote(u, oid); msg = "✅ Recorded: the buyer accepted the quote."
    elif k == "plan": svc.plan(u, oid); msg = "✅ A work plan is proposed below. Nothing starts until you approve it."
    elif k == "start": svc.start(u, oid); msg = "✅ Production started. Every member has been sent her task."
    elif k == "progress": rows = (_mine(u, oid) or {}).get("progress", {}).get("rows", []); msg = "\n".join(f"{r['name']}: {r['stage']} {r['done']}/{r['units']}" for r in rows) or "No work has been planned yet."
    elif k == "risk": msg = _risk_text(svc.risk(u, oid, None))
    elif k == "payout": p = svc.propose_payout(u, oid, None); msg = f"✅ Payout proposed, total ₹{p['payout']['total']}. Members will review it; a payout needs your approval and theirs."
    elif k == "approve": p = svc.approve_payout(u, oid); msg = f"✅ Your approval is recorded. Order status: {p['status'].replace('_', ' ')}."
    return _open(u, phone, oid, msg)

def _step_resolve_note(u, phone, st, t, w, media):
    oid = st["data"]["oid"]; o = _mine(u, oid); i = next((k for k, x in enumerate((o or {}).get("disputes", [])) if x["open"]), None)
    if i is None: return _open(u, phone, oid, "There is no open dispute on this order.")
    svc.resolve_dispute(u, oid, i, (t or "resolved by leader")[:300]); return _open(u, phone, oid, "✅ Dispute resolved and recorded.")

# ---------- member: assigned work ----------
WORK = [("accept", "Accept a task"), ("done", "Report work done"), ("sample", "Send a sample photo"), ("decline", "Decline a task"), ("issue", "Report a problem or delay"), ("back", "Back to the menu")]
def _work(u, phone):
    ts = svc.tasks(u)
    if not ts: _clear(phone); return "You have no assigned work right now. When the leader plans an order, I will message you here. Send MENU for other options."
    lines = "\n".join(i18n.t("task_line", u["lang"], o=x["order_id"], stage=i18n.stage(x["stage"], u["lang"]), n=x["units"], pay=x["expected_pay"], done=x["done"], st=x["status"].replace("_", " ")) for x in ts)
    return _go(phone, "work_menu", {}, f"Your assigned work:\n\n{lines}\n\nWhat would you like to do?\n{_opts(WORK)}")

ACT_STATUS = {"accept": ("planned", "in_production"), "done": ("in_production",), "sample": ("in_production",), "decline": ("accepted", "planned"), "issue": ("planned", "in_production", "accepted")}
def _after_order(u, phone, act, oid):
    if act == "accept": svc.accept_task(u, oid); _clear(phone); return f"✅ Thank you! You accepted your task in order #{oid}. The leader has been told. Send MENU for more options."
    if act == "decline": svc.decline(u, oid); _clear(phone); return i18n.t("declined", u["lang"]) + " Send MENU for more options."
    if act == "done": return _go(phone, "work_pieces", {"oid": oid}, f"How many pieces did you finish just now for order #{oid}?")
    if act == "sample": return _go(phone, "work_sample", {"oid": oid}, f"Please send a clear photo of your first finished piece for order #{oid}. You can add a short note with it.")
    return _go(phone, "work_issue", {"oid": oid}, f"Please tell me the problem for order #{oid} (for example: more material needed, or I will be late). You can send a voice note.")

def _step_work_menu(u, phone, st, t, w, media):
    act = _choose([k for k, _ in WORK], t) if (t or "").strip().isdigit() else None
    if not act: return "Please reply with a number from the list, or send MENU."
    if act == "back": _clear(phone); return _welcome(u["role"])
    rows = [x for x in svc.tasks(u) if x["status"] in ACT_STATUS[act]]
    if not rows: _clear(phone); return {"accept": "There is no task waiting for you to accept.", "done": "None of your orders is in production yet.", "sample": "None of your orders is in production yet.", "decline": "You can only decline a task before production starts, and there is none like that now.", "issue": "You have no active task to report on."}[act] + " Send MENU for other options."
    ids = list(dict.fromkeys(x["order_id"] for x in rows))
    if len(ids) == 1: return _after_order(u, phone, act, ids[0])
    return _go(phone, "work_order", {"act": act, "ids": ids}, "Which order? Reply with the number: " + ", ".join(f"#{i}" for i in ids))

def _step_work_order(u, phone, st, t, w, media):
    d = st["data"]; n = _num((t or "").lstrip("#"))
    if n not in d["ids"]: return "Please reply with one of these order numbers: " + ", ".join(f"#{i}" for i in d["ids"])
    return _after_order(u, phone, d["act"], n)

def _step_work_pieces(u, phone, st, t, w, media):
    n = _num(t)
    if not n: return "Please send just a number, for example 20."
    r = svc.report_done(u, st["data"]["oid"], n); _clear(phone)
    return i18n.t("done_ok", u["lang"], n=r["done"], tot=r["units"], stage=i18n.stage(r["stage"], u["lang"]), o=r["order_id"]) + " Send MENU for more options."

def _step_work_sample(u, phone, st, t, w, media):
    if not media or media.get("kind") != "image": return "Please send a photo of your finished piece."
    oid = st["data"]["oid"]; svc.submit_sample(u, oid, (t or "")[:200], media.get("photo")); _clear(phone)
    return i18n.t("sample_ok", u["lang"], o=oid, photo=" (photo)") + " Send MENU for more options."

def _step_work_issue(u, phone, st, t, w, media):
    if not (t or "").strip(): return "Please describe the problem in words or a voice note."
    svc.report_issue(u, st["data"]["oid"], t); _clear(phone); return "✅ I have told your leader. She will decide what to do next. Send MENU for more options."

# ---------- member: earnings ----------
def _earn(u, phone):
    es = svc.earnings(u)
    if not es: _clear(phone); return i18n.t("no_earn", u["lang"]) + " Send MENU for other options."
    txt = "Your earnings:\n\n" + "\n\n".join(f"Order #{e['order_id']}: {e['explain']}" for e in es) + "\n\n(These figures come from the recorded work and the approved rate card. A machine does not decide your pay.)"
    pend = next((e for e in es if e["status"] == "payout_pending" and not e["approved_by_me"]), None)
    if not pend: _clear(phone); return txt + "\n\nSend MENU for other options."
    return _go(phone, "earn_act", {"oid": pend["order_id"]}, txt + f"\n\nThe payout for order #{pend['order_id']} is waiting for you.\n1. I agree, approve it\n2. I disagree, raise a dispute\n3. Back to the menu")

def _step_earn_act(u, phone, st, t, w, media):
    a, oid = (t or "").strip(), st["data"]["oid"]
    if a == "1": v = svc.approve_payout(u, oid); _clear(phone); return i18n.t("thanks", u["lang"], st=v["status"].replace("_", " "))
    if a == "2": return _go(phone, "earn_reason", st["data"], "I am sorry about that. Please tell me what looks wrong (for example, the count of pieces). The payment waits until your leader looks at it.")
    if a == "3": _clear(phone); return _welcome(u["role"])
    return "Please reply 1, 2 or 3."
def _step_earn_reason(u, phone, st, t, w, media):
    if not (t or "").strip(): return "Please tell me what looks wrong, in words or a voice note."
    svc.dispute(u, st["data"]["oid"], t); _clear(phone); return i18n.t("disputed", u["lang"]) + " Send MENU for more options."

# ---------- menu ----------
def _step_menu(u, phone, st, t, w, media):
    k = _choose([x for x, _ in MENUS[u["role"]]], t)
    if not k: return "I did not understand that. Please reply with a number:\n\n" + _menu_text(u["role"])
    return _run(u, phone, k)
def _run(u, phone, k):
    if k == "new": return _start_order(u, phone, "", None)
    return {"check": _check, "review": _review, "work": _work, "earn": _earn}[k](u, phone)

STEPS = {"menu": _step_menu, "buyer_name": _step_buyer_name, "details": _step_details, "missing": _step_missing, "confirm": _step_confirm, "edit": _step_edit,
         "after_submit": _step_after_submit, "quote_price": _step_quote_price, "check_pick": _step_check_pick, "review_pick": _step_review_pick,
         "review_act": _step_review_act, "resolve_note": _step_resolve_note, "work_menu": _step_work_menu, "work_order": _step_work_order, "work_pieces": _step_work_pieces,
         "work_sample": _step_work_sample, "work_issue": _step_work_issue, "earn_act": _step_earn_act, "earn_reason": _step_earn_reason}
FREE = {"buyer_name", "details", "missing", "edit", "work_issue", "earn_reason", "resolve_note", "work_sample"}     # steps where typed words are content, not commands

ESCAPE = {"after_submit", "review_pick", "review_act", "check_pick", "work_menu", "work_order", "earn_act"}   # numbered-choice steps: a typed command still works

def handle(u, phone, t, w, media):
    """Returns the reply, or None to let the typed-command handler answer (HELP, QUOTE 1, DONE 1 20 ... still work)."""
    role, st, word = u["role"], _load(phone), (w[0] if w else "")
    if role not in MENUS: return None
    kind = (media or {}).get("kind")
    if len(w) <= 2 and word in GREET and not any(x.isdigit() for x in w): _save(phone, "menu", {}); return _welcome(role)
    if len(w) == 1 and word in STOP: _clear(phone); return "Okay, cancelled. Send MENU whenever you want to start again."
    if not st or st["step"] == "menu":
        if _is_command(role, w): return None
        if role == "seller" and kind == "image": return None                                  # a photo from a member is a work sample, as before
        if role in ("buyer", "leader") and (kind in ("image", "pdf", "voice") or _looks_like_order(t)): return _start_order(u, phone, t, media)
        if not st: _save(phone, "menu", {}); return _welcome(role)
    elif st["step"] in ESCAPE and _is_command(role, w) and not word.isdigit(): _clear(phone); return None
    fn = STEPS.get(st["step"]) if st else None
    if not fn: _save(phone, "menu", {}); return _welcome(role)
    return fn(u, phone, st, t, w, media)
