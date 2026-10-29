"""THE workflow. One definition of the order lifecycle that every surface reads from:
the ten dashboard tabs, the alerts bell, the tab badges, an order's "next action", and WhatsApp's TODAY command.

An order moves along:
  submitted -> quoted -> accepted -> planned -> in_production -> payout_pending -> settled -> ready_for_delivery -> dispatched -> closed
  (partially_delivered loops back to ready_for_delivery; blocked and cancelled are side exits)

For any order, pending(facts) answers: what is waiting, who owns it, how urgent, which tab handles it, and which WhatsApp
command does the same thing. Pure functions only (no database), so they are easy to test and cannot drift between surfaces."""

STATUS_LABEL = {"submitted": "New", "quoted": "Quoted", "accepted": "Accepted", "planned": "Planned", "in_production": "In production",
                "payout_pending": "Payout pending", "settled": "Settled", "ready_for_delivery": "Ready to dispatch", "dispatched": "Dispatched",
                "partially_delivered": "Part delivered", "closed": "Delivered", "blocked": "Blocked", "cancelled": "Cancelled"}
ACTIVE = ("submitted", "quoted", "accepted", "planned", "in_production", "payout_pending", "settled", "ready_for_delivery", "dispatched", "partially_delivered", "blocked")
FINISHED = ("closed", "cancelled")

# Board columns for the Orders tab, in lifecycle order. payout_pending sits with production (the work is done, the money is not);
# partially_delivered sits with settled (the rest still has to be packed).
BOARD = [("new", ["submitted"]), ("quoted", ["quoted"]), ("accepted", ["accepted"]), ("planned", ["planned"]), ("production", ["in_production", "payout_pending"]),
         ("settled", ["settled", "partially_delivered"]), ("ready", ["ready_for_delivery"]), ("dispatched", ["dispatched"]), ("delivered", ["closed"]),
         ("blocked", ["blocked"]), ("cancelled", ["cancelled"])]
BOARD_OF = {st: col for col, sts in BOARD for st in sts}

# Delivery pipeline (Delivery tab)
PIPELINE = [("count", "Ready for count", ["settled"]), ("packed", "Counted, ready to dispatch", ["ready_for_delivery"]),
            ("dispatched", "Dispatched, awaiting buyer sign-off", ["dispatched"]), ("partial", "Partially delivered", ["partially_delivered"]),
            ("delivered", "Delivered", ["closed"]), ("blocked", "Blocked", ["blocked"])]

TABS = ["overview", "orders", "team", "quality", "delivery", "payouts", "buyers", "pricing", "materials", "alerts"]

