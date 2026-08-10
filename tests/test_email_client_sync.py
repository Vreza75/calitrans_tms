"""Regression tests for the Operations Inbox IMAP sync (services/email_client.py).

Root cause under test: dispatch@calitranscorp.com is a live, high-volume
mailbox. The per-mailbox fetch only returns the newest `limit` messages found
within its scan window, so a [TMS-TEST] message sent minutes earlier is
routinely pushed past the top-N cutoff by ordinary customer traffic and never
returned, even though credentials and the IMAP connection are fine. These
tests use a fake IMAP4_SSL so they exercise the real account-resolution,
fetch, diagnostics, and dedicated test-sync lookback code paths without a
network connection.
"""
import email.message
import imaplib

from services import email_client


def _raw_email(*, subject, sender, date, message_id, body="Hello there"):
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "dispatch@calitranscorp.com"
    msg["Date"] = date
    msg["Message-ID"] = message_id
    msg.set_content(body)
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self, raw_messages, login_error=None):
        self._messages = raw_messages
        self._login_error = login_error
        self.selected_mailbox = None
        self.last_search_query = None

    def login(self, user, password):
        if self._login_error:
            raise self._login_error
        return ("OK", [b"LOGIN completed"])

    def select(self, mailbox):
        self.selected_mailbox = mailbox
        return ("OK", [str(len(self._messages)).encode()])

    def search(self, charset, query):
        self.last_search_query = query
        ids = b" ".join(mid for mid, _raw in self._messages)
        return ("OK", [ids])

    def fetch(self, eid, spec):
        for mid, raw in self._messages:
            if mid == eid:
                return ("OK", [(b"1 (RFC822)", raw)])
        return ("NO", [None])

    def logout(self):
        pass


def _patch_imap(monkeypatch, fake):
    monkeypatch.setattr(email_client.imaplib, "IMAP4_SSL", lambda *a, **k: fake)


def _fake_get_setting(overrides):
    """Isolated settings lookup for tests - real get_setting() also falls
    back to .streamlit/secrets.toml, so blanking os.environ alone can't
    guarantee a var is "unset" and risks echoing a real secret into a test
    failure diff. Tests that care about a var being present/absent should
    monkeypatch get_setting itself instead."""

    def _get(name, default=None):
        return overrides.get(name, default)

    return _get


# ---- account / password resolution ----


def test_password_for_email_account_empty_when_no_matching_var(monkeypatch):
    monkeypatch.setattr(
        email_client, "get_setting", _fake_get_setting({"DISPATCH_YAHOO_EMAIL": "dispatch@calitranscorp.com"})
    )
    assert email_client._password_for_email_account("dispatch@calitranscorp.com") == ""


def test_missing_dispatch_password_excludes_it_from_operations_accounts(monkeypatch):
    monkeypatch.setattr(
        email_client,
        "get_setting",
        _fake_get_setting(
            {
                "OPERATIONS_EMAIL_ACCOUNTS": "dispatch@calitranscorp.com",
                "DISPATCH_YAHOO_EMAIL": "dispatch@calitranscorp.com",
            }
        ),
    )
    accounts = email_client._operations_email_accounts()
    assert "dispatch@calitranscorp.com" not in [a["email"].lower() for a in accounts]


def test_dispatch_yahoo_app_password_is_recognized(monkeypatch):
    monkeypatch.setattr(
        email_client,
        "get_setting",
        _fake_get_setting(
            {
                "DISPATCH_YAHOO_EMAIL": "dispatch@calitranscorp.com",
                "DISPATCH_YAHOO_APP_PASSWORD": "test-password-marker",
            }
        ),
    )
    assert email_client._password_for_email_account("dispatch@calitranscorp.com") == "test-password-marker"


# ---- fetch_operations_email_sync: login / diagnostics ----


