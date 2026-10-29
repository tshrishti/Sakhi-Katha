import envload  # noqa: F401  (loads .env before anything reads the environment)
import asyncio, base64, contextlib, csv, hmac, io, logging, os, pathlib
from xml.sax.saxutils import escape
from sqlalchemy import select
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import auth, bootstrap, bot, db, hub, media as mediafetch, ops, svc, voice, workflow

log = logging.getLogger("sakhi")
DEV = auth.DEV
bootstrap.ensure()                                       # tables + the SHG group + the owner phone from .env
if not DEV and db.IS_SQLITE:
    log.warning("DATABASE_URL is not set: using local SQLite. On Render this is wiped on every deploy. Attach a Postgres database.")
if not DEV and not os.getenv("PUBLIC_URL"):
    log.warning("PUBLIC_URL is not set: Twilio webhook signature checks will fail and login cookies will not be Secure.")

SCHED_MIN = int(os.getenv("SCHEDULER_MINUTES", "15"))   # in-process reminder loop; set ENABLE_SCHEDULER=0 to use an external cron instead
async def _scheduler():
    await asyncio.sleep(60)                              # let the app finish starting
    while True:
        try:
            r = await asyncio.to_thread(svc.run_reminders); e = await asyncio.to_thread(svc.escalate_overdue)
            log.info("scheduler: reminders=%s escalated=%s", r, e)
        except Exception:
            log.exception("scheduler run failed")
        await asyncio.sleep(max(1, SCHED_MIN) * 60)

@contextlib.asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_scheduler()) if os.getenv("ENABLE_SCHEDULER", "1") == "1" and not DEV else None
    yield
    if task: task.cancel()

app = FastAPI(title="Sakhi Khata", lifespan=lifespan, docs_url="/docs" if DEV else None, redoc_url=None, openapi_url="/openapi.json" if DEV else None)
HOME = {"buyer": "/app/buyer.html", "seller": "/app/seller.html", "leader": "/app/leader.html", "officer": "/app/officer.html"}

@app.middleware("http")
async def security_headers(request: Request, call_next):
    r = await call_next(request)
    r.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})
    if request.url.path.startswith("/api"): r.headers["Cache-Control"] = "no-store"
    return r
@app.exception_handler(svc.Err)
async def svc_err(request, e): return JSONResponse({"detail": e.msg}, status_code=e.code)
@app.get("/healthz")
def healthz(): return {"ok": True}                           # liveness: process is up (no DB call)
@app.get("/readyz")
def readyz():                                                 # readiness: database reachable
    from sqlalchemy import text
    try:
        with db.engine.connect() as c: c.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"ok": False, "db": "down"}, status_code=503)
    return {"ok": True, "db": "up"}

