"""_insert_operations_email_message previously built the parsed/classified/
triaged record in memory and issued a single INSERT at the end - an
exception anywhere in attachment saving, classification, or triage meant the
raw email (including its body) was never persisted at all, only counted as
an error. _prepare_operations_email_record is the pure, DB-free half of that
pipeline; it must keep the raw fields intact even when each downstream step
fails.
"""
from services import operations_inbox_service as ops

MESSAGE = {
    "subject": "[TMS-TEST] New Booking",
    "from": "Victor Reza <vreza75@gmail.com>",
    "body": "Test booking body",
    "received_at": "2026-07-14T09:00:00+00:00",
    "direction": "inbound",
    "mailbox": "dispatch@calitranscorp.com:INBOX",
    "mailbox_account": "dispatch@calitranscorp.com",
    "id": "1",
}


def test_attachment_processing_failure_does_not_drop_raw_fields(monkeypatch):
    monkeypatch.setattr(
        ops,
        "_save_operations_email_attachments",
        lambda message, message_id: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    record = ops._prepare_operations_email_record(MESSAGE)

    assert record["subject"] == "[TMS-TEST] New Booking"
    assert record["latest_body"] == "Test booking body"
    assert record["saved_attachments"] == []
    assert any("attachment" in err.lower() for err in record["processing_errors"])


def test_classification_failure_does_not_drop_raw_fields(monkeypatch):
    monkeypatch.setattr(
        ops,
        "build_operations_email_classification",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("classification exploded")),
    )

    record = ops._prepare_operations_email_record(MESSAGE)

    assert record["subject"] == "[TMS-TEST] New Booking"
    assert record["latest_body"] == "Test booking body"
    assert record["conversation_key"]
    assert any("classification" in err.lower() for err in record["processing_errors"])


def test_triage_failure_does_not_drop_raw_fields(monkeypatch):
    monkeypatch.setattr(
        ops,
        "triage_operations_email",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("triage exploded")),
    )

    record = ops._prepare_operations_email_record(MESSAGE)

    assert record["subject"] == "[TMS-TEST] New Booking"
    assert record["latest_body"] == "Test booking body"
    assert record["triage"] == {}
    assert any("triage" in err.lower() for err in record["processing_errors"])


def test_no_failures_produces_no_processing_errors():
    record = ops._prepare_operations_email_record(MESSAGE)
    assert record["processing_errors"] == []
    assert record["subject"] == "[TMS-TEST] New Booking"


def test_pre_saved_attachments_skips_self_save(monkeypatch):
    """Phase 7: workers/inbox_handlers.py's fetch-and-enqueue path saves
    attachments to disk before enqueueing an inbox.process_message job
    (raw bytes can't safely travel through a job payload), then passes
    the resulting metadata back in here via pre_saved_attachments - this
    must skip _save_operations_email_attachments entirely, not call it a
    second time on a message whose 'attachments' key was already
    stripped before this function ever saw it."""
    calls = []
    monkeypatch.setattr(
        ops,
        "_save_operations_email_attachments",
        lambda message, message_id: calls.append(1) or [],
    )

    pre_saved = [{"filename": "rate.pdf", "file_path": "/storage/rate.pdf", "content_sha256": "abc"}]
    record = ops._prepare_operations_email_record(MESSAGE, pre_saved_attachments=pre_saved)

    assert calls == []
    assert record["saved_attachments"] == pre_saved


def test_pre_saved_attachments_none_default_matches_unchanged_behavior():
    """The default (None) must behave identically to calling this
    function with one positional argument, as every pre-existing caller
    still does."""
    record_default = ops._prepare_operations_email_record(MESSAGE)
    record_explicit_none = ops._prepare_operations_email_record(MESSAGE, pre_saved_attachments=None)

    assert record_default["saved_attachments"] == record_explicit_none["saved_attachments"] == []