def test_incorrect_password_is_reported_not_swallowed(monkeypatch):
    fake = FakeIMAP([], login_error=imaplib.IMAP4.error("[AUTHENTICATIONFAILED] LOGIN Invalid credentials"))
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "wrong"}],
    )

    messages = email_client.fetch_operations_email_sync(limit=4)
    diagnostics = email_client.get_last_operations_email_sync_diagnostics()

    assert messages == []
    assert diagnostics["errors"], "login failure must be recorded, not swallowed"
    account_diag = diagnostics["per_account"][0]
    assert account_diag["login_success"] is False
    assert account_diag["error_type"]
    assert "AUTHENTICATIONFAILED" in account_diag["error_message"] or "Invalid credentials" in account_diag["error_message"]


def test_successful_dispatch_login_reports_folder_and_counts(monkeypatch):
    raw = [(b"1", _raw_email(subject="Hello", sender="a@b.com", date="Tue, 14 Jul 2026 10:00:00 +0000", message_id="<1@test>"))]
    fake = FakeIMAP(raw)
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "good"}],
    )

    messages = email_client.fetch_operations_email_sync(limit=4)
    diagnostics = email_client.get_last_operations_email_sync_diagnostics()
    account_diag = diagnostics["per_account"][0]

    assert len(messages) == 1
    assert account_diag["login_success"] is True
    assert account_diag["selected_folder"] == "INBOX"
    assert account_diag["messages_found"] == 1
    assert account_diag["messages_fetched"] == 1


def test_empty_mailbox_returns_zero_messages_without_error(monkeypatch):
    fake = FakeIMAP([])
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "good"}],
    )

    messages = email_client.fetch_operations_email_sync(limit=4)
    diagnostics = email_client.get_last_operations_email_sync_diagnostics()
    account_diag = diagnostics["per_account"][0]

    assert messages == []
    assert account_diag["login_success"] is True
    assert account_diag["messages_found"] == 0
    assert account_diag["errors"] == []


def test_time_budget_override_is_recorded_in_diagnostics(monkeypatch):
    fake = FakeIMAP([])
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "good"}],
    )

    email_client.fetch_operations_email_sync(limit=4, time_budget_seconds=7)
    diagnostics = email_client.get_last_operations_email_sync_diagnostics()
    assert diagnostics["time_budget_seconds"] == 7


# ---- TMS-TEST filtered lookback (OPERATIONS_TEST_SYNC_*) ----


def _enable_test_sync(monkeypatch, lookback_minutes="0"):
    monkeypatch.setenv("OPERATIONS_TEST_SYNC_ENABLED", "true")
    monkeypatch.setenv("OPERATIONS_TEST_SYNC_SUBJECT_CONTAINS", "[TMS-TEST]")
    monkeypatch.setenv("OPERATIONS_TEST_SYNC_ALLOWED_SENDER", "vreza75@gmail.com")
    monkeypatch.setenv("OPERATIONS_TEST_SYNC_LOOKBACK_MINUTES", lookback_minutes)


def test_tms_test_message_found_even_when_buried_past_normal_limit(monkeypatch):
    test_msg = (
        b"1",
        _raw_email(
            subject="[TMS-TEST] New booking",
            sender="Victor Reza <vreza75@gmail.com>",
            date="Tue, 14 Jul 2026 09:00:00 +0000",
            message_id="<test1@test>",
        ),
    )
    unrelated = [
        (
            str(i).encode(),
            _raw_email(
                subject=f"Real customer email {i}",
                sender="cust@example.com",
                date="Tue, 14 Jul 2026 10:00:00 +0000",
                message_id=f"<{i}@test>",
            ),
        )
        for i in range(2, 7)
    ]
    fake = FakeIMAP([test_msg] + unrelated)
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "good"}],
    )
    _enable_test_sync(monkeypatch)
    monkeypatch.setenv("OPERATIONS_EMAIL_PER_ACCOUNT_LIMIT", "2")

    messages = email_client.fetch_operations_email_sync(limit=2)

    subjects = [m["subject"] for m in messages]
    assert any("[TMS-TEST]" in s for s in subjects)


