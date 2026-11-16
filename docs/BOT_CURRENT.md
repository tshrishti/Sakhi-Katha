# WhatsApp bot: current behavior

Twilio delivers inbound WhatsApp messages to `/whatsapp` in [backend/main.py](../backend/main.py). The webhook validates signatures when configured, fetches supported media, transcribes voice notes through [backend/voice.py](../backend/voice.py), and calls `bot.handle(sender, text, msg_id, media)`. `backend/bot.py` normalizes the phone number and looks up the active user's role in `db.User`; message text never selects a role. Unknown numbers are rejected unless `OPEN_REGISTRATION=1`.

## Flows and persistence

`convo.handle` runs before the legacy typed-command handlers. It stores guided-flow state in `db.ChatState` (and can read older `db.Convo` records), uses `CHAT_TTL_MIN` with a 30-minute default, and has steps for menu, buyer name/order details/missing fields/confirmation, group selection, quote and leader review, task work, earnings, and expiry/cancel confirmation. The language/name/role/consent/skill/group-code onboarding steps now also live in `db.ChatState`, survive process restarts, and use the same 30-minute expiry. The `MessageSid` duplicate cache (`bot.SEEN`) remains process-local.

Buyers can place an order through the guided text/media flow, choose a group, review the parsed order and explicitly confirm; typed commands cover status, quote acceptance, delivery sign-off, cancellation, ratings, and group choice. Leaders can create on behalf of a buyer or use their review flow, then quote, plan/start work, review samples, manage payouts, delivery, disputes, notices, and leave requests. Members can view tasks and earnings, accept/decline tasks, report completed pieces, send samples, report issues, approve pay, raise disputes, view notices, and request leave. New buyer registration now requires explicit consent before `svc.register_self` records it. Member registration requires separate consent, a valid group invite, and leader approval; the consent timestamp is retained on the pending request and in the ledger. Approval initializes the member's persisted menu state.

All business-changing paths call [backend/svc.py](../backend/svc.py), which enforces role/group authorization, updates the shared database, appends ledger events, and sends notifications. Dashboard APIs call the same service; `/api/stream` projects ledger updates to authorized dashboards. The bot's guided steps and typed fallback are therefore one of the ways into the same service layer, not a second business-rules implementation.

## Localization and messages

[backend/i18n.py](../backend/i18n.py) defines `en`, `hi`, and `hing`; `LANGS` accepts numeric and word aliases. Registration consent, onboarding prompts, buyer/member/leader menus, and the buyer's order confirmation now use that catalogue. New-order notifications are rendered in each leader recipient's saved language and direct leaders to the existing new-orders menu. `HELP` returns the localized role menu rather than a command dump. Kannada is not implemented, and Hindi/Hinglish copy needs native-speaker review. Legacy order, task, and leader action prompts still contain hardcoded English and are scheduled for the role-flow phase; other outbound notifications are not yet consistently localized.

## Safety and verification notes

The LLM in [backend/llm.py](../backend/llm.py) extracts order details and reads image text; deterministic calculations and authorization stay in `engine.py` and `svc.py`. The member-facing bot does not expose buyer-only or leader-only order views because `svc.my_orders` returns role-scoped data. Existing tests exercise bot registration, guided order confirmation, group joins, role commands, notifications, ledger integrity, dashboard APIs, and selected live-event projections. Run them with `python -m pytest -q`.

This is a current-state inventory, not an endorsement of the copy. Remaining gaps include hardcoded English in legacy role-action flows, incomplete localization of outbound notifications, process-local message idempotency, and conversational steps that still require typing order IDs or command words.
