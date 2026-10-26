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

Chat commands: buyer `ACCEPT n`, `STATUS`; leader `QUOTE n [price]`, `PLAN n`, `START n`, `PASS n name`, `REWORK n name pieces reason`, `RISK n [days left]`, `PROGRESS n`, `PAYOUT n [name rework-count]`, `APPROVE n`, `RESOLVE n note`, `ORDERS`; member `TASKS`, `DONE n pieces`, `SAMPLE n` (or send a photo), `DECLINE n`, `EARNINGS` (or ask in Hindi), `APPROVE n`, `DISPUTE n reason`; anyone `LANG 1/2/3`.

## Tests
    cd backend && python -m pytest -q      # 24 tests; use fictional fixtures that exist only in test code

## Production
Set the variables in `.env.example`, deploy with the `Dockerfile` or `Procfile`. Read `SECURITY.md` first: it lists what is NOT done yet.
Honest limits: no AI vision verdict on photos; deadline risk is a transparent rule on declared capacity, not a prediction.
