"""Phase 7: workers/inbox_handlers.py::handle_inbox_sync tests.
sync_operations_email_engine itself is mocked - this is about the
handler's (success, error) translation, in particular proving the
processing-failure-vs-review-outcome distinction (workers/processor.py's
handler contract): a non-zero per-message error count inside an
otherwise-successful sync must NOT be reported as handler failure.
"""
from __future__ import annotations

from unittest import mock

from workers.inbox_handlers import handle_inbox_process_message, handle_inbox_sync


def _fake_result(**overrides) -> dict:
    result = {
        "fetched": 0,
        "imported": 0,
        "skipped": 0,
        "errors": 0,
        "error": "",
        "error_messages": [],
    }
    result.update(overrides)
    return result


def test_successful_sync_with_no_errors_returns_success():
    with mock.patch(
        "services.operations_inbox_service.sync_operations_email_engine",
        return_value=_fake_result(fetched=5, imported=5),
    ) as sync:
        success, error = handle_inbox_sync({})

    assert success is True
    assert error == ""
    sync.assert_called_once_with(limit=12, time_budget_seconds=25)


def test_per_message_errors_without_a_top_level_error_still_succeeds():
    """The core Phase 7 requirement: a sync that fetched/processed
    messages but couldn't fully parse some of them is a completed job,
    not a failed one - business ambiguity, not infrastructure failure."""
    with mock.patch(
        "services.operations_inbox_service.sync_operations_email_engine",
        return_value=_fake_result(
            fetched=5, imported=3, errors=2, error_messages=["subject: booking number ambiguous"]
        ),
    ):
        success, error = handle_inbox_sync({})

    assert success is True
    assert error == ""


def test_total_failure_sets_error_and_reports_failure():
    with mock.patch(
        "services.operations_inbox_service.sync_operations_email_engine",
        return_value=_fake_result(
            error="No operations email mailbox could be attempted. Check email addresses and Yahoo app passwords in secrets."
        ),
    ):
        success, error = handle_inbox_sync({})

    assert success is False
    assert "mailbox" in error


def test_payload_overrides_limit_and_time_budget():
    with mock.patch(
        "services.operations_inbox_service.sync_operations_email_engine", return_value=_fake_result()
    ) as sync:
        handle_inbox_sync({"limit": 50, "time_budget_seconds": 120})

    sync.assert_called_once_with(limit=50, time_budget_seconds=120)


# ---------------------------------------------------------------------------
# handle_inbox_process_message - not yet enqueued by anything (no producer
# as of this batch), but independently addressable/tested ahead of the
# eventual sync/process split. See workers/inbox_handlers.py's docstring.
# ---------------------------------------------------------------------------

_MESSAGE = {
    "subject": "[TMS-TEST] New Booking",
    "from": "Victor Reza <vreza75@gmail.com>",
    "body": "Test booking body",
    "received_at": "2026-07-14T09:00:00+00:00",
    "direction": "inbound",
    "mailbox": "dispatch@calitranscorp.com:INBOX",
    "mailbox_account": "dispatch@calitranscorp.com",
    "id": "1",
}


def test_process_message_calls_the_existing_insert_pipeline_unchanged():
    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        return_value={"message_id": "m1", "conversation_key": "ck1"},
    ) as insert:
        success, error = handle_inbox_process_message(_MESSAGE)

    assert success is True
    assert error == ""
    insert.assert_called_once_with(_MESSAGE)


def test_process_message_insert_failure_propagates_for_the_processor_to_retry():
    """Not caught here - a DB insert failure is a genuine infrastructure
    failure and must reach workers/processor.py's generic exception
    handling (retry, then terminal-fail), not be swallowed as success."""
    import pytest

    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        side_effect=RuntimeError("db insert failed"),
    ):
        with pytest.raises(RuntimeError):
            handle_inbox_process_message(_MESSAGE)


def test_process_message_is_registered():
    from workers import processor

    assert processor.JOB_HANDLERS.get("inbox.process_message") is handle_inbox_process_message
