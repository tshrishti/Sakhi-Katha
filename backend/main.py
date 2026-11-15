import envload  # noqa: F401  (loads .env before anything reads the environment)
import asyncio, base64, contextlib, csv, hmac, io, json, logging, os, pathlib, threading, time
from xml.sax.saxutils import escape
from sqlalchemy import func, select
from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import auth, bootstrap, bot, db, events, media as mediafetch, svc, voice

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
_stream_lock = threading.Lock()
_stream_users = {}

def _ledger_batch(cursor, limit):
    stmt = (select(db.LedgerEvent, db.Order)
            .outerjoin(db.Order, db.Order.id == db.LedgerEvent.order_id)
            .where(db.LedgerEvent.i > cursor)
            .order_by(db.LedgerEvent.i)
            .limit(limit))
    with db.S() as s:
        return list(s.execute(stmt).all())

def _sse(message, event_id=None, event_name="change"):
    lines = []
    if event_id is not None: lines.append(f"id: {event_id}")
    lines.extend((f"event: {event_name}", "data: " + json.dumps(message, ensure_ascii=False, separators=(",", ":")), ""))
    return "\n".join(lines) + "\n"

@app.middleware("http")
async def security_headers(request: Request, call_next):
    r = await call_next(request)
    r.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})
    if request.url.path == "/api/stream": r.headers["Cache-Control"] = "no-cache"
    elif request.url.path.startswith("/api"): r.headers["Cache-Control"] = "no-store"
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

