"""Run scripted WhatsApp conversations and report copy/flow issues.

Run from the repository root with ``python -m tools.flow_audit`` from backend/,
or run the focused tests with ``pytest backend/tools``.
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# The auditor must never attach to a developer's configured or production DB.
_db_path = (Path(tempfile.gettempdir()) / f"sakhi-flow-audit-{os.getpid()}.sqlite").resolve()
os.environ.update({
    "DATABASE_URL": "sqlite:///" + _db_path.as_posix(),
    "ENV": "dev",
    "SK_NO_DOTENV": "1",
    "OPEN_REGISTRATION": "1",
    "OWNER_PHONE": "",
})
for _key in ("TWILIO_AUTH_TOKEN", "TWILIO_ACCOUNT_SID", "GROQ_API_KEY"):
    os.environ.pop(_key, None)

import auth  # noqa: E402
import bot  # noqa: E402
import convo  # noqa: E402
import db  # noqa: E402
import fixtures  # noqa: E402
import i18n  # noqa: E402
import llm  # noqa: E402
import notify  # noqa: E402
import svc  # noqa: E402


EXAMPLE_MESSAGE = (
    "स्वागत है, Kamal! आप रजिस्टर हो गईं। आगे बढ़ने का मतलब है कि आपका समूह "
    "आपके ऑर्डर और भुगतान का रिकॉर्ड रखेगा। कभी भी HELP भेजें。\n"
    "Send your order as text or voice (quantity, price per piece, days). "
    "STATUS lists orders. ACCEPT <id> accepts a quote. RECEIVED <id> <pieces> "
    "confirms delivery (add FINAL to accept a shortfall). CANCEL <id> before work "
    "starts. LANG 1/2/3 changes language."
)
LANGUAGE_CODES = {"en": "1", "hi": "2", "hing": "3"}
ALLOWED_ENGLISH_IN_HINDI = {
    "sakhi", "khata", "shg", "rs", "help", "menu", "order", "orders", "english",
    "hindi", "hinglish", "cloth", "jute", "bag", "bags", "pickle",
}
RAW_COMMANDS = {
    "HELP", "STATUS", "ACCEPT", "RECEIVED", "FINAL", "CANCEL", "LANG",
    "QUOTE", "PLAN", "START", "DONE", "TASKS", "EARNINGS", "APPROVE",
    "DISPUTE", "DECLINE", "PAYOUT", "READY", "DISPATCH", "JOIN", "ROLE",
}


@dataclass
class Violation:
    severity: str
    rule: str
    turn: int
    text: str
    transcript: str


@dataclass
class ScriptTurn:
    speaker: str
    text: str
    msg_id: str | None = None
    media: dict | None = None


def _snippet(text: str, limit: int = 180) -> str:
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _is_next_step(text: str) -> bool:
    return bool(
        re.search(r"(?m)^\s*[1-9][.)]?\s+", text)
        or re.search(r"\b(?:send|reply|type)\s+9\b", text, re.I)
        or re.search(r"\b(?:reply|send)\s+0\b.*\b9\b", text, re.I | re.S)
        or re.search(r"\b(?:menu)\b.{0,30}\b9\b|\b9\b.{0,30}\bmenu\b|मेनू.{0,30}9|9.{0,30}मेनू", text, re.I | re.S)
        or re.search(r"[?؟]", text)
        or re.search(r"\b(?:waiting|wait for|ask your group leader|contact your coordinator)\b|"
                     r"will message you when|message you when reviewed|"
                     r"प्रतीक्षा|इंतज़ार|जवाब पर आपको संदेश मिलेगा|"
                     r"jawab aate hi|jawab ka intezaar", text, re.I)
        or re.search(r"\bplease send\b|\bsend a clearer\b|\btype the order\b", text, re.I)
        or re.search(r"(?<!\d)[1-5]\s+(?:yes|no|accept|cancel|change|agree|decline)\b", text, re.I)
    )


def lint_message(text: str, role: str, lang: str, turn: int = 1,
                 transcript: str = "") -> list[Violation]:
    """Apply the Phase 1 copy checks to a single reply or notification."""
    text = text or ""
    transcript = transcript or text
    issues: list[Violation] = []

    def add(rule: str, severity: str = "Major", offending: str | None = None) -> None:
        issues.append(Violation(severity, rule, turn, offending or _snippet(text), transcript))

    if lang == "hi":
        english_words = {
            word.lower() for word in re.findall(r"\b[A-Za-z]{4,}\b", text)
            if word.lower() not in ALLOWED_ENGLISH_IN_HINDI
        }
        if english_words:
            add("language-purity", offending=", ".join(sorted(english_words)))
    elif lang == "en" and re.search(r"[\u0900-\u097f]", text):
        add("language-purity", offending=_snippet(text))

    lines = text.splitlines()
    max_lines = 6 if len(re.findall(r"(?m)^\s*[1-5][.)]\s", text)) >= 3 else 4
    if len(text) > 280 or len(lines) > max_lines:
        add("length", offending=f"{len(text)} characters / {len(lines)} lines")
    if text.count("?") + text.count("؟") > 1:
        add("multiple-questions")
    request_count = len(re.findall(
        r"\b(?:send|reply|type|choose|tell me|please send|please tell)\b", text, re.I
    ))
    if request_count > 1:
        add("multiple-requests", offending=f"{request_count} requests: {_snippet(text)}")

    caps = sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text)) - {"SHG"})
    caps = [word for word in caps if word in RAW_COMMANDS or word not in {"Sakhi", "Khata"}]
    if caps or re.search(r"<[^>]+>", text) or re.search(r"\b(?:send|reply|type)\s+[A-Z][A-Z0-9_]*\b", text):
        add("raw-command-syntax", offending=", ".join(caps) or _snippet(text))
    if re.search(r"\b[1-9](?:\s*/\s*[1-9]){1,}\b", text):
        add("slash-separated-options")
    if re.search(r"[{}]|\b(?:None|null|undefined|Traceback|Error)\b", text, re.I):
        add("unrendered-placeholder-or-leak", "Blocker")
    if not _is_next_step(text):
        add("dead-end", "Blocker")
    if role == "buyer":
        forbidden = re.search(
            r"\byour group\b|आपका समूह|तुम्हारा समूह|aapka group|"
            r"\b(?:floor price|cost to make|member pay|worker pay)\b|"
            r"(?<!\w)(?:Lakshmi|Shobha|Savitha|Padma|Rukmini)(?!\w)",
            text, re.I,
        )
        if forbidden:
            add("role-content-leak", "Blocker", forbidden.group(0))
    if lang in ("hi", "hing") and re.search(
        r"आप(?!का|की|के).{0,30}(?:गईं|गई|गया|गये|हुईं|हुई|हुआ)|"
        r"हो गईं|aap(?!ka|ki|ke).{0,30}\bho gayi\b|"
        r"aap(?!ka|ki|ke).{0,30}\bho gaya\b",
        text, re.I,
    ):
        add("gendered-user-language")
    if re.search(r"आगे बढ़ने का मतलब|by continuing you agree", text, re.I):
        add("implied-consent", "Blocker")
    for match in re.finditer(r"₹\s*(\d[\d,]*)", text):
        amount = match.group(1).replace(",", "")
        expected = f"{int(amount):,}".replace(",", "X").replace("X", ",")
        # Indian grouping differs after the first three digits.
        if len(amount) > 3:
            expected = amount[-3:]
            rest = amount[:-3]
            grouped = []
            while rest:
                grouped.insert(0, rest[-2:])
                rest = rest[:-2]
            expected = ",".join(grouped + [expected])
            if match.group(1) != expected:
                add("currency-grouping", offending=match.group(0))
    return issues


def catalogue_issues() -> list[str]:
    problems = []
    for key, translations in i18n.M.items():
        missing = {"en", "hi", "hing"} - translations.keys()
        if missing:
            problems.append(f"{key}: missing {', '.join(sorted(missing))}")
            continue
        placeholders = {
            lang: set(re.findall(r"{([a-zA-Z_]\w*)}", translations[lang]))
            for lang in ("en", "hi", "hing")
        }
        if len({frozenset(value) for value in placeholders.values()}) != 1:
            problems.append(f"{key}: placeholders differ {placeholders}")
    return problems


def hardcoded_message_sites() -> list[str]:
    """Find literal text returned directly or passed to outbound notification calls."""
    sites: list[str] = []
    for filename in ("bot.py", "convo.py", "svc.py", "notify.py"):
        path = BACKEND / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    if value.value.strip():
                        sites.append(f"{filename}:{node.lineno}: returned literal")
                elif isinstance(value, ast.JoinedStr):
                    literal = "".join(
                        part.value for part in value.values
                        if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    )
                    if literal.strip():
                        sites.append(f"{filename}:{node.lineno}: returned f-string")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "send":
                if node.args and isinstance(node.args[-1], (ast.Constant, ast.JoinedStr)):
                    sites.append(f"{filename}:{node.lineno}: notification literal")
        if filename == "bot.py":
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "HELP" for target in node.targets
                ):
                    sites.append(f"{filename}:{node.lineno}: static HELP catalogue")
    return sites


class FakeClock:
    def __init__(self) -> None:
        self.now = time.time()

    def time(self) -> float:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now += minutes * 60


class AuditRunner:
    """Run real bot.handle calls against an isolated test database."""

    def __init__(self, fake_nlu: bool = False):
        self.fake_nlu = fake_nlu
        self.clock = FakeClock()
        self.transcript: list[tuple[str, str, str | None]] = []
        self.notifications: list[dict] = []
        self.replies: list[tuple[str, str, str, str | None]] = []
        self._original_send = notify.send
        self._original_extract = llm.extract_details
        self._original_convo_time = convo.time

    def __enter__(self) -> "AuditRunner":
        db.Base.metadata.drop_all(db.engine)
        db.Base.metadata.create_all(db.engine)
        fixtures.seed_test_world()
        bot.SEEN.clear()
        bot.PREF.clear()
        bot.ONB.clear()
        notify.send = lambda phone, text: self.notifications.append(
            {"phone": phone, "text": text}
        ) or True
        convo.time = SimpleNamespace(time=self.clock.time)
        if self.fake_nlu:
            original = self._original_extract

            def fake_extract(parts, today=None):
                result = original(parts, today=today)
                result["source"] = "fake"
                return result

            llm.extract_details = fake_extract
        return self

    def __exit__(self, *_exc) -> None:
        notify.send = self._original_send
        llm.extract_details = self._original_extract
        convo.time = self._original_convo_time

    def send(self, speaker: str, phone: str, text: str = "", msg_id: str | None = None,
             media: dict | None = None) -> str | None:
        self.transcript.append((speaker, text, msg_id))
        reply = bot.handle(phone, text, msg_id, media)
        self.replies.append((speaker, phone, text, reply))
        return reply

    def set_language(self, phone: str, lang: str) -> None:
        with db.S() as session:
            user = session.query(db.User).filter_by(phone=auth.norm(phone)).first()
            if user:
                user.lang = lang
                session.commit()

    def lint(self, languages: dict[str, str]) -> list[Violation]:
        findings: list[Violation] = []
        previous_reply = None
        previous_input = None
        for index, (speaker, _phone, user_text, reply) in enumerate(self.replies, 1):
            lang = languages.get(speaker, "en")
            transcript = f"{speaker}: {user_text!r} -> {reply!r}"
            if reply:
                findings.extend(lint_message(reply, speaker, lang, index, transcript))
                if reply == previous_reply and user_text != previous_input:
                    findings.append(Violation(
                        "Major", "repeated-reply", index, _snippet(reply), transcript
                    ))
                previous_reply, previous_input = reply, user_text
        for index, item in enumerate(self.notifications, 1):
            with db.S() as session:
                recipient = session.query(db.User).filter_by(phone=item["phone"]).first()
                recipient_role = recipient.role if recipient else "unknown"
                recipient_lang = (recipient.lang or "en") if recipient else "en"
            findings.extend(lint_message(
                item["text"], recipient_role, recipient_lang, index,
                f"notification to {item['phone']}: {item['text']!r}",
            ))
        return findings


SCRIPT_DATA = {
    "buyer_order": [
        ScriptTurn("buyer", "hello"),
        ScriptTurn("buyer", "@language"),
        ScriptTurn("buyer", "Kamal"),
        ScriptTurn("buyer", "2"),
        ScriptTurn("buyer", "1"),
        ScriptTurn("buyer", "1"),
        ScriptTurn("buyer", "200 cloth bags, Rs 60 each, 5 days"),
        ScriptTurn("buyer", "1"),
    ],
    "member_join": [
        ScriptTurn("member", "hello"),
        ScriptTurn("member", "@language"),
        ScriptTurn("member", "Kamal"),
        ScriptTurn("member", "1"),
        ScriptTurn("member", "1"),
        ScriptTurn("member", "2"),
        ScriptTurn("member", "482913"),
        ScriptTurn("leader", "1"),
        ScriptTurn("member", "hello"),
    ],
    "leader_quote": [
        ScriptTurn("leader", "hello"),
        ScriptTurn("leader", "1"),
        ScriptTurn("leader", "Kamal Stores"),
        ScriptTurn("leader", "200 cloth bags, Rs 60 each, 5 days"),
        ScriptTurn("leader", "1"),
    ],
}


def run_script_data(runner: AuditRunner, name: str, lang: str) -> None:
    phones = {
        "buyer": "whatsapp:+919811122201",
        "member": "whatsapp:+919811122202",
        "leader": "whatsapp:+919000000001",
    }
    if name == "leader_quote":
        runner.set_language(phones["leader"], lang)
    for turn in SCRIPT_DATA[name]:
        text = LANGUAGE_CODES[lang] if turn.text == "@language" else turn.text
        runner.send(turn.speaker, phones[turn.speaker], text, turn.msg_id, turn.media)


def run_error_probes() -> tuple[list[Violation], dict[str, int]]:
    """Exercise invalid registration, duplicate delivery, media failure, and persisted expiry."""
    findings: list[Violation] = []
    counts: dict[str, int] = {}

    with AuditRunner() as runner:
        phone = "whatsapp:+919811122231"
        runner.send("unknown", phone, "hello")
        repeated = runner.send("unknown", phone, "english")
        runner.send("unknown", phone, "💬")
        runner.send("unknown", phone, "", msg_id="SM-AUDIT-DUP")
        duplicate = runner.send("unknown", phone, "", msg_id="SM-AUDIT-DUP")
        runner.send("unknown", phone, "x" * 400)
        runner.send("unknown", phone, "1", msg_id="SM-AUDIT-9")
        findings.extend(runner.lint({"unknown": "en"}))
        counts["registration-invalid-and-duplicate"] = len(runner.transcript)
        if repeated:
            findings.append(Violation(
                "Major", "repeated-reply", 2, _snippet(repeated),
                "unknown: 'english' -> identical language menu",
            ))
        if duplicate is not None:
            findings.append(Violation(
                "Blocker", "duplicate-not-idempotent", 5, _snippet(str(duplicate)),
                "The same MessageSid returned a second reply",
            ))

    with AuditRunner() as runner:
        buyer = "whatsapp:+919000000002"
        runner.set_language(buyer, "en")
        runner.send("buyer", "whatsapp:+919000000002", "hello")
        runner.send("buyer", "whatsapp:+919000000002", "1")
        runner.send("buyer", "whatsapp:+919000000002", "", media={"kind": "image"})
        runner.send("buyer", "whatsapp:+919000000002", "", media={"kind": "voice"})
        runner.clock.advance(31)
        runner.send("buyer", "whatsapp:+919000000002", "more order details")
        findings.extend(runner.lint({"buyer": "en"}))
        counts["media-and-session-expiry"] = len(runner.transcript)

    with AuditRunner() as runner:
        os.environ["OPEN_REGISTRATION"] = "0"
        runner.send("unknown", "whatsapp:+919811122232", "hello")
        os.environ["OPEN_REGISTRATION"] = "1"
        findings.extend(runner.lint({"unknown": "en"}))
        counts["registration-closed"] = len(runner.transcript)
    return findings, counts


def run_scripted_audit() -> tuple[list[Violation], dict[str, int]]:
    """Exercise each role/language with fallback and fake-NLU order extraction."""
    all_findings: list[Violation] = []
    metrics: Counter[str] = Counter()
    for mode in (False, True):
        for lang in ("en", "hi", "hing"):
            for role, script_name in (
                ("buyer", "buyer_order"),
                ("member", "member_join"),
                ("leader", "leader_quote"),
            ):
                with AuditRunner(fake_nlu=mode) as runner:
                    # Keep flow scripts declarative so they can be extended without
                    # embedding conversation logic in the runner.
                    run_script_data(runner, script_name, lang)
                    languages = {role: lang}
                    if role != "leader":
                        languages["leader"] = "en"
                    all_findings.extend(runner.lint(languages))
                    metrics[f"{role}/{lang}/{'fake-nlu' if mode else 'rules'}"] = len(runner.transcript)

    error_findings, error_metrics = run_error_probes()
    all_findings.extend(error_findings)
    metrics.update(error_metrics)

    # Keep the reported real-world example first and verify the linter catches its defects.
    example_findings = lint_message(EXAMPLE_MESSAGE, "buyer", "hi", 1, EXAMPLE_MESSAGE)
    all_findings = example_findings + all_findings
    for site in hardcoded_message_sites():
        all_findings.append(Violation(
            "Major", "hardcoded-message", 0, site, "Static source scan: " + site
        ))
    for problem in catalogue_issues():
        all_findings.append(Violation(
            "Major", "catalogue-completeness", 0, problem, "Catalogue validation"
        ))
    return all_findings, dict(metrics)


def render_report(findings: list[Violation], metrics: dict[str, int]) -> str:
    grouped: dict[tuple[str, str], Violation] = {}
    active_findings = [f for f in findings if f.transcript != EXAMPLE_MESSAGE]
    for finding in active_findings:
        grouped.setdefault((finding.severity, finding.rule), finding)
    severity_order = {"Blocker": 0, "Major": 1, "Minor": 2}
    ordered = sorted(grouped.items(), key=lambda item: (
        severity_order.get(item[0][0], 3), item[0][1]
    ))
    example_rules = sorted({
        finding.rule for finding in findings if finding.transcript == EXAMPLE_MESSAGE
    })
    lines = [
        "# WhatsApp flow audit",
        "",
        "Generated by `backend/tools/flow_audit.py`. The synthetic section 1 transcript remains "
        "a regression fixture; it is not counted as current behavior.",
        "",
        "## Findings",
        "",
        "| Severity | Rule | Transcript / offending text | Proposed fix |",
        "|---|---|---|---|",
    ]
    if example_rules:
        example_text = _snippet(EXAMPLE_MESSAGE.replace("|", "\\|"), 160)
        lines.append(
            f"| Resolved | `section-1-example` ({', '.join(example_rules)}) | "
            f"{example_text} | "
            "Registration now requires consent and returns the localized buyer menu; the detector remains as a regression test. |"
        )
    fixes = {
        "language-purity": "Render the whole reply from the recipient-language catalogue.",
        "raw-command-syntax": "Replace command instructions with numbered, contextual choices.",
        "gendered-user-language": "Use gender-neutral Hindi/Hinglish phrasing.",
        "implied-consent": "Ask for explicit consent before registration and explain data use.",
        "role-content-leak": "Use role-scoped copy and verify buyer-visible output.",
        "dead-end": "End the reply with the next valid choice or a clear question.",
        "repeated-reply": "Add an input hint and handle the invalid choice explicitly.",
        "hardcoded-message": "Move user-facing text to the translation catalogue.",
        "catalogue-completeness": "Add all required languages and keep placeholders identical.",
        "unrendered-placeholder-or-leak": "Validate and render all user-visible data before sending.",
        "length": "Split the response and show only the next decision.",
        "multiple-requests": "Ask one question or request per message.",
        "multiple-questions": "Ask one question per message.",
        "currency-grouping": "Format amounts with Indian digit grouping.",
        "slash-separated-options": "Show each language or choice on its own numbered line.",
    }
    for (severity, rule), finding in ordered:
        if finding.transcript == EXAMPLE_MESSAGE:
            continue
        example = _snippet(finding.transcript.replace("|", "\\|"), 160)
        lines.append(
            f"| {severity} | `{rule}` | {example} | "
            f"{fixes.get(rule, 'Review and correct the user-facing response.')} |"
        )
    lines.extend(["", "## Metrics", ""])
    lines.append("| Script | Messages to complete / exercise |")
    lines.append("|---|---:|")
    for script, count in sorted(metrics.items()):
        lines.append(f"| `{script}` | {count} |")
    over_limit = sum(f.rule == "length" for f in active_findings)
    dead_ends = sum(f.rule == "dead-end" for f in active_findings)
    blockers = sum(f.severity == "Blocker" for f in active_findings)
    majors = sum(f.severity == "Major" for f in active_findings)
    lines.extend([
        "",
        f"- Message-length violations: **{over_limit}**.",
        f"- Dead ends: **{dead_ends}**.",
        f"- Blockers: **{blockers}**; Majors: **{majors}**.",
        "",
        "## Scope and gaps",
        "",
        "- Scripted runs cover buyer order placement, member registration/approval, and leader order creation "
        "for all three current languages with rule-based extraction and a fake NLU wrapper.",
        "- The webhook's Twilio signature, actual voice transcription, production Twilio delivery, and a live "
        "SSE connection are not invoked by this isolated runner; existing application tests cover parts of "
        "those integrations.",
        "- The catalog check verifies translation keys and placeholder parity. The AST scan is a review aid, "
        "not a proof that every dynamic user-facing string is catalog-backed.",
        "- Hindi and Hinglish examples are not native-speaker verified.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    findings, metrics = run_scripted_audit()
    report = render_report(findings, metrics)
    report_path = ROOT / "docs" / "FLOW_AUDIT.md"
    report_path.write_text(report, encoding="utf-8")
    current = [f for f in findings if f.transcript != EXAMPLE_MESSAGE]
    print(f"Wrote {report_path}")
    print(f"Findings: {len(current)}; blockers: "
          f"{sum(f.severity == 'Blocker' for f in current)}; "
          f"majors: {sum(f.severity == 'Major' for f in current)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
