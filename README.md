# Sakhi Khata - fair, talking ledger for Self-Help Groups

Order -> price check -> fair allocation -> quality gate -> deadline rescue -> payout -> tamper-evident ledger.
**Rule:** the AI suggests and explains; the group decides. All money maths is exact Decimal code, never the LLM.

## Architecture

    WhatsApp + Twilio   (real messages, voice notes, photos)
            |
    Sakhi Khata backend (AI interpretation, pricing, allocation, quality workflow, permissions, ledger)
            |                                   |
    Member WhatsApp                      Web dashboards
    (tasks, updates, earnings, disputes) (orders, approvals, progress, payouts)

No simulated chat, no one-click logins, no fictional people. Everything you see comes from real WhatsApp messages.

## Test it with your own number (9315168344)

1. **Fill `.env`**: `GROQ_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` (the same values MaaSakhi uses on Railway). `OWNER_PHONE` is already set.
2. **Start**: double-click `start.bat` (Windows) or run `./start.sh`. Server on http://localhost:8000.
3. **Expose it**: `ngrok http 8000`, copy the https URL into `PUBLIC_URL` in `.env`, restart the server.
4. **Twilio**: Console -> Messaging -> Try it out -> WhatsApp sandbox -> "When a message comes in" = `https://<your-ngrok>/whatsapp` (POST). Join the sandbox from 9315168344 with your `join <phrase>` message.
   The sandbox allows ONE webhook URL per Twilio account: while testing this, MaaSakhi's webhook must be pointed elsewhere (or use a second Twilio account).
5. **WhatsApp**: you start as the group **leader**. Send `HELP`. One phone can play every role: send `ROLE buyer`, `ROLE leader`, `ROLE member`, `ROLE officer` to switch (owner number only).
   Typical run: `ROLE buyer` -> "200 bags, blue, Rs 60 each, 5 days" -> `ROLE leader` -> `QUOTE 1` -> `ROLE buyer` -> `ACCEPT 1` -> `ROLE leader` -> `PLAN 1`, `START 1` -> `ROLE member` -> `TASKS`, `DONE 1 20`.
6. **Dashboard**: open `<PUBLIC_URL>/app/login.html`, enter 9315168344, the one-time code arrives on WhatsApp (message the sandbox in the last 24h first). You land on the dashboard of whatever role you currently are.
   Voice notes (Hindi/English) and photos work on WhatsApp once the Groq key is set.

Other testers: with `OPEN_REGISTRATION=1`, any number that joined the sandbox and messages the bot registers itself (language -> name -> buyer/member). Set it to 0 once real members are added by the leader from the dashboard.

## Roles and dashboards
| Role | Who | Sees | Page |
|---|---|---|---|
| buyer | places bulk orders | own orders, quote, status. No costs, members or pay | /app/buyer.html |
| leader | SHG group leader | whole group: quote, plan, start, payout, disputes, members | /app/leader.html |
| seller | SHG member | own tasks, own earnings with explanation, approve/dispute | /app/seller.html |
| officer | mission / federation | aggregates only, CSV export, ledger check | /app/officer.html |

Chat commands: buyer `ACCEPT n`, `STATUS`; leader `QUOTE n [price]`, `PLAN n`, `START n`, `PASS n name`, `REWORK n name pieces reason`, `RISK n [days left]`, `PROGRESS n`, `CHECKLIST n`, `PAYOUT n [name rework-count]`, `APPROVE n`, `RESOLVE n note`, `ORDERS`; member `TASKS`, `DONE n pieces`, `SAMPLE n` (or send a photo), `CHECKLIST n`, `DECLINE n`, `EARNINGS` (or ask in Hindi), `APPROVE n`, `DISPUTE n reason`; anyone `LANG 1/2/3`.