# Which tabs a ledger event touches. This is how a WhatsApp message becomes a live dashboard update.
EVENT_TABS = {
    "progress.reported": ["overview", "orders", "team"], "task.accepted": ["orders", "team"], "production.started": ["overview", "orders", "team", "quality"],
    "quality.sample_submitted": ["quality", "orders", "alerts", "overview"], "quality.ai_screened": ["quality", "orders", "alerts"],
    "quality.decided": ["quality", "orders", "team", "alerts", "overview"], "quality.checklist_set": ["quality", "orders"],
    "order.submitted": ["overview", "orders", "buyers", "alerts", "pricing"], "quote.sent": ["orders", "buyers", "pricing", "alerts", "overview"],
    "quote.accepted": ["overview", "orders", "buyers", "alerts", "payouts"], "plan.proposed": ["orders", "team", "alerts", "overview"],
    "dispute.opened": ["alerts", "team", "payouts", "overview"], "dispute.resolved": ["alerts", "payouts", "orders", "overview"],
    "member.issue": ["alerts", "team", "overview"], "member.declined": ["alerts", "team", "orders", "overview"], "issue.resolved": ["alerts", "team", "overview"],
    "payout.proposed": ["payouts", "orders", "team", "alerts", "overview"], "payout.approval": ["payouts", "orders", "alerts", "overview"], "payout.settled": ["payouts", "orders", "team", "delivery", "overview", "alerts"],
    "delivery.qty_checked": ["delivery", "orders", "alerts"], "delivery.dispatched": ["delivery", "orders", "alerts", "buyers"], "delivery.photo_added": ["delivery"],
    "delivery.signed_off": ["delivery", "orders", "buyers", "overview", "alerts", "payouts"], "order.blocked": ["delivery", "orders", "alerts", "overview"],
    "order.unblocked": ["delivery", "orders", "alerts", "overview"], "order.cancelled": ["orders", "delivery", "buyers", "overview", "alerts", "payouts"],
    "money.received": ["payouts", "buyers", "orders", "overview", "alerts"], "money.terms": ["payouts", "orders", "alerts"], "buyer.reminded": ["payouts", "buyers"],
    "work.reassigned": ["orders", "team", "alerts"], "risk.checked": ["orders", "alerts"], "risk.alert": ["orders", "alerts", "overview"], "escalation.raised": ["alerts", "overview", "quality", "payouts"],
    "ratecard.proposed": ["pricing", "alerts"], "ratecard.approval": ["pricing", "alerts"], "ratecard.activated": ["pricing", "orders"], "ratecard.rejected": ["pricing", "alerts"],
    "message.sent": ["team"], "member.added": ["team", "materials"], "member.updated": ["team"], "member.deactivated": ["team", "alerts"],
    "buyer.rated": ["buyers"], "buyer.updated": ["buyers"], "order.note": ["orders"], "material.updated": ["materials", "alerts"], "consent.recorded": ["team"],
}
ALL_TABS_DEFAULT = ["overview", "orders"]
def tabs_for(type_): return EVENT_TABS.get(type_, ALL_TABS_DEFAULT)

# ---------- pending work ----------
# priority: lower is more urgent. Order follows the Alerts centre: late, blocked, pay disputes, samples, member issues, ... then routine decisions.
PRI = {"late": 1, "blocked": 2, "dispute": 3, "sample": 4, "issue": 5, "decline": 5, "stale": 8, "ratecard": 7, "payment_overdue": 9,
       "quote": 10, "record_accept": 11, "plan": 12, "plan_infeasible": 12, "start": 13, "propose_payout": 14, "approve_payout": 15, "count": 16, "dispatch": 17,
       "material": 18, "await_work": 29, "await_buyer": 30, "await_members": 31, "await_signoff": 32, "await_approvals": 33}
KIND_LABEL = {"late": "Late", "blocked": "Blocked", "dispute": "Pay dispute", "sample": "Sample waiting", "issue": "Member issue", "decline": "Task declined", "stale": "No report in 24h",
              "ratecard": "Rate card awaiting approval", "payment_overdue": "Buyer payment overdue", "quote": "Quote needed", "record_accept": "Record buyer's acceptance", "plan": "Plan needed",
              "plan_infeasible": "Plan not feasible", "start": "Ready to start", "propose_payout": "Propose payout", "approve_payout": "Approve payout", "count": "Count packed pieces",
              "dispatch": "Dispatch", "material": "Material short", "await_work": "Members are working", "await_buyer": "Waiting for buyer", "await_members": "Waiting for members", "await_signoff": "Waiting for buyer sign-off",
              "await_approvals": "Waiting for member approvals"}
