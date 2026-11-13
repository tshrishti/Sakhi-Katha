# Current chatbot flow

## Entry points and state

Twilio posts WhatsApp messages to `/whatsapp` in `backend/main.py`. The webhook validates Twilio's signature in production, retrieves supported images, PDFs, or voice notes, and calls `bot.handle(sender, text, msg_id, media)`. `backend/voice.py` transcribes voice with Groq Whisper; `backend/llm.py` extracts photo text and order details. Unsupported media and unreadable content receive explicit replies. `bot.py` normalizes the sender, looks up an active database user (the database supplies the role), handles language and owner-role commands, and offers the message to `convo.handle` before its older command handlers. `MessageSid` deduplication is currently held in the process-local `bot.SEEN` set.

`convo.py` persists guided-flow steps in `db.Convo` (SQLite/Postgres JSON data and timestamp). Its two-hour in-memory-configured TTL discards stale state. The step handlers are `menu`, `buyer_name`, `details`, `missing`, `confirm`, `edit`, `after_submit`, `quote_price`, `check_pick`, `review_pick`, `review_act`, `resolve_note`, `work_menu`, `work_order`, `work_pieces`, `work_sample`, `work_issue`, `earn_act`, and `earn_reason`. Free-text and numbered-choice classifications are in `FREE` and `ESCAPE`. `STOP` currently clears the whole flow; typed role commands are allowed as a fallback in selected steps.

## Role flows and service calls

- **Buyer:** the guided flow gathers a buyer/business name, parses an order, asks for missing fields, and requires confirmation before calling `svc.submit_order`. It currently chooses `svc.groups()[0]`. The check flow uses `svc.my_orders`; direct commands also call `svc.accept_quote`, `svc.signoff`, and `svc.cancel`.
- **Leader:** order review calls `svc.my_orders` and `svc.review`; actions call `svc.quote`, `svc.accept_quote`, `svc.plan`, `svc.start`, `svc.risk`, `svc.propose_payout`, `svc.approve_payout`, and `svc.resolve_dispute`. Checklist, quality, delivery, blocking, and cancellation commands use the corresponding `svc` functions.
- **Member/seller:** tasks and earnings use `svc.tasks` and `svc.earnings`; task actions call `svc.accept_task`, `svc.decline`, `svc.report_done`, `svc.submit_sample`, and `svc.report_issue`; pay approval and disputes use `svc.approve_payout` and `svc.dispute`. `svc.py` owns role/group/order authorization, business rules, ledger writes, and notifications for both the bot and REST dashboards.
- Self-registration in `bot._onboard` is process-local and currently asks for language, name, role, and (for members) skill. `svc.register_self` creates the user immediately and assigns a member to the first group; there is no group-code or leader-approval step.

The conversational order path accumulates text/transcripts/OCR, invokes `llm.extract_details`, and asks for product, quantity, offered price, and delivery days. `llm.py` uses Groq when configured and falls back to `engine.parse_order`; `defaults.py` and `ratecards.py` define known products and their pricing templates. The current guided product-missing step accepts arbitrary text. Existing buyers are asked for a buyer name again.

## Localization and outbound messages

`i18n.py` contains English, Hindi, and Hinglish prompts and member-facing status, task, earnings, and reminder strings. The bot stores the selected language on `db.User.lang`; English remains the default for most guided-flow and leader/buyer replies. Kannada is not currently supported. `svc._tell` and several reminder paths send already-rendered English strings; other reminder messages use the recipient's stored language. Consent is currently recorded by `svc.consent` or at member registration, but WhatsApp registration treats continuing as consent rather than asking explicitly.

## Persistence, safety, and tests

`db.py` defines users, groups, orders, ledger events, OTPs, guided `convos`, and rate cards. Startup `bootstrap.ensure()` creates tables and a configured real group/owner. `svc.py` enforces authorization and uses the append-only ledger; buyers receive a restricted order view. `backend/test_app.py`, `backend/test_engine.py`, and `backend/conftest.py` provide the test suite and a test-only seeded world. Groq calls are replaced with fakes in guided-flow tests. The webhook signature is mandatory when a Twilio token is configured outside development.

Current reliability gaps relevant to the upgrade: weak rule-based multilingual order parsing, no product validation, silent first-group routing, immediate member self-registration, two-hour conversation expiry without resumable state, process-local onboarding/idempotency state, long generic welcome/help text, and no Kannada catalogue. These are implementation targets; this file records the pre-upgrade behavior.