## AI quality screening (leader decides)
1. **Buyer-specification checklist.** Built from the order (product, size, colour, specs, packaging) plus basic workmanship checks (even stitching, loose threads, stains, even colour). The leader edits it on the dashboard (*Buyer's quality checklist*) or on WhatsApp (`CHECKLIST <order>`, `CHECKLIST <order> SET point one; point two`). Members can read it too.
2. **Pre-screen.** When a member sends a sample photo (WhatsApp or the dashboard's *Send first sample (photo)* button), a Groq vision model (`GROQ_VISION_MODEL`) checks it against the checklist and returns, per criterion: *met / possible defect / cannot tell*, a **confidence** (high / medium / low) and a short note.
3. **The leader decides.** Flags are shown to the leader on the dashboard and in the WhatsApp alert, most confident first. The AI never passes, fails or reworks a sample, and members and buyers never see its output. If the AI is unavailable, the answer is malformed or there is no photo, the sample still goes to the leader with a plain notice (dashboard *Try again* re-runs the check).
4. **Audit.** The ledger records `quality.ai_screened` (status, flag count, model) and the leader's `quality.decided` with the AI flag count beside it; the checklist used is stored with each screening.
Safeguards: invented criteria are dropped, a flag with no reason is ignored, missing criteria show as "not assessed", photo text and member notes are treated as untrusted. Without `GROQ_API_KEY` the feature degrades to "AI screening is not switched on".

## Tests
    cd backend && python -m pytest -q      # 44 tests; use fictional fixtures that exist only in test code

## Deploy on Render
1. Push this folder to a GitHub repo (`.env` and `*.db` are git-ignored; never commit keys).
2. Render dashboard -> **New + -> Blueprint** -> select the repo. `render.yaml` creates the web service (Docker) and a Postgres database and links them. `SECRET_KEY` and `CRON_SECRET` are generated for you.
3. When asked, fill `GROQ_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `OWNER_PHONE`, `SANDBOX_JOIN_PHRASE`. Deploy.
4. Twilio Console -> WhatsApp sandbox -> "When a message comes in" = `https://<your-service>.onrender.com/whatsapp` (POST). `PUBLIC_URL` is picked up from Render automatically and must match this URL exactly (set it manually if you use a custom domain).
5. Check `https://<your-service>.onrender.com/readyz` returns `{"ok":true,"db":"up"}`, then open `/app/login.html`.

Render notes:
- `/healthz` = process alive, `/readyz` = database reachable (used as the Render health check).
- Reminders and escalations run inside the app every 15 minutes (`ENABLE_SCHEDULER`, `SCHEDULER_MINUTES`). Prefer an external cron? Set `ENABLE_SCHEDULER=0` and run `python backend/cron_ping.py` from a Render Cron Job, or POST `/api/cron/reminders` with header `X-Cron-Secret`.
- Keep ONE instance (chat state and the scheduler are in-process). Free web instances sleep and make Twilio time out; use Starter or above. The free Postgres plan expires after 30 days, so the blueprint uses a paid one.
- Local dev: `pip install -r backend/requirements-dev.txt`, then `cd backend && python -m pytest -q`.

## Production
Set the variables in `.env.example`, deploy with the `Dockerfile` or `Procfile`. Read `SECURITY.md` first: it lists what is NOT done yet.
Honest limits: the AI only flags possible defects on photos (it never passes or fails a sample and can miss things); deadline risk is a transparent rule on declared capacity, not a prediction.


## Rate cards and product templates
- `backend/defaults.py` holds product templates (stages, rates, cost inputs, unit words). Shipped: cloth bag and cotton kurta. Add a product by adding one entry.
- Each group can have its own versioned rate card per product (`rate_cards` table). The leader proposes; the leader plus `RATECARD_MEMBER_QUORUM` members (default 2) approve; the previous version becomes `superseded`. All steps are written to the audit ledger.
- An order is priced on the group's active card (or the template default) when it is quoted, and keeps that card (`rate_card` snapshot) for planning and payout.
- API: `GET /api/products`, `GET|POST /api/leader/ratecards`, `POST /api/leader/ratecards/{id}/approve|reject`, `GET /api/seller/ratecards`, `POST /api/seller/ratecards/{id}/approve`.

## Delivery and acceptance
Order flow now continues past settlement: `settled` → `ready_for_delivery` (leader's quantity check of packed pieces) → `dispatched` (courier, tracking, date; delivery photo optional) → buyer sign-off with pieces received → `closed`. A short delivery gives `partially_delivered`, and the leader packs and sends the rest (or the buyer accepts the shortfall and closes). `blocked` (leader, with a reason; unblock returns to the previous state) and `cancelled` (buyer or leader, only before production starts) are side exits. Every step is written to the hash-chained ledger. WhatsApp: leader `READY / DISPATCH / BLOCK / UNBLOCK / CANCEL`, buyer `RECEIVED <id> <pieces> [FINAL]` and `CANCEL <id>`.


## Reminder engine

One scheduled endpoint, `POST /api/cron/reminders` (header `X-Cron-Secret`), runs every reminder type and returns counts:

| Type | Who is told | When |
|---|---|---|
| `task_due` | Member (their language) | Unfinished task, delivery date within `TASK_REMIND_DAYS` or overdue |
| `deadline_risk` | Leader | Pieces finished + slowest stage's daily capacity x days left < pieces needed |
| `sample_review` | Leader | Sample waiting over `SAMPLE_NUDGE_MINUTES` (before the officer escalation) |
| `payout_approval` | Leader and members who have not approved | Payout pending over `PAYOUT_NUDGE_HOURS` |
| `escalated` | Mission officer | Existing escalation, now run from the same call |

Days left is counted from the real delivery date saved when the leader starts work (`due_at`), not typed in. A typed value in the risk check is only a what-if override. Each reminder repeats at most once per `REMINDER_COOLDOWN_HOURS`, so the endpoint is safe to call often. `/api/cron/escalate` still works.
