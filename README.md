# Sakhi Khata

WhatsApp (Twilio) and the web dashboards share one backend and one database, so anything done on WhatsApp shows up on the dashboards (within ~8 seconds, or on refresh) and the other way round.

- `/` landing page, `/app/` -> login, then your role's dashboard (buyer, leader, member, officer). Login = phone + one-time code.
- `/whatsapp` is the Twilio webhook. Send HELP on WhatsApp.
- `archive/old-prototype/` is the old offline mock-up (fake data and fake logins). It is NOT served and is not connected to anything.

Run locally (Windows PowerShell): `cd backend; pip install -r requirements.txt; $env:ENV="dev"; python -m uvicorn main:app --port 8000`
Tests: `cd backend; $env:ENV="dev"; python -m pytest -q`

---

## Leader hub: ten tabs, one shared workflow

The leader dashboard (`frontend/app/leader.html`) has ten tabs: Overview, Orders, Team, Quality, Delivery, Payouts, Buyers, Pricing, Materials, Alerts. They are **views of one workflow**, not ten separate screens:

| File | Role |
|---|---|
| `backend/workflow.py` | The single definition of the order lifecycle: what is pending on an order, who owns it, how urgent, which tab handles it, which WhatsApp command does the same thing, and which tabs each ledger event affects. Pure functions, no database. |
| `backend/hub.py` | One read-model (`Ctx`). Every tab, the alerts bell, the tab badges, the order drawer and WhatsApp `TODAY` are projections of it. |
| `backend/ops.py` | New leader writes: place an order for a buyer, record buyer payments, private notes, move pieces between members, resolve member issues, message members, manage members/buyers, materials, buyer rating. |
| `backend/money.py`, `backend/fairness.py` | Money owed *by* the buyer; fair-work history the planner uses for rotation. |

**WhatsApp to dashboard, live.** Every change (a member's `DONE`, a sample photo, a payment) is appended to the hash-chained ledger. The dashboard polls `/api/leader/events?after=<cursor>` every 4s and re-reads only the tabs that event touches. Changes made on the dashboard show up in WhatsApp (`TODAY`, `TEAM`, `DUE`) the same way.

**New WhatsApp commands.** Leader: `TODAY`, `PAID <id> <rupees>`, `REMIND <id>`, `DUE`, `NOTE <id> <text>`, `TEAM`, `MSG <name|all> <text>`, `REASSIGN <id> <from> <to> <pieces> [stage]`, `FIXED <id> [note]`, `STOCK`. Member: `ISSUE <id> <text>`. Buyer: `RATE <id> <1-5>`.

**Bug fixed.** Typed `READY`, `DISPATCH`, `BLOCK`, `UNBLOCK`, `CANCEL` (leader) and `RECEIVED`, `CANCEL` (buyer) previously showed the menu instead of running, because the guided-conversation layer did not recognise them.

**Honest limits.**
- Message delivery status (sent/delivered/read) needs Twilio credentials and `PUBLIC_URL` so Twilio can call `/whatsapp/status`. Without them messages show "not connected" rather than pretending to be sent.
- UI translation (English / Hindi / Hinglish) covers navigation, headings, KPIs and status labels; longer explanatory text is English.
- "Pieces finished" counts pieces that have passed **every** stage; a day with only cutting reported shows 0.
- Margin and "can we take this order" are labelled estimates.
- Voice notes for placing an order work over WhatsApp; the web form takes a photo.
- AI quality pre-check still needs `GROQ_API_KEY`; without it samples show "AI check unavailable" and you decide manually.
- The buyer and member dashboards were only lightly touched (the API supports buyer rating and member issue reports; their pages do not have buttons for them yet, WhatsApp does).

Tests: `cd backend && ENV=dev python -m pytest -q` (80 tests: the original 55 plus 25 for the hub).
