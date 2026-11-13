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

The current leader dashboard has an Overview and Orders tab, a role-scoped decision queue, deadline/health indicators, order details, and ledger-backed server-sent updates with a polling fallback. Member, buyer, and government dashboards retain their existing workflows; their tabbed/live upgrades are planned in later dashboard phases.

The pre-change architecture and verified behavior are in [docs/DASHBOARDS_CURRENT.md](docs/DASHBOARDS_CURRENT.md). The WhatsApp chatbot baseline is in [docs/CHATBOT_CURRENT.md](docs/CHATBOT_CURRENT.md).

## Tests

Install development dependencies and run:

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m pytest -q
```
