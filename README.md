# Sakhi Khata

Sakhi Khata helps self-help groups receive buyer orders, plan work, track member-reported progress, review quality, and approve fair payouts. The FastAPI backend and WhatsApp bot share the same service rules and database. Static role dashboards live in `frontend/app/`.

## Run locally

1. Copy `.env.example` to `.env` and set a development `SECRET_KEY`. Add WhatsApp/Twilio credentials only if you need outbound messaging.
2. Start the server:

   ```powershell
   .\start.bat
   ```

   Or from `backend/`, install `requirements.txt` and run `python -m uvicorn main:app --reload`.
3. Open `http://localhost:8000/app/login.html`. Sign in with a registered phone number and the one-time code sent through the configured notification channel. There are no demo logins in the running app.

The default database is local SQLite. For a persistent deployment, configure `DATABASE_URL` for PostgreSQL and use a production `SECRET_KEY`, `PUBLIC_URL`, and verified Twilio webhook settings. See `.env.example` and `SECURITY.md`.

## Dashboards

The root URL now opens the real sign-in flow; it no longer serves the disconnected mock dashboard. The role-based dashboards use the same backend and database as WhatsApp.

The leader dashboard has Overview, Orders, Team, Quality, Delivery, Payouts, Buyers, Pricing, and Alerts tabs. Members have Home, My tasks, Earnings, Notices, and Me; buyers have Home, Orders, New order, Group, and Me. The mission-officer page provides group aggregates and a private member-concerns inbox. Each dashboard uses the same backend rules as WhatsApp and refreshes from role-projected live events, with polling as a fallback. WhatsApp dashboard links require a Continue click and expire after the configured `MAGIC_LINK_TTL_MIN`.

The officer page is currently aggregate reporting plus the private concern inbox; the full hierarchical government dashboard and scoped user provisioning are not implemented. See [docs/DASHBOARDS_CURRENT.md](docs/DASHBOARDS_CURRENT.md) for role scope, privacy boundaries, and remaining gaps.

The dashboard behavior and API overview are in [docs/DASHBOARDS_CURRENT.md](docs/DASHBOARDS_CURRENT.md). The WhatsApp chatbot baseline is in [docs/CHATBOT_CURRENT.md](docs/CHATBOT_CURRENT.md).

## Tests

Install development dependencies and run:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m pytest -q
```