# kind -> (tab that handles it, action key the UI understands, WhatsApp command that does the same thing)
HANDLER = {"late": ("orders", "open", "RISK {id}"), "blocked": ("delivery", "unblock", "UNBLOCK {id}"), "dispute": ("payouts", "resolve_dispute", "RESOLVE {id} <note>"),
           "sample": ("quality", "review_sample", "PASS {id} <name>"), "issue": ("team", "resolve_issue", "FIXED {id} <note>"), "decline": ("orders", "plan", "PLAN {id}"),
           "stale": ("team", "message", "MSG all <text>"), "ratecard": ("pricing", "open", ""), "payment_overdue": ("payouts", "remind_buyer", "REMIND {id}"),
           "quote": ("orders", "quote", "QUOTE {id}"), "record_accept": ("orders", "accept", "ACCEPT {id}"), "plan": ("orders", "plan", "PLAN {id}"), "plan_infeasible": ("orders", "plan", "PLAN {id}"),
           "start": ("orders", "start", "START {id}"), "propose_payout": ("payouts", "propose_payout", "PAYOUT {id}"), "approve_payout": ("payouts", "approve_payout", "APPROVE {id}"),
           "count": ("delivery", "count", "READY {id} <pieces>"), "dispatch": ("delivery", "dispatch", "DISPATCH {id} <courier>"), "material": ("materials", "open", "STOCK"), "await_work": ("orders", "open", "PROGRESS {id}"),
           "await_buyer": ("orders", "open", ""), "await_members": ("orders", "open", ""), "await_signoff": ("delivery", "open", ""), "await_approvals": ("payouts", "open", "")}
PASSIVE_OWNERS = ("buyer", "member")

def _item(kind, f, title, detail="", owner="leader", ref=None, ts=None, escalated=False, hint=None):
    tab, action, cmd = HANDLER[kind]; pri = PRI[kind]
    if escalated: pri = min(pri, 3)
    return {"id": f"{kind}:{f['id']}:{ref if ref is not None else ''}", "kind": kind, "label": KIND_LABEL[kind], "priority": pri, "owner": owner, "order_id": f["id"], "title": title, "detail": detail,
            "tab": tab, "action": action, "cmd": cmd.format(id=f["id"]), "ts": ts or f.get("last_event_ts") or f.get("created"), "escalated": bool(escalated), "hint": hint or {}}

