# Sakhi Khata dashboards: current state

## Pages and client behavior

The app is static HTML with vanilla JavaScript under `frontend/app/`: `login.html`,
`buyer.html`, `leader.html`, `seller.html` (the SHG member UI), and `officer.html`.
`common.js` supplies same-origin `/api` requests, JSON bodies, HTML escaping,
toasts, OTP-session bootstrapping, consent, and logout. `style.css` is the shared
dark theme. Authenticated requests use the HttpOnly `sk` cookie; login is phone
plus one-time code. The dashboards are long pages rather than tabs. The checked
`common.js` and page scripts contain no polling timer or `EventSource`: pages
load data when opened or after an action, with no server push or periodic
refresh. Each page renders its main content with `innerHTML`.

The buyer page submits and lists orders, accepts quotes, confirms deliveries,
and cancels eligible orders. The leader page lists group orders and supports
quoting, planning, starting, quality review, payouts, delivery, member
management, and rate cards. The member page lists only that member's tasks and
earnings and supports task acceptance/decline, progress, samples, payout
approval/disputes, and rate-card votes. The officer page shows aggregate group
counts and CSV export. `archive/old-prototype/` is not served; it is not a
source of live application data.

## API routes

`backend/main.py` exposes health/readiness and public configuration; OTP
request/verify/logout, `/api/me`, language, and consent; buyer products,
groups, orders, quote acceptance, sign-off, cancellation, and delivery photos;
leader order reads and quote/plan/start/payout/dispute/quality/risk/checklist/
delivery/block/cancel actions, members, and rate cards; member tasks, earnings,
progress, samples, checklists, task acceptance/decline, payout approval/dispute,
and rate-card approval; officer aggregate summary and CSV export; cron
reminders/escalation; ledger verification; and the Twilio `/whatsapp` webhook.
Dashboard routes require the corresponding role. The shared service layer also
checks order ownership and group scope.

## Services, roles, and persistence

`backend/svc.py` is the shared rule layer used by routes and the bot. Order
submission, group selection, buyer order reads, quote acceptance, and sign-off
are buyer-scoped (leaders can submit on behalf of a buyer where allowed).
Order reads and lifecycle changes are scoped to the buyer or the leader/member's
group. Quote, plan/start, payout proposal/leader approval, dispute resolution,
sample review, checklist editing, risk, delivery, member management, and rate
card proposal/rejection are leader-only. Task/earnings reads, task acceptance
or decline, progress, sample submission, payout approval/dispute, and rate-card
approval are member-scoped. Checklist reads are available to leaders and
members assigned to the order. Consent/language are for the authenticated
account; registration, join-request and role-switch helpers are restricted by
their own onboarding/owner rules. Officer reporting returns aggregates only.
Reminder and escalation routines run as scheduled system work.

The service layer covers group/order lookup, product/rate-card snapshots,
deterministic pricing and allocation, sample screening, payout quorum and
dispute rules, delivery state, join approval, language, and reminder/
escalation helpers. State changes append to the existing hash-chained `ledger`;
its sequence, payload, and hashed fields are the audit source and are not
modified by dashboard rendering.

`backend/db.py` currently defines `groups` (id, name, village, block, district),
`users` (id, phone, name, role, group, skills, capacity, active, consent time,
language, preferred group), `orders` (id, group, buyer, status, JSON data,
created time), `ledger` (sequence/type/actor/order/payload/time/hash chain),
OTP and WhatsApp conversation state, group invites and join requests, and
versioned rate cards. `create_all` creates tables; `ensure_columns` safely adds
the existing `lang` and `preferred_group_id` columns. It does not currently
provide an event stream, notices, leave requests, buyer payment/rating records,
order notes, access log, or magic-link tokens.

## WhatsApp, localization, and tests

`backend/auth.py` verifies OTPs and signs role-bearing HttpOnly sessions.
`backend/notify.py` sends best-effort WhatsApp messages through Twilio.
`backend/bot.py` routes typed commands and inbound media; `backend/convo.py`
stores guided conversation state in `chat_state` with legacy `convos` fallback.
Both call `svc.py`, so dashboard and WhatsApp state changes use the same
database and rules. English, Hindi, and Hinglish are available; the user's
language is persisted in `users.lang` and shared with message rendering.

`backend/test_app.py` exercises OTP/RBAC, role isolation, order/payout and
delivery lifecycles, WhatsApp parity, group/rate-card behavior, and aggregate
privacy. `backend/test_engine.py` covers deterministic pricing, allocation,
payout, parsing, feasibility, and ledger tamper detection. Existing frontend
tests are not present. The old mock-up has no live role or database connection.
