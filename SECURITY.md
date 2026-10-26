# Security and governance notes (what exists, what is still needed)

## Built in
- Login: phone + one-time code (hashed, 5-minute expiry, single use, 5 attempts, 5 codes/hour). Unknown numbers get the same reply (no account enumeration).
- Session: signed, HttpOnly, SameSite=Lax cookie, Secure outside dev, 8-hour expiry. App refuses to start in prod without SECRET_KEY.
- Authorisation on the server for every route and every WhatsApp command: buyer = own orders only and no costs/plans/pay; seller = own tasks and earnings only; leader = own group; officer = aggregates only (groups with fewer than 3 orders suppressed, no names/phones in CSV).
- Money: exact Decimal code; the LLM never computes or commits money. Payout needs leader + 2 members; an open dispute blocks settlement.
- Audit: append-only, hash-chained ledger in the database; verify button for leaders and officers. (It detects edits; it cannot prove the original event was true.)
- Consent recorded at first login. No demo logins or simulated-chat endpoints exist. Webhook signature verified in prod. Security headers, API docs off in prod, no secrets in code, unknown WhatsApp numbers get nothing.
- Order text is treated as untrusted input for the LLM, and every HTML dashboard escapes user content.

## Needed before a real government pilot (not done, do not claim)
- Independent security review / penetration test; rate limiting at the gateway; central logging and monitoring; backup and restore drills.
- Legal review against India's DPDP Act 2023 (notice, purposes, retention, deletion, grievance officer, children's data) and any state rules.
- Hosting decision (data residency, government-approved cloud if the programme requires it); managed Postgres with encryption at rest.
- Accessibility and language review (government web guidelines); Kannada/Hindi UI strings checked by native speakers.
- Token revocation (sessions are stateless), CSRF tokens if cookies are ever allowed cross-site, per-group rate cards, officer hierarchy (block/district scoping), WhatsApp Business API production number and approved templates.
- Register real, consenting people through an authorised coordinator; turn OPEN_REGISTRATION off.
