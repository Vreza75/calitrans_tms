"""Phase 7: workers/inbox_handlers.py tests. Everything DB/network/
mailbox is mocked - services.operations_inbox_service functions are
mocked at the point workers/inbox_handlers.py imports them, not
re-implemented, to prove the wiring/contract without re-testing the
underlying pipeline (that's tests/test_operations_email_insert_
resilience.py and friends).
"""
from __future__ import annotations

from unittest import mock

import pytest

from workers.inbox_handlers import (
    SYSTEM_ACTOR_INBOX_WORKER,
    handle_inbox_process_message,
    handle_inbox_sync,
)


# ---------------------------------------------------------------------------
# handle_inbox_sync - delegates to _fetch_and_enqueue_inbox_messages.
# ---------------------------------------------------------------------------


def _fake_fetch_result(**overrides) -> dict:
    result = {
        "fetched": 0,
        "enqueued": 0,
        "skipped": 0,
        "errors": 0,
        "error": "",
        "error_messages": [],
    }
    result.update(overrides)
    return result


def test_handle_inbox_sync_delegates_with_defaults():
    with mock.patch(
        "workers.inbox_handlers._fetch_and_enqueue_inbox_messages",
        return_value=_fake_fetch_result(fetched=5, enqueued=5),
    ) as fetch:
        success, error = handle_inbox_sync({})

    assert success is True
    assert error == ""
    fetch.assert_called_once_with(limit=12, time_budget_seconds=25)


def test_handle_inbox_sync_payload_overrides_limit_and_time_budget():
    with mock.patch(
        "workers.inbox_handlers._fetch_and_enqueue_inbox_messages", return_value=_fake_fetch_result()
    ) as fetch:
        handle_inbox_sync({"limit": 50, "time_budget_seconds": 120})

    fetch.assert_called_once_with(limit=50, time_budget_seconds=120)


def test_handle_inbox_sync_per_message_enqueue_errors_without_a_top_level_error_still_succeeds():
    with mock.patch(
        "workers.inbox_handlers._fetch_and_enqueue_inbox_messages",
        return_value=_fake_fetch_result(fetched=5, enqueued=3, errors=2, error_messages=["subject: enqueue failed"]),
    ):
        success, error = handle_inbox_sync({})

    assert success is True
    assert error == ""


def test_handle_inbox_sync_total_failure_reports_failure():
    with mock.patch(
        "workers.inbox_handlers._fetch_and_enqueue_inbox_messages",
        return_value=_fake_fetch_result(error="No operations email mailbox could be attempted."),
    ):
        success, error = handle_inbox_sync({})

    assert success is False
    assert "mailbox" in error


# ---------------------------------------------------------------------------
# _fetch_and_enqueue_inbox_messages - the actual fetch/dedup/save/enqueue
# loop. Everything it calls is mocked.
# ---------------------------------------------------------------------------

_MESSAGE_A = {
    "subject": "Booking A",
    "from": "a@customer.com",
    "body": "body a",
    "received_at": "2026-08-13T09:00:00+00:00",
    "direction": "inbound",
    "mailbox": "dispatch@calitranscorp.com:INBOX",
    "id": "a1",
    "attachments": [{"filename": "rate.pdf", "content": b"%PDF-fake-bytes", "content_type": "application/pdf"}],
}
_MESSAGE_B = {
    "subject": "Booking B",
    "from": "b@customer.com",
    "body": "body b",
    "received_at": "2026-08-13T09:05:00+00:00",
    "direction": "inbound",
    "mailbox": "dispatch@calitranscorp.com:INBOX",
    "id": "b1",
}


def _patch_fetch_deps(**overrides):
    defaults = dict(
        ensure_schema=mock.DEFAULT,
        fetch_messages=[_MESSAGE_A, _MESSAGE_B],
        diagnostics={"errors": False, "accounts_attempted": 1},
        already_imported=False,
        saved_attachments=[{"filename": "rate.pdf", "file_path": "/storage/rate.pdf", "content_sha256": "abc"}],
        enqueue_job_return=1,
    )
    defaults.update(overrides)

    patches = [
        mock.patch("services.operations_inbox_service.ensure_operations_email_sync_schema"),
        mock.patch(
            "services.email_client.fetch_operations_email_sync", return_value=defaults["fetch_messages"]
        ),
        mock.patch(
            "services.email_client.get_last_operations_email_sync_diagnostics",
            return_value=defaults["diagnostics"],
        ),
        mock.patch(
            "services.operations_inbox_service.load_existing_operations_email_lookup",
            return_value={"loaded": True, "by_message_id": {}, "by_received": {}},
        ),
        mock.patch(
            "services.operations_inbox_service.operations_email_already_imported",
            return_value=defaults["already_imported"],
        ),
        mock.patch(
            "services.operations_inbox_service._save_operations_email_attachments",
            return_value=defaults["saved_attachments"],
        ),
        mock.patch("db_client.transaction"),
        mock.patch("repositories.worker_job_repo.enqueue_job", return_value=defaults["enqueue_job_return"]),
    ]
    return patches