def pending(f):
    """f = facts about ONE order (built by hub.Ctx.facts). Returns every pending item, most urgent first."""
    st, n, out = f["status"], f["id"], []
    who = f"#{n}"
    if st in FINISHED: return out
    if st == "blocked": out.append(_item("blocked", f, f"Order {who} is on hold: {f.get('blocked_reason') or 'no reason given'}", "Delivery cannot go ahead until you unblock it.", ts=f.get("blocked_ts")))
    elif f.get("late"): out.append(_item("late", f, f"Order {who} is late ({abs(f['days_left'])} day(s) past the delivery date)" if f.get("days_left") is not None else f"Order {who} is late", f"{f['finished']} of {f['qty']} pieces finished through every stage.", ts=f.get("due")))
    for d in f.get("disputes_open", []): out.append(_item("dispute", f, f"{d['name']} disputed pay on order {who}", d.get("reason", ""), ref=d["i"], ts=d["ts"], escalated=d.get("escalated")))
    for q in f.get("samples_pending", []): out.append(_item("sample", f, f"{q['name']} sent a {q['stage']} sample for order {who}", f"{q['flags']} possible problem(s) flagged by the AI check" if q.get("flags") else "Waiting for your decision", ref=q["member"], ts=q["ts"], escalated=q.get("escalated")))
    for x in f.get("issues_open", []): out.append(_item("issue", f, f"{x['name']} reported a problem on order {who}", x["text"], ref=x["i"], ts=x["ts"]))
    if f.get("stale"): out.append(_item("stale", f, f"Nobody has reported progress on order {who} for {f['stale_hours']} hours", "Ask the members what is happening.", ts=f.get("stale_since")))
    if f.get("overdue_amount"): out.append(_item("payment_overdue", f, f"Buyer owes Rs {f['overdue_amount']} on order {who} ({f['overdue_days']} day(s) overdue)", "Send the buyer a reminder.", ts=f.get("money_due_ts")))
    if st == "submitted": out.append(_item("quote", f, f"Order {who} needs a quote", "The offer is below the floor price." if f.get("underpriced") else "Check the price and send a quote.", hint={"counter": f.get("suggested")}))
    elif st == "quoted": out.append(_item("record_accept", f, f"Record that the buyer accepted order {who}", "You placed this order for the buyer.") if f.get("on_behalf") else _item("await_buyer", f, f"Waiting for the buyer to accept the quote on order {who}", owner="buyer"))
    elif st == "accepted":
        if f.get("declined_unplanned"): out.append(_item("decline", f, f"A member declined her task on order {who}", "Replan to give the work to someone else. There is no penalty for declining.", ts=f.get("declined_ts")))
        else: out.append(_item("plan", f, f"Build the work plan for order {who}"))
    elif st == "planned":
        if not f.get("plan_feasible", True): out.append(_item("plan_infeasible", f, f"The plan for order {who} is not feasible", "; ".join(f.get("plan_issues", [])), hint={"options": f.get("plan_options", [])}))
        else:
            out.append(_item("start", f, f"Approve the plan and start order {who}", f"{f['accepted_count']} of {f['members_count']} members have accepted"))
            if f["accepted_count"] < f["members_count"]: out.append(_item("await_members", f, f"Waiting for members to accept their tasks on order {who}", f"{f['accepted_count']} of {f['members_count']} accepted", owner="member"))
    elif st == "in_production":
        if f.get("all_done") and not f.get("samples_pending"): out.append(_item("propose_payout", f, f"All work on order {who} is reported done: propose the payout"))
        elif not f.get("all_done"): out.append(_item("await_work", f, f"Members are working on order {who}", f"{f['finished']} of {f['qty']} pieces finished through every stage", owner="member"))
    elif st == "payout_pending":
        if not f.get("leader_approved"): out.append(_item("approve_payout", f, f"Approve the payout of Rs {f.get('payout_total')} on order {who}", f"{f['member_approvals']} of {f['member_quorum']} members approved"))
        elif f["member_approvals"] < f["member_quorum"]: out.append(_item("await_approvals", f, f"Waiting for members to approve the payout on order {who}", f"{f['member_approvals']} of {f['member_quorum']}", owner="member"))
    elif st in ("settled", "partially_delivered"): out.append(_item("count", f, f"Count the packed pieces for order {who}", f"{f['remaining']} piece(s) still owed to the buyer"))
    elif st == "ready_for_delivery": out.append(_item("dispatch", f, f"Dispatch order {who}", f"{f['counted']} piece(s) counted"))
    elif st == "dispatched": out.append(_item("await_signoff", f, f"Waiting for the buyer to sign off order {who}", owner="buyer"))
    out.sort(key=lambda i: (i["priority"], i["ts"] or 0)); return out

def health(f):
    """-> (key, reasons). Keys: on_track, at_risk, late, blocked, done, cancelled."""
    st = f["status"]
    if st == "cancelled": return "cancelled", ["Cancelled"]
    if st == "closed": return "done", ["Delivered and closed"]
    if st == "blocked": return "blocked", [f.get("blocked_reason") or "On hold"]
    if f.get("late"): return "late", ["Past the delivery date"]
    why = []
    if f.get("shortfall"): why.append(f"About {f['shortfall']} pieces short at the current pace")
    if f.get("stale"): why.append(f"No progress reported for {f['stale_hours']} hours")
    if f.get("samples_waiting_long"): why.append("A sample has waited a long time for your decision")
    if f.get("overdue_amount"): why.append("Buyer payment is overdue")
    return ("at_risk", why) if why else ("on_track", ["On track"])

