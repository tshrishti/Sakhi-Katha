from tools import flow_audit as audit


def test_real_example_is_detected_as_multiple_independent_failures():
    findings = audit.lint_message(audit.EXAMPLE_MESSAGE, "buyer", "hi")
    rules = {finding.rule for finding in findings}
    assert {
        "language-purity",
        "raw-command-syntax",
        "gendered-user-language",
        "implied-consent",
        "role-content-leak",
        "dead-end",
    } <= rules


def test_catalogue_placeholder_parity_is_checked():
    assert audit.catalogue_issues() == []


def test_next_step_detector_recognizes_numbered_choices_navigation_and_waiting():
    assert audit._is_next_step("1 Open new orders\n9 Menu")
    assert audit._is_next_step("Reply 0 to go back or 9 for the menu.")
    assert audit._is_next_step("मेनू के लिए 9 भेजें।")
    assert audit._is_next_step("Your request is waiting for your group leader.")
    assert audit._is_next_step("I could not read that photo. Please send a clearer photo.")


def test_gender_detector_allows_gendered_verb_agreeing_with_possessive_noun():
    copy = audit.i18n.t("registered_buyer", "hi", name="Kamal")
    assert "gendered-user-language" not in {
        finding.rule for finding in audit.lint_message(copy, "buyer", "hi")
    }


def test_report_does_not_hide_active_issue_behind_synthetic_fixture():
    active = audit.Violation("Blocker", "dead-end", 1, "Prompt", "leader: '1' -> 'Prompt'")
    report = audit.render_report(
        audit.lint_message(audit.EXAMPLE_MESSAGE, "buyer", "hi") + [active], {}
    )
    assert "leader: '1' -> 'Prompt'" in report


def test_auditor_runs_real_bot_and_captures_notifications():
    with audit.AuditRunner() as runner:
        audit.run_script_data(runner, "buyer_order", "en")
        assert len(runner.replies) == 8
        assert any(reply and "Order #1 sent" in reply for _, _, _, reply in runner.replies)
        assert runner.notifications


def test_duplicate_message_sid_is_ignored():
    with audit.AuditRunner() as runner:
        phone = "whatsapp:+919811122211"
        first = runner.send("unknown", phone, "hello", msg_id="SM-AUDIT-1")
        duplicate = runner.send("unknown", phone, "hello", msg_id="SM-AUDIT-1")
        assert first
        assert duplicate is None


def test_language_word_selects_language_without_repeating_the_welcome():
    with audit.AuditRunner() as runner:
        phone = "whatsapp:+919811122242"
        welcome = runner.send("unknown", phone, "hello")
        name_prompt = runner.send("unknown", phone, "english")
        assert welcome != name_prompt
        assert "What is your name?" in name_prompt


def test_buyer_registration_requires_and_records_consent_across_restart():
    with audit.AuditRunner() as runner:
        phone = "whatsapp:+919811122240"
        runner.send("unknown", phone, "hello")
        runner.send("unknown", phone, "2")
        runner.send("unknown", phone, "Nila")
        consent = runner.send("unknown", phone, "2")
        assert "क्या यह ठीक है" in consent
        with audit.db.S() as session:
            assert session.query(audit.db.User).filter_by(phone="+919811122240").first() is None
            state = session.get(audit.db.ChatState, "+919811122240")
            assert state.step == "onboard_consent"
        audit.bot.ONB.clear()
        registered = runner.send("unknown", phone, "1")
        assert "आपका रजिस्ट्रेशन हो गया है" in registered
        with audit.db.S() as session:
            user = session.query(audit.db.User).filter_by(phone="+919811122240").one()
            state = session.get(audit.db.ChatState, "+919811122240")
            assert user.consent_at and state.step == "menu"


def test_declining_consent_does_not_register_user():
    with audit.AuditRunner() as runner:
        phone = "whatsapp:+919811122241"
        for text in ("hello", "1", "Nila", "2"):
            runner.send("unknown", phone, text)
        reply = runner.send("unknown", phone, "2")
        assert "did not register" in reply
        with audit.db.S() as session:
            assert session.query(audit.db.User).filter_by(phone="+919811122241").first() is None


def test_member_join_request_keeps_consent_and_approval_menu():
    with audit.AuditRunner() as runner:
        audit.run_script_data(runner, "member_join", "hi")
        with audit.db.S() as session:
            user = session.query(audit.db.User).filter_by(phone="+919811122202").one()
            state = session.get(audit.db.ChatState, user.phone)
            assert user.role == "seller"
            assert state.step == "menu" and state.lang == "hi"
            request = session.query(audit.db.JoinRequest).filter_by(phone=user.phone).one()
            assert request.consent_at and request.status == "approved"