def _enter_all(patches, conn=None):
    mocks = [p.start() for p in patches]
    if conn is not None:
        # db_client.transaction is patches[-2]; give its context manager a conn.
        mocks[-2].return_value.__enter__.return_value = conn
    return mocks


def test_fetch_and_enqueue_enqueues_one_job_per_new_message_and_strips_attachments():
    from workers.inbox_handlers import _fetch_and_enqueue_inbox_messages

    conn = mock.MagicMock()
    patches = _patch_fetch_deps()
    mocks = _enter_all(patches, conn=conn)
    try:
        enqueue_job = mocks[-1]
        result = _fetch_and_enqueue_inbox_messages()
    finally:
        for p in patches:
            p.stop()

    assert result["fetched"] == 2
    assert result["enqueued"] == 2
    assert result["error"] == ""
    assert enqueue_job.call_count == 2

    first_call = enqueue_job.call_args_list[0].kwargs
    assert first_call["job_type"] == "inbox.process_message"
    assert first_call["aggregate_type"] == "email_message"
    assert first_call["idempotency_key"].startswith("inbox.process_message:")
    assert first_call["actor"] == SYSTEM_ACTOR_INBOX_WORKER
    assert "attachments" not in first_call["payload"]["message"]
    assert first_call["payload"]["pre_saved_attachments"][0]["filename"] == "rate.pdf"
    assert b"%PDF-fake-bytes" not in str(first_call["payload"]).encode()  # raw bytes never reach the payload


def test_fetch_and_enqueue_skips_already_imported_messages():
    from workers.inbox_handlers import _fetch_and_enqueue_inbox_messages

    conn = mock.MagicMock()
    patches = _patch_fetch_deps(already_imported=True)
    mocks = _enter_all(patches, conn=conn)
    try:
        enqueue_job = mocks[-1]
        result = _fetch_and_enqueue_inbox_messages()
    finally:
        for p in patches:
            p.stop()

    assert result["skipped"] == 2
    assert result["enqueued"] == 0
    enqueue_job.assert_not_called()


def test_fetch_and_enqueue_total_failure_sets_error():
    from workers.inbox_handlers import _fetch_and_enqueue_inbox_messages

    conn = mock.MagicMock()
    patches = _patch_fetch_deps(
        diagnostics={"errors": True, "accounts_attempted": 0}, fetch_messages=[]
    )
    mocks = _enter_all(patches, conn=conn)
    try:
        result = _fetch_and_enqueue_inbox_messages()
    finally:
        for p in patches:
            p.stop()

    assert "mailbox" in result["error"]


# ---------------------------------------------------------------------------
# handle_inbox_process_message
# ---------------------------------------------------------------------------

_JOB_PAYLOAD = {
    "message": {"subject": "Booking A", "from": "a@customer.com", "body": "body a"},
    "pre_saved_attachments": [{"filename": "rate.pdf", "file_path": "/storage/rate.pdf"}],
}


def test_process_message_passes_pre_saved_attachments_through():
    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        return_value={"message_id": "m1", "conversation_key": "ck1"},
    ) as insert, mock.patch("services.operations_inbox_service.sync_conversation_status") as sync_status:
        success, error = handle_inbox_process_message(_JOB_PAYLOAD)

    assert success is True
    assert error == ""
    insert.assert_called_once_with(
        _JOB_PAYLOAD["message"], pre_saved_attachments=_JOB_PAYLOAD["pre_saved_attachments"]
    )
    sync_status.assert_called_once_with("ck1")


def test_process_message_conversation_status_failure_does_not_fail_the_job():
    """Best-effort reconciliation, matching sync_operations_email_engine's
    own swallowed-failure behavior for this step - the message itself was
    already successfully inserted."""
    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        return_value={"message_id": "m1", "conversation_key": "ck1"},
    ), mock.patch(
        "services.operations_inbox_service.sync_conversation_status", side_effect=RuntimeError("boom")
    ):
        success, error = handle_inbox_process_message(_JOB_PAYLOAD)

    assert success is True
    assert error == ""


def test_process_message_insert_failure_propagates_for_the_processor_to_retry():
    """Not caught here - a DB insert failure is a genuine infrastructure
    failure and must reach workers/processor.py's generic exception
    handling (retry, then terminal-fail), not be swallowed as success."""
    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        side_effect=RuntimeError("db insert failed"),
    ):
        with pytest.raises(RuntimeError):
            handle_inbox_process_message(_JOB_PAYLOAD)


def test_process_message_is_registered():
    from workers import processor

    assert processor.JOB_HANDLERS.get("inbox.process_message") is handle_inbox_process_message


def test_inbox_sync_handler_is_registered():
    from workers import processor

    assert processor.JOB_HANDLERS.get("inbox.sync") is handle_inbox_sync