def describe(e, name):
    """One-line, human description of a ledger event for the Live activity feed and the Timeline. name(actor) -> display name."""
    t, p, who = e["type"], e.get("payload") or {}, name(e["actor"]); o = f" on order #{e['order_id']}" if e.get("order_id") else ""
    m = {"order.submitted": lambda: f"New order #{e['order_id']}: {p.get('qty')} pcs at Rs {p.get('offered_price')}", "quote.sent": lambda: f"Quote of Rs {p.get('quote')} sent{o}",
         "quote.accepted": lambda: f"Quote accepted{o}", "plan.proposed": lambda: f"Work plan proposed{o}" + ("" if p.get("feasible", True) else " (not feasible)"), "production.started": lambda: f"Production started{o}",
         "progress.reported": lambda: f"{who} reported {p.get('done')} of {p.get('of')} {p.get('stage')} pieces{o}", "task.accepted": lambda: f"{who} accepted her task{o}", "member.declined": lambda: f"{who} declined her task{o}",
         "member.issue": lambda: f"{who} reported a problem{o}: {p.get('text', '')}", "issue.resolved": lambda: f"Problem resolved{o}",
         "quality.sample_submitted": lambda: f"{who} sent a sample{o}" + (" with a photo" if p.get("photo") else ""), "quality.ai_screened": lambda: f"AI pre-check{o}: {p.get('flagged', 0)} possible problem(s) flagged" if p.get("status") == "ok" else f"AI pre-check unavailable{o}",
         "quality.decided": lambda: f"{who} decided: sample {p.get('decision')}{o}", "quality.checklist_set": lambda: f"Quality checklist updated{o}", "payout.proposed": lambda: f"Payout of Rs {p.get('total')} proposed{o}",
         "payout.approval": lambda: f"{who} approved the payout{o}", "payout.settled": lambda: f"Payout settled{o}", "dispute.opened": lambda: f"{who} disputed pay{o}", "dispute.resolved": lambda: f"Pay dispute resolved{o}",
         "delivery.qty_checked": lambda: f"Counted {p.get('counted')} of {p.get('owed')} pieces{o}", "delivery.dispatched": lambda: f"Dispatched {p.get('qty')} pieces via {p.get('courier')}{o}",
         "delivery.photo_added": lambda: f"Delivery photo added{o}", "delivery.signed_off": lambda: f"Buyer received {p.get('received')} of {p.get('sent')} pieces{o}", "order.blocked": lambda: f"Order blocked{o}: {p.get('reason')}",
         "order.unblocked": lambda: f"Order unblocked{o}", "order.cancelled": lambda: f"Order cancelled{o}", "money.received": lambda: f"Rs {p.get('amount')} received from the buyer{o}", "money.terms": lambda: f"Payment due date set{o}",
         "buyer.reminded": lambda: f"Payment reminder sent to the buyer{o}", "work.reassigned": lambda: f"{p.get('pieces')} {p.get('stage')} pieces moved to another member{o}", "risk.checked": lambda: f"Deadline check{o}",
         "risk.alert": lambda: f"Deadline risk: about {p.get('shortfall')} pieces short{o}", "escalation.raised": lambda: f"{p.get('disputes', 0) + p.get('sample_reviews', 0)} item(s) flagged to the mission officer",
         "ratecard.proposed": lambda: f"Rate card proposed: {p.get('product')} v{p.get('version')}", "ratecard.approval": lambda: f"{who} approved rate card {p.get('product')} v{p.get('version')}",
         "ratecard.activated": lambda: f"Rate card active: {p.get('product')} v{p.get('version')}", "ratecard.rejected": lambda: f"Rate card withdrawn: {p.get('product')} v{p.get('version')}",
         "message.sent": lambda: f"Message sent to {p.get('n', 1)} member(s)", "member.added": lambda: f"Member added: {p.get('name', '')}", "member.updated": lambda: f"Member details updated: {p.get('name', '')}",
         "member.deactivated": lambda: f"Member deactivated: {p.get('name', '')}", "buyer.rated": lambda: f"Buyer rated the group {p.get('stars')}/5{o}", "order.note": lambda: f"Private note added{o}",
         "material.updated": lambda: f"Materials updated: {p.get('what', '')}", "consent.recorded": lambda: f"{who} gave consent"}
    return (m.get(t) or (lambda: t.replace(".", " ")))()