@app.get("/api/public")
def public():                                                  # only what the landing page needs to point people at the WhatsApp number
    return {"whatsapp": os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886").replace("whatsapp:", ""), "join": os.getenv("SANDBOX_JOIN_PHRASE", "")}

# ---- auth ----
@app.post("/api/auth/request-otp")
def request_otp(b: dict): return auth.request_otp(b.get("phone", ""))
@app.post("/api/auth/verify")
def verify(b: dict, resp: Response):
    u = auth.verify_otp(b.get("phone", ""), str(b.get("code", "")))
    resp.set_cookie("sk", auth.make_token(u["id"]), httponly=True, samesite="lax", secure=auth.SECURE_COOKIE, max_age=auth.TTL)
    return {**u, "home": HOME[u["role"]]}
@app.post("/api/auth/logout")
def logout(resp: Response): resp.delete_cookie("sk"); return {"ok": True}
@app.get("/api/me")
def me(u=Depends(auth.require())): return u
@app.post("/api/me/lang")
def set_lang(b: dict, u=Depends(auth.require())):
    import i18n
    code = i18n.LANGS.get(str(b.get("lang", "")).lower())
    if not code: raise HTTPException(422, "Language must be en, hi or hing")
    svc.set_lang(u, code); return {"ok": True, "lang": code}
@app.post("/api/me/consent")
def consent(u=Depends(auth.require())): return svc.consent(u)

# ---- buyer ----
B = Depends(auth.require("buyer"))
@app.get("/api/products")
def products(): return svc.products()
@app.get("/api/buyer/groups")
def b_groups(u=B): return svc.groups()
@app.post("/api/buyer/orders")
def b_submit(b: dict, u=B): return svc.submit_order(u, int(b.get("group_id", 0)), str(b.get("text", ""))[:1000])
@app.get("/api/buyer/orders")
def b_orders(u=B): return svc.my_orders(u)
@app.post("/api/buyer/orders/{oid}/accept")
def b_accept(oid: int, u=B): return svc.accept_quote(u, oid)

@app.post("/api/buyer/orders/{oid}/signoff")
def b_signoff(oid: int, b: dict, u=B): return svc.signoff(u, oid, b.get("received"), b.get("note", ""), bool(b.get("final")))
@app.post("/api/buyer/orders/{oid}/rate")
def b_rate(oid: int, b: dict, u=B): return ops.rate_group(u, oid, b.get("stars"), b.get("comment", ""))
@app.post("/api/buyer/orders/{oid}/cancel")
def b_cancel(oid: int, b: dict, u=B): return svc.cancel(u, oid, b.get("reason", ""))
@app.get("/api/buyer/orders/{oid}/delivery/{i}/photo")
def b_dphoto(oid: int, i: int, u=B): return _img(svc.delivery_photo_get(u, oid, i))

# ---- leader ----
L = Depends(auth.require("leader"))
@app.get("/api/leader/orders")
def l_orders(u=L): return svc.my_orders(u)
@app.post("/api/leader/orders/{oid}/quote")
def l_quote(oid: int, b: dict, u=L): return svc.quote(u, oid, b.get("counter"))
@app.post("/api/leader/orders/{oid}/plan")
def l_plan(oid: int, u=L): return svc.plan(u, oid)
@app.post("/api/leader/orders/{oid}/start")
def l_start(oid: int, u=L): return svc.start(u, oid)
@app.post("/api/leader/orders/{oid}/payout")
def l_payout(oid: int, b: dict, u=L): return svc.propose_payout(u, oid, b.get("results"))
@app.post("/api/leader/orders/{oid}/payout/approve")
def l_approve(oid: int, u=L): return svc.approve_payout(u, oid)
@app.post("/api/leader/orders/{oid}/disputes/{i}/resolve")
def l_resolve(oid: int, i: int, b: dict, u=L): return svc.resolve_dispute(u, oid, i, b.get("note", ""))
@app.post("/api/leader/orders/{oid}/quality")
def l_quality(oid: int, b: dict, u=L): return svc.review_sample(u, oid, b.get("member", ""), "pass" if b.get("decision") == "pass" else "rework", b.get("rework", 0), b.get("reason", ""))
@app.post("/api/leader/orders/{oid}/risk")
def l_risk(oid: int, b: dict, u=L): return svc.risk(u, oid, b.get("days_left"))
@app.get("/api/leader/orders/{oid}/quality/{i}/photo")
def l_photo(oid: int, i: int, u=L): return _img(svc.photo(u, oid, i))
@app.post("/api/leader/orders/{oid}/quality/{i}/rescreen")
def l_rescreen(oid: int, i: int, u=L): return svc.rescreen(u, oid, i)
@app.get("/api/leader/orders/{oid}/checklist")
def l_checklist(oid: int, u=L): return svc.get_checklist(u, oid)
@app.put("/api/leader/orders/{oid}/checklist")
def l_set_checklist(oid: int, b: dict, u=L): return svc.set_checklist(u, oid, b.get("items", []))
@app.post("/api/leader/orders/{oid}/ready")
def l_ready(oid: int, b: dict, u=L): return svc.mark_ready(u, oid, b.get("counted"), b.get("note", ""))
@app.post("/api/leader/orders/{oid}/dispatch")
def l_dispatch(oid: int, b: dict, u=L): return svc.dispatch(u, oid, b.get("courier"), b.get("tracking", ""), b.get("date", ""), b.get("note", ""))
@app.post("/api/leader/orders/{oid}/delivery-photo")
def l_dphoto(oid: int, b: dict, u=L): return svc.delivery_photo(u, oid, b.get("photo"))
@app.get("/api/leader/orders/{oid}/delivery/{i}/photo")
def l_dphoto_get(oid: int, i: int, u=L): return _img(svc.delivery_photo_get(u, oid, i))
@app.post("/api/leader/orders/{oid}/block")
def l_block(oid: int, b: dict, u=L): return svc.block(u, oid, b.get("reason"))
@app.post("/api/leader/orders/{oid}/unblock")
def l_unblock(oid: int, b: dict, u=L): return svc.unblock(u, oid, b.get("note", ""))
@app.post("/api/leader/orders/{oid}/cancel")
def l_cancel(oid: int, b: dict, u=L): return svc.cancel(u, oid, b.get("reason", ""))
@app.get("/api/leader/members")
def l_members(u=L): return svc.members(u)
@app.post("/api/leader/members")
def l_add(b: dict, u=L): return svc.add_member(u, b.get("phone", ""), b.get("name", ""), b.get("skills", []), b.get("capacity", 0))

@app.get("/api/leader/ratecards")
def l_rc(u=L): return svc.ratecards_list(u)
@app.post("/api/leader/ratecards")
def l_rc_propose(b: dict, u=L): return svc.propose_ratecard(u, str(b.get("product", "")), b.get("stages"), b.get("cost"), b.get("note", ""))
@app.post("/api/leader/ratecards/{cid}/approve")
def l_rc_ok(cid: int, u=L): return svc.approve_ratecard(u, cid)
@app.post("/api/leader/ratecards/{cid}/reject")
def l_rc_no(cid: int, u=L): return svc.reject_ratecard(u, cid)

# ---- leader hub: ten tabs, one shared workflow (see workflow.py, hub.py, ops.py) ----
def _hub(u, fn, *a, **k):
    with db.S() as s: return fn(hub.Ctx(s, u), *a, **k)
VIEWS = {"overview": hub.overview, "orders": hub.orders_view, "team": hub.team_view, "quality": hub.quality_view, "delivery": hub.delivery_view, "payouts": hub.payouts_view,
         "buyers": hub.buyers_view, "pricing": hub.pricing_view, "materials": hub.materials_view, "alerts": hub.alerts_view}
@app.get("/api/leader/view/{tab}")
def l_view(tab: str, u=L):
    if tab not in VIEWS: raise HTTPException(404, "No such tab")
    return _hub(u, VIEWS[tab])
@app.get("/api/leader/events")
def l_events(after: int | None = None, u=L):
    with db.S() as s: return hub.events_since(s, u, after)
@app.get("/api/leader/search")
def l_search(q: str = "", u=L): return _hub(u, hub.search, q)
@app.get("/api/leader/orders/{oid}/detail")
def l_detail(oid: int, u=L): return _hub(u, hub.order_detail, oid)
@app.get("/api/leader/orders/{oid}/timeline")
def l_timeline(oid: int, u=L): return _hub(u, hub.timeline, oid)
@app.post("/api/leader/orders")
def l_create(b: dict, u=L): return ops.create_order(u, b)
@app.post("/api/leader/orders/{oid}/repeat")
def l_repeat(oid: int, b: dict, u=L): return ops.repeat_order(u, oid, b.get("qty"), b.get("offered_price"), b.get("deadline_days"), b.get("delivery_date"))
@app.post("/api/leader/orders/{oid}/accept")
def l_accept(oid: int, u=L): return svc.accept_quote(u, oid)
@app.post("/api/leader/orders/{oid}/money")
def l_money(oid: int, b: dict, u=L): return ops.record_payment(u, oid, b.get("amount"), b.get("kind"), b.get("method", ""), b.get("note", ""))
@app.put("/api/leader/orders/{oid}/money/due")
def l_money_due(oid: int, b: dict, u=L): return ops.set_due_date(u, oid, b.get("due_date"))
@app.post("/api/leader/orders/{oid}/money/remind")
def l_money_remind(oid: int, u=L): return ops.remind_buyer(u, oid)
@app.post("/api/leader/orders/{oid}/notes")
def l_note(oid: int, b: dict, u=L): return ops.add_note(u, oid, b.get("text"))
@app.post("/api/leader/orders/{oid}/reassign")
def l_reassign(oid: int, b: dict, u=L): return ops.reassign(u, oid, int(b.get("from", 0)), int(b.get("to", 0)), str(b.get("stage", "")), b.get("pieces"))
@app.post("/api/leader/orders/{oid}/issues/{i}/resolve")
def l_issue(oid: int, i: int, b: dict, u=L): return ops.resolve_issue(u, oid, i, b.get("note", ""))
@app.get("/api/leader/orders/{oid}/evidence/photo")
def l_evidence(oid: int, u=L):
    with db.S() as s:
        ph = ((svc._order(s, oid, u).data.get("evidence") or {}).get("photo"))
    if not ph: raise HTTPException(404, "No photo")
    return _img(ph)
@app.get("/api/leader/members/{mid}")
def l_member(mid: int, u=L): return _hub(u, hub.member_detail, mid)
@app.put("/api/leader/members/{mid}")
def l_member_edit(mid: int, b: dict, u=L): return ops.update_member(u, mid, b.get("name"), b.get("skills"), b.get("capacity"), b.get("lang"), b.get("active"), b.get("phone"))
@app.post("/api/leader/messages")
def l_message(b: dict, u=L): return ops.send_message(u, b.get("to", "all"), b.get("text"), b.get("template"))
@app.post("/api/leader/capacity-check")
def l_capacity(b: dict, u=L): return _hub(u, hub.can_take, str(b.get("product", "")), b.get("qty"), b.get("days"))
@app.post("/api/leader/price-calc")
def l_pricecalc(b: dict, u=L): return _hub(u, hub.price_calc, str(b.get("product", "")), b.get("qty"), b.get("offered"))
@app.get("/api/leader/buyers/profile")
def l_buyer(key: str, u=L): return _hub(u, hub.buyer_profile, key)
@app.put("/api/leader/buyers/profile")
def l_buyer_edit(b: dict, u=L): return ops.update_buyer(u, str(b.get("key", "")), b.get("notes"), b.get("phone"), b.get("address"))
@app.get("/api/leader/activity")
def l_activity(order: int | None = None, member: int | None = None, type: str | None = None, limit: int = 200, u=L): return _hub(u, hub.activity, order, member, type, min(max(limit, 1), 1000))
@app.get("/api/leader/activity.csv")
def l_activity_csv(order: int | None = None, member: int | None = None, type: str | None = None, u=L):
    txt = _hub(u, hub.activity_csv, order=order, member=member, type_=type)
    return Response(txt, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sakhi_khata_activity.csv"})
@app.get("/api/leader/prefs")
def l_prefs(u=L): return ops.get_prefs(u)
@app.put("/api/leader/prefs")
def l_prefs_set(b: dict, u=L): return ops.set_prefs(u, b.get("whatsapp_alerts", True))
@app.post("/api/leader/materials")
def l_mat(b: dict, u=L): return ops.save_material(u, b.get("id"), b.get("name"), b.get("unit"), b.get("qty"), b.get("per_piece"), b.get("active"))
@app.post("/api/leader/suppliers")
def l_sup(b: dict, u=L): return ops.save_supplier(u, b.get("id"), b.get("name"), b.get("phone"), b.get("note"))
@app.post("/api/leader/purchases")
def l_buy(b: dict, u=L): return ops.add_purchase(u, int(b.get("material_id", 0)), b.get("qty"), b.get("cost"), b.get("supplier_id"), b.get("supplier_name"), b.get("note", ""))
@app.post("/api/leader/orders/{oid}/materials")
def l_use(oid: int, b: dict, u=L): return ops.use_material(u, oid, int(b.get("material_id", 0)), b.get("qty"))
PRIVACY = ("Sakhi Khata keeps your group's orders, work reports, quality samples and pay records so the group can price, plan and pay fairly. "
           "Members see only their own pay. Buyers see their own orders and how far along the work is, never costs or members. Buyers' phone numbers are visible to you only. "
           "Mission officers see totals only, with small groups hidden. Every change is written to a tamper-evident audit log. Private notes you write stay on the order and are not copied into that log. "
           "Anyone can ask you to correct or remove their records.")
@app.get("/api/privacy")
def privacy(u=Depends(auth.require())): return {"text": PRIVACY}

# ---- seller (SHG member) ----
M = Depends(auth.require("seller"))
@app.get("/api/seller/ratecards")
def s_rc(u=M): return svc.ratecards_list(u)
@app.post("/api/seller/ratecards/{cid}/approve")
def s_rc_ok(cid: int, u=M): return svc.approve_ratecard(u, cid)
@app.get("/api/seller/tasks")
def s_tasks(u=M): return svc.tasks(u)
@app.get("/api/seller/earnings")
def s_earn(u=M): return svc.earnings(u)
@app.post("/api/seller/orders/{oid}/done")
def s_done(oid: int, b: dict, u=M): return svc.report_done(u, oid, b.get("n"))
@app.post("/api/seller/orders/{oid}/sample")
def s_sample(oid: int, b: dict, u=M): return svc.submit_sample(u, oid, str(b.get("note", ""))[:200], b.get("photo"))
@app.get("/api/seller/orders/{oid}/checklist")
def s_checklist(oid: int, u=M): return svc.get_checklist(u, oid)
@app.post("/api/seller/orders/{oid}/issue")
def s_issue(oid: int, b: dict, u=M): return svc.report_issue(u, oid, b.get("text", ""))
@app.post("/api/seller/orders/{oid}/accept")
def s_accept(oid: int, u=M): return svc.accept_task(u, oid)
@app.post("/api/seller/orders/{oid}/decline")
def s_decline(oid: int, u=M): return svc.decline(u, oid)
@app.post("/api/seller/orders/{oid}/payout/approve")
def s_approve(oid: int, u=M): return svc.approve_payout(u, oid)
@app.post("/api/seller/orders/{oid}/dispute")
def s_dispute(oid: int, b: dict, u=M): return svc.dispute(u, oid, b.get("reason", ""))

def _img(data_url):
    head, b64 = data_url.split(",", 1)
    return Response(base64.b64decode(b64), media_type=head[5:].split(";")[0], headers={"Cache-Control": "private, max-age=60"})

# ---- officer / mission / federation: aggregates only ----
@app.get("/api/officer/summary")
def o_summary(u=Depends(auth.require("officer"))): return svc.officer_summary()
@app.get("/api/officer/export.csv")
def o_export(u=Depends(auth.require("officer"))):
    rows = svc.officer_summary(); out = io.StringIO(); w = csv.DictWriter(out, ["group", "block", "district", "orders", "settled", "in_progress", "in_delivery", "closed", "blocked", "cancelled", "underpriced_offers", "open_disputes", "escalated_open"])
    w.writeheader(); [w.writerow(r) for r in rows]
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sakhi_khata_summary.csv"})
def _cron_auth(request):                                    # call from a scheduler (Railway cron, cron-job.org): header X-Cron-Secret
    sec = os.getenv("CRON_SECRET", "")
    if not sec or not hmac.compare_digest(request.headers.get("X-Cron-Secret", ""), sec): raise HTTPException(404, "Not found")
@app.post("/api/cron/escalate")
def cron_escalate(request: Request):
    _cron_auth(request); return {"raised": svc.escalate_overdue()}
@app.post("/api/cron/reminders")
def cron_reminders(request: Request):                       # one endpoint for every scheduled reminder: task due, deadline risk, sample review, payout approval, escalation
    _cron_auth(request); return svc.run_reminders()
@app.get("/api/ledger/verify")
def ledger_verify(u=Depends(auth.require("leader", "officer"))): return svc.LED.verify()

# ---- WhatsApp (Twilio) ----
def _twilio_ok(request, form, path="/whatsapp"):
    tok = os.getenv("TWILIO_AUTH_TOKEN")
    if not tok: return DEV                                  # production refuses unsigned webhooks
    from twilio.request_validator import RequestValidator
    return RequestValidator(tok).validate(os.getenv("PUBLIC_URL", "") + path, dict(form), request.headers.get("X-Twilio-Signature", ""))
@app.post("/whatsapp/status")
async def whatsapp_status(request: Request):               # Twilio reports sent / delivered / read / failed for messages the leader sent to members
    form = await request.form()
    if not _twilio_ok(request, form, "/whatsapp/status"): return Response("forbidden", status_code=403)
    ops.message_status(form.get("MessageSid"), str(form.get("MessageStatus", "")).lower()); return Response("", status_code=204)
@app.post("/whatsapp")
async def whatsapp(request: Request):
    form = await request.form()
    if not _twilio_ok(request, form): return Response("forbidden", status_code=403)
    frm, text, sid, media, failed = form.get("From", ""), form.get("Body", ""), form.get("MessageSid"), None, None
    if int(form.get("NumMedia", 0) or 0):
        url, ctype = form.get("MediaUrl0"), form.get("MediaContentType0", "")
        if ctype.startswith("image/"):                                              # a sample photo, or an order written by hand
            small, big = await mediafetch.fetch_image_pair(url, ctype)
            media = {"kind": "image", "photo": small, "photo_hi": big or small, "url": url, "ctype": ctype}
        elif ctype == "application/pdf":                                            # an order document
            pdf = await mediafetch.fetch_pdf_text(url)
            media = {"kind": "pdf", "text": pdf, "url": url, "ctype": ctype}
            if not pdf and not text: failed = "I received your PDF but could not read any text in it (it may be a scan). Please send a clear photo of the order, a voice note, or type the details."
        elif ctype.startswith("audio/") or ctype in ("application/ogg", "video/ogg"):
            if not text: text = await voice.transcribe(url, ctype) or ""
            media = {"kind": "voice", "url": url, "ctype": ctype}
            if not text: failed = "Voice note received, but I could not understand it. Please type the message."
        else: failed = "Sorry, I can read text, voice notes, photos and PDF files only."
    reply = failed if failed else bot.handle(frm, text, sid, media)
    return Response(f"<Response>{'<Message>' + escape(reply) + '</Message>' if reply else ''}</Response>", media_type="application/xml")

app.mount("/", StaticFiles(directory=pathlib.Path(__file__).parent.parent / "frontend", html=True), name="web")