def test_tms_test_message_is_ordered_before_normal_fetch_results(monkeypatch):
    """A test message found only via the dedicated lookback must sort ahead
    of the normal top-N results: sync_operations_email_engine's insert loop
    can run out of time budget mid-list, and a message appended at the end
    is exactly the one most likely to get cut off before being saved."""
    test_msg = (
        b"1",
        _raw_email(
            subject="[TMS-TEST] New booking",
            sender="Victor Reza <vreza75@gmail.com>",
            date="Tue, 14 Jul 2026 09:00:00 +0000",
            message_id="<test1@test>",
        ),
    )
    unrelated = [
        (
            str(i).encode(),
            _raw_email(
                subject=f"Real customer email {i}",
                sender="cust@example.com",
                date="Tue, 14 Jul 2026 10:00:00 +0000",
                message_id=f"<{i}@test>",
            ),
        )
        for i in range(2, 7)
    ]
    fake = FakeIMAP([test_msg] + unrelated)
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "good"}],
    )
    _enable_test_sync(monkeypatch)
    monkeypatch.setenv("OPERATIONS_EMAIL_PER_ACCOUNT_LIMIT", "2")

    messages = email_client.fetch_operations_email_sync(limit=2)

    subjects = [m["subject"] for m in messages]
    test_index = next(i for i, s in enumerate(subjects) if "[TMS-TEST]" in s)
    assert test_index == 0, f"expected the test message first, got order: {subjects}"


def test_message_without_test_subject_not_pulled_in_by_lookback(monkeypatch):
    plain = (
        b"1",
        _raw_email(
            subject="Please send invoice",
            sender="Victor Reza <vreza75@gmail.com>",
            date="Tue, 14 Jul 2026 09:00:00 +0000",
            message_id="<plain1@test>",
        ),
    )
    fake = FakeIMAP([plain])
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "good"}],
    )
    _enable_test_sync(monkeypatch)
    monkeypatch.setenv("OPERATIONS_EMAIL_PER_ACCOUNT_LIMIT", "5")

    email_client.fetch_operations_email_sync(limit=5)
    diagnostics = email_client.get_last_operations_email_sync_diagnostics()
    assert diagnostics["per_account"][0].get("test_sync_matches", 0) == 0


def test_test_sync_disabled_by_default_does_not_add_extra_messages(monkeypatch):
    test_msg = (
        b"1",
        _raw_email(
            subject="[TMS-TEST] New booking",
            sender="Victor Reza <vreza75@gmail.com>",
            date="Tue, 14 Jul 2026 09:00:00 +0000",
            message_id="<test1@test>",
        ),
    )
    unrelated = [
        (
            str(i).encode(),
            _raw_email(
                subject=f"Real customer email {i}",
                sender="cust@example.com",
                date="Tue, 14 Jul 2026 10:00:00 +0000",
                message_id=f"<{i}@test>",
            ),
        )
        for i in range(2, 7)
    ]
    fake = FakeIMAP([test_msg] + unrelated)
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "good"}],
    )
    monkeypatch.delenv("OPERATIONS_TEST_SYNC_ENABLED", raising=False)
    monkeypatch.setenv("OPERATIONS_EMAIL_PER_ACCOUNT_LIMIT", "2")

    messages = email_client.fetch_operations_email_sync(limit=2)
    subjects = [m["subject"] for m in messages]
    assert not any("[TMS-TEST]" in s for s in subjects)


# ---- diagnose_operations_email_accounts (connection-only diagnostic) ----