@app.get("/app/go", include_in_schema=False)
def magic_link_page():
    return FileResponse(pathlib.Path(__file__).parent.parent / "frontend" / "app" / "go.html",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

# ---- auth ----
@app.post("/api/auth/request-otp")
def request_otp(b: dict): return auth.request_otp(b.get("phone", ""))
@app.post("/api/auth/verify")
def verify(b: dict, resp: Response):
    u = auth.verify_otp(b.get("phone", ""), str(b.get("code", "")))
    resp.set_cookie("sk", auth.make_token(u["id"]), httponly=True, samesite="lax", secure=auth.SECURE_COOKIE, max_age=auth.TTL)
    return {**u, "home": HOME[u["role"]]}
@app.post("/api/auth/magic-link/verify")
def verify_magic(b: dict, resp: Response):
    u = svc.consume_magic_link(b.get("token", ""))
    resp.set_cookie("sk", auth.make_token(u["id"]), httponly=True, samesite="lax",
                    secure=auth.SECURE_COOKIE, max_age=auth.TTL)
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

# ---- live dashboard events ----
@app.get("/api/stream")
async def stream(request: Request):
    if os.getenv("STREAM_ENABLED", "1") != "1":
        raise HTTPException(503, "Live updates are temporarily unavailable")
    user = auth.current_user(request)
    if not user: raise HTTPException(401, "Please log in")
    token = request.cookies.get("sk", "")
    expires = auth.token_expiry(token)
    if expires is None: raise HTTPException(401, "Please log in")
    limit = max(1, int(os.getenv("STREAM_MAX_PER_USER", "3")))
    with _stream_lock:
        current = _stream_users.get(user["id"], 0)
        if current >= limit: raise HTTPException(429, "Too many live dashboard connections")
        _stream_users[user["id"]] = current + 1
    try:
        raw_cursor = request.headers.get("last-event-id") or request.query_params.get("since")
        if raw_cursor is None:
            with db.S() as s:
                latest = s.scalar(select(func.max(db.LedgerEvent.i)))
                cursor = -1 if latest is None else latest
        else:
            try: cursor = max(-1, int(raw_cursor))
            except ValueError: raise HTTPException(400, "Invalid event cursor")
    except Exception:
        with _stream_lock:
            _stream_users[user["id"]] = max(0, _stream_users.get(user["id"], 1) - 1)
        raise

    async def updates():
        last_heartbeat, last_metrics = time.monotonic(), 0
        try:
            yield "retry: 3000\n\n"
            replay = _ledger_batch(cursor, 201)
            current_cursor = cursor
            if len(replay) > 200:
                yield _sse({"type": "resync"}, event_name="change")
                return
            projected = events.project_joined([(row, order) for row, order in replay], user)
            for message in projected:
                if user["role"] == "officer":
                    if time.monotonic() - last_metrics < 10: continue
                    last_metrics = time.monotonic()
                yield _sse(message, message["seq"])
            if replay: current_cursor = replay[-1][0].i
            interval = max(0.25, int(os.getenv("STREAM_POLL_MS", "1000")) / 1000)
            while time.time() < expires:
                if await request.is_disconnected(): return
                batch = _ledger_batch(current_cursor, 201)
                if len(batch) > 200:
                    yield _sse({"type": "resync"}, event_name="change")
                    return
                for row, order in batch:
                    current_cursor = row.i
                    message = events.project_joined([(row, order)], user)
                    if message:
                        if user["role"] == "officer":
                            if time.monotonic() - last_metrics < 10: continue
                            last_metrics = time.monotonic()
                        yield _sse(message[0], row.i)
                if time.monotonic() - last_heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(interval)
        finally:
            with _stream_lock:
                remaining = _stream_users.get(user["id"], 1) - 1
                if remaining > 0: _stream_users[user["id"]] = remaining
                else: _stream_users.pop(user["id"], None)

    return StreamingResponse(updates(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ---- buyer ----
B = Depends(auth.require("buyer"))
@app.get("/api/products")
def products(): return svc.products()
@app.get("/api/buyer/groups")
def b_groups(u=B): return svc.groups()
@app.get("/api/buyer/products")
def b_products(u=B): return svc.buyer_products(u)
@app.get("/api/buyer/home")
def b_home(u=B): return svc.buyer_home(u)
@app.get("/api/buyer/last-order")
def b_last_order(u=B): return svc.last_order(u)
@app.post("/api/buyer/group")
def b_set_group(b: dict, u=B): return svc.set_preferred_group(u, b.get("group_id"))
@app.post("/api/buyer/orders")
def b_submit(b: dict, u=B): return svc.submit_order(u, int(b.get("group_id", 0)), str(b.get("text", ""))[:1000])
@app.post("/api/buyer/orders/form")
def b_submit_form(b: dict, u=B): return svc.buyer_submit_order(u, b.get("group_id"), b)
@app.get("/api/buyer/orders")
def b_orders(u=B): return svc.my_orders(u)
@app.post("/api/buyer/orders/{oid}/accept")
def b_accept(oid: int, u=B): return svc.accept_quote(u, oid)

@app.post("/api/buyer/orders/{oid}/signoff")
def b_signoff(oid: int, b: dict, u=B):
    return svc.signoff(u, oid, b.get("received"), b.get("note", ""), bool(b.get("final")),
                       b.get("rating"), b.get("rating_note", ""))
@app.post("/api/buyer/orders/{oid}/rating")
def b_rate(oid: int, b: dict, u=B): return svc.rate_buyer_group(u, oid, b.get("stars"), b.get("comment", ""))
@app.post("/api/buyer/orders/{oid}/cancel")
def b_cancel(oid: int, b: dict, u=B): return svc.cancel(u, oid, b.get("reason", ""))
@app.get("/api/buyer/orders/{oid}/delivery/{i}/photo")
def b_dphoto(oid: int, i: int, u=B): return _img(svc.delivery_photo_get(u, oid, i))

# ---- leader ----
L = Depends(auth.require("leader"))
@app.get("/api/leader/orders")
def l_orders(u=L): return svc.my_orders(u)
@app.get("/api/leader/orders/{oid}/review")
def l_review(oid: int, u=L): return svc.review(u, oid)
@app.get("/api/leader/overview")
def l_overview(summary: bool = False, u=L): return svc.leader_overview(u, summary_only=summary)
@app.get("/api/leader/orders/{oid}")
def l_order_detail(oid: int, u=L): return svc.leader_order_detail(u, oid)
@app.get("/api/leader/orders/{oid}/notes")
def l_order_notes(oid: int, u=L): return svc.leader_get_order_notes(u, oid)
@app.post("/api/leader/orders/{oid}/notes")
def l_add_order_note(oid: int, b: dict, u=L): return svc.leader_add_order_note(u, oid, b.get("note", ""))
@app.post("/api/leader/orders/{oid}/buyer-payment")
def l_buyer_payment(oid: int, b: dict, u=L):
    return svc.leader_record_buyer_payment(u, oid, b.get("status"), b.get("amount"), b.get("note", ""))
@app.get("/api/leader/notices")
def l_notices(u=L): return svc.leader_notices(u)
@app.post("/api/leader/notices")
def l_send_notice(b: dict, u=L):
    member_id = b.get("member_id")
    return svc.leader_broadcast(u, b.get("message", ""), member_id if member_id not in ("", None) else None)
@app.get("/api/leader/leaves")
def l_leaves(u=L): return svc.leader_leave_requests(u)
@app.post("/api/leader/leaves/{rid}/resolve")
def l_resolve_leave(rid: int, b: dict, u=L): return svc.leader_resolve_leave(u, rid, b.get("status"), b.get("note", ""))
@app.post("/api/leader/pricing/calculate")
def l_price_calculate(b: dict, u=L): return svc.leader_price_calculate(u, b.get("product"), b.get("qty"), b.get("offered"))
@app.get("/api/leader/team")
def l_team(u=L): return svc.leader_team(u)
@app.get("/api/leader/alerts")
def l_alerts(u=L): return svc.leader_alerts(u)
@app.get("/api/leader/buyers")
def l_buyers(u=L): return svc.leader_buyers(u)
@app.get("/api/leader/buyers/{buyer_id}/contact")
def l_buyer_contact(buyer_id: int, u=L): return svc.leader_buyer_contact(u, buyer_id)
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

# ---- seller (SHG member) ----
M = Depends(auth.require("seller"))
@app.get("/api/seller/ratecards")
def s_rc(u=M): return svc.ratecards_list(u)
@app.post("/api/seller/ratecards/{cid}/approve")
def s_rc_ok(cid: int, u=M): return svc.approve_ratecard(u, cid)
@app.get("/api/seller/tasks")
def s_tasks(u=M): return svc.tasks(u)
@app.get("/api/seller/home")
def s_home(u=M): return svc.seller_home(u)
@app.get("/api/seller/earnings")
def s_earn(u=M): return svc.earnings(u)
@app.post("/api/seller/orders/{oid}/done")
def s_done(oid: int, b: dict, u=M): return svc.report_done(u, oid, b.get("n"), b.get("stage"))
@app.post("/api/seller/orders/{oid}/sample")
def s_sample(oid: int, b: dict, u=M): return svc.submit_sample(u, oid, str(b.get("note", ""))[:200], b.get("photo"))
@app.get("/api/seller/orders/{oid}/checklist")
def s_checklist(oid: int, u=M): return svc.get_checklist(u, oid)
@app.post("/api/seller/orders/{oid}/accept")
def s_accept(oid: int, u=M): return svc.accept_task(u, oid)
@app.post("/api/seller/orders/{oid}/decline")
def s_decline(oid: int, b: dict = Body(default={}), u=M): return svc.decline(u, oid, b.get("reason", ""))
@app.post("/api/seller/orders/{oid}/issue")
def s_issue(oid: int, b: dict, u=M): return svc.report_issue(u, oid, b.get("text", ""))
@app.get("/api/seller/notices")
def s_notices(u=M): return svc.member_notices(u)
@app.post("/api/seller/notices/{nid}/seen")
def s_notice_seen(nid: int, u=M): return svc.member_ack_notice(u, nid)
@app.post("/api/seller/leave")
def s_leave(b: dict, u=M): return svc.member_request_leave(u, b.get("days"), b.get("reason", ""))
@app.post("/api/seller/concern")
def s_concern(b: dict, u=M): return svc.member_raise_concern(u, b.get("message", ""))
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
@app.get("/api/officer/concerns")
def o_concerns(u=Depends(auth.require("officer"))): return svc.officer_member_concerns(u)
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
def _twilio_ok(request, form):
    tok = os.getenv("TWILIO_AUTH_TOKEN")
    if not tok: return DEV                                  # production refuses unsigned webhooks
    from twilio.request_validator import RequestValidator
    return RequestValidator(tok).validate(os.getenv("PUBLIC_URL", "") + "/whatsapp", dict(form), request.headers.get("X-Twilio-Signature", ""))
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
