# Sakhi Khata dashboards: current state

## Pages and sign-in

The static role dashboards live under `frontend/app/`: `buyer.html`,
`leader.html`, `seller.html` (member), and `officer.html`. `/` redirects to the
real sign-in flow; the archived prototype is not a live data source. Sign-in is
phone plus a WhatsApp one-time code. The optional WhatsApp dashboard link is
single-use, expires after `MAGIC_LINK_TTL_MIN` (10 minutes by default), and
requires an explicit Continue click before the server consumes it.

The dashboards use a shared dark, responsive theme, role-protected same-origin
JSON API calls, an HttpOnly session cookie, language preference (English,
Hindi, or Hinglish), and a consent prompt. The leader, member, and buyer pages
use tabs. The mission-officer page remains an aggregate report with a
private-member-concerns inbox.

## Live updates and shared rules

`backend/svc.py` is the shared business-rule layer used by the dashboard routes
and WhatsApp bot. State changes append to the existing hash-chained ledger;
the sequence, payload, and hash fields are not changed by dashboard rendering.
Role-projected server-sent events notify connected dashboards, with bounded
polling as a fallback. The member, buyer, and leader pages reload the affected
views after relevant events and skip replacing content while the user is
typing.

Dashboard actions use the same service functions and database as WhatsApp.
Leader messages, leave requests, member concern reports, buyer orders and
ratings, payment records, and magic-link issuance are persisted and audited.
Outbound WhatsApp message language follows the recipient's saved language.

## Role dashboards

- **Buyer:** Home, Orders, New order, Group, and Me. Buyers can choose a group,
  submit structured orders, repeat a prior order, accept quotes, confirm
  delivery (including a shortfall decision), rate a closed order, and view
  their supplier-group passport. Buyer views do not expose production plans,
  member names, cost floors, or member pay.
- **Group leader:** Overview, Orders, Team, Quality, Delivery, Payouts,
  Buyers, Pricing, and Alerts. Buyer contact is revealed only on explicit
  request and that access is recorded. Order notes are leader-only. Buyer
  payment records are informational; Sakhi Khata does not process payments.
- **Member:** Home, My tasks, Earnings, Notices, and Me. Members see their own
  assignments and earnings, can accept or decline work, report progress,
  submit samples/issues, approve or dispute payouts, acknowledge leader
  messages, request leave, and send a private concern to mission officers.
- **Mission officer:** Aggregate group summary and CSV export, ledger
  verification, and the private member-concerns inbox. Concern details are
  unavailable to group leaders. The broader hierarchical government dashboard
  and scoped provisioning are not implemented.

## API, authorization, and privacy

`backend/main.py` exposes role-scoped endpoints for buyer home/products/groups,
structured orders and ratings; leader team, buyer/contact, alerts, notices,
leaves, pricing calculations, order notes, and buyer payment records; member
home, tasks, earnings, notices, leaves, and private concerns; and officer
aggregates and concerns. Role and group/order scope checks remain in the
service layer. Contact reads write to `access_log`; personal phone numbers are
not included in buyer-list or SSE responses. Private concerns are stored for
officers and omitted from leader event projections.

`backend/db.py` defines notices, leave requests, buyer payments/ratings, private
order notes, one-time magic links, access logs, and member concerns alongside
the existing group, user, order, OTP, and ledger tables. `create_all` creates
the added tables for existing databases.

## Localization and validation

English, Hindi, and Hinglish are currently supported. Kannada translations,
text-to-speech earnings playback, email/password authentication, payment
processing, and the hierarchical government dashboard remain out of scope.
WhatsApp remains supported as the primary channel.

`backend/test_app.py`, `backend/test_engine.py`, and `backend/test_events.py`
cover role access, order and payout rules, bot/dashboard parity, event
projection, buyer privacy, member concerns, and audit-ledger integrity.
Frontend scripts are plain JavaScript with no separate frontend test runner;
validate their syntax with Node and run the backend suite with
`python -m pytest -q`.