def test_diagnose_reports_login_and_folder_without_fetching_messages(monkeypatch):
    fake = FakeIMAP([(b"1", _raw_email(subject="x", sender="y@z.com", date="Tue, 14 Jul 2026 09:00:00 +0000", message_id="<1@t>"))])
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_configured_operations_email_addresses",
        lambda: ["dispatch@calitranscorp.com"],
    )
    monkeypatch.setattr(email_client, "_password_for_email_account", lambda addr: "good")

    results = email_client.diagnose_operations_email_accounts()

    assert results[0]["email"] == "dispatch@calitranscorp.com"
    assert results[0]["credentials_configured"] is True
    assert results[0]["login_success"] is True
    assert results[0]["selected_folder"] == "INBOX"
    assert results[0]["messages_found"] == 1
    assert results[0]["error_type"] == ""
    assert not hasattr(fake, "fetch_called")


def test_diagnose_reports_missing_credentials_without_connecting(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("should not attempt a connection with no password configured")

    monkeypatch.setattr(email_client.imaplib, "IMAP4_SSL", _fail_if_called)
    monkeypatch.setattr(
        email_client,
        "_configured_operations_email_addresses",
        lambda: ["accounting@calitranscorp.com"],
    )
    monkeypatch.setattr(email_client, "_password_for_email_account", lambda addr: "")

    results = email_client.diagnose_operations_email_accounts()

    assert results[0]["credentials_configured"] is False
    assert results[0]["login_success"] is False
    assert results[0]["error_type"] == "MissingCredentials"


def test_diagnose_reports_login_failure(monkeypatch):
    fake = FakeIMAP([], login_error=imaplib.IMAP4.error("[AUTHENTICATIONFAILED] LOGIN Invalid credentials"))
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_configured_operations_email_addresses",
        lambda: ["accounting@calitranscorp.com"],
    )
    monkeypatch.setattr(email_client, "_password_for_email_account", lambda addr: "wrong")

    results = email_client.diagnose_operations_email_accounts()

    assert results[0]["credentials_configured"] is True
    assert results[0]["login_success"] is False
    assert results[0]["error_type"]
    assert "AUTHENTICATIONFAILED" in results[0]["error_message"] or "Invalid credentials" in results[0]["error_message"]


# ---- fetch_operations_email_near_date (bounded attachment rescan lookup) ----


def test_fetch_operations_email_near_date_uses_date_and_sender_bounded_search(monkeypatch):
    """rescan_operations_request_attachments' fallback used to be
    fetch_recent_operations_emails(limit=250), which scans thousands of
    candidate IDs on a high-volume mailbox and was observed to abort the
    IMAP connection outright (socket EOF) rather than find the target
    message. A date+sender bounded search is small and reliable instead."""
    test_msg = (
        b"1",
        _raw_email(
            subject="[TMS-TEST]- NEW BOOKING- 130067971 / GAOU7296662// OTR IMPORT",
            sender="Victor Reza <vreza75@gmail.com>",
            date="Tue, 14 Jul 2026 17:11:46 +0000",
            message_id="<near-date-1@test>",
        ),
    )
    fake = FakeIMAP([test_msg])
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "good"}],
    )

    messages = email_client.fetch_operations_email_near_date(
        sender="Victor Reza <vreza75@gmail.com>",
        received_at="2026-07-14T17:11:46+00:00",
        limit=10,
    )

    assert len(messages) == 1
    assert "GAOU7296662" in messages[0]["subject"]
    assert "SINCE" in fake.last_search_query
    assert "BEFORE" in fake.last_search_query
    assert "vreza75@gmail.com" in fake.last_search_query


def test_fetch_operations_email_near_date_falls_back_to_all_without_a_date(monkeypatch):
    fake = FakeIMAP([])
    _patch_imap(monkeypatch, fake)
    monkeypatch.setattr(
        email_client,
        "_operations_email_accounts",
        lambda: [{"email": "dispatch@calitranscorp.com", "password": "good"}],
    )

    email_client.fetch_operations_email_near_date(sender="", received_at=None, limit=10)

    assert fake.last_search_query == "ALL"
