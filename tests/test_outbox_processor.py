"""Phase 6: services/outbox_processor.py tests. Everything DB/network is
mocked (repositories.outbox_repo functions, db_client.transaction,
services.driver_sms_service.send_sms) - these tests are about the
processor's own dispatch/retry/terminal-failure logic, not about real
persistence (that's tests/test_outbox_repo.py, including the
PostgreSQL-gated concurrent-claim test that proves duplicate claims are
prevented).
"""
from __future__ import annotations

from unittest import mock

from services import outbox_processor


def _fake_transaction(conn):
    ctx = mock.MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_process_one_returns_none_when_nothing_claimable():
    conn = mock.MagicMock()
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.claim_next_pending", return_value=None
    ):
        result = outbox_processor.process_one()
    assert result is None


def test_process_one_success_marks_delivered_and_projects_delivery_status():
    conn = mock.MagicMock()
    event = {
        "id": 1,
        "event_type": "driver_dispatch_sms",
        "aggregate_type": "load",
        "aggregate_id": "7",
        "payload": {"to": "+15551234567", "message": "hi", "dispatch_message_id": 99},
        "attempt_count": 0,
        "actor": "dispatcher@calitranscorp.com",
    }
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.outbox_repo.mark_delivered") as mark_delivered, mock.patch(
        "services.driver_sms_service.send_sms", return_value=(True, "SM123")
    ) as send_sms, mock.patch(
        "services.dispatch_data_service.update_dispatch_message_delivery"
    ) as update_delivery:
        result = outbox_processor.process_one()

    assert result == {"id": 1, "event_type": "driver_dispatch_sms", "outcome": "delivered", "error": ""}
    send_sms.assert_called_once_with("+15551234567", "hi")
    mark_delivered.assert_called_once_with(conn, 1, provider_message_id="SM123")
    update_delivery.assert_called_once_with(99, conn=conn, delivery_status="delivered", provider_message_id="SM123")


def test_process_one_failure_below_max_attempts_retries():
    conn = mock.MagicMock()
    event = {
        "id": 2,
        "event_type": "driver_dispatch_sms",
        "aggregate_type": "load",
        "aggregate_id": "7",
        "payload": {"to": "+15551234567", "message": "hi", "dispatch_message_id": 5},
        "attempt_count": 1,
        "actor": "dispatcher@calitranscorp.com",
    }
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.outbox_repo.mark_retry") as mark_retry, mock.patch(
        "services.driver_sms_service.send_sms", return_value=(False, "Twilio error")
    ), mock.patch("services.dispatch_data_service.update_dispatch_message_delivery") as update_delivery:
        result = outbox_processor.process_one()

    assert result["outcome"] == "retrying"
    assert result["error"] == "Twilio error"
    mark_retry.assert_called_once()
    assert mark_retry.call_args.args == (conn, 2)
    assert mark_retry.call_args.kwargs["error"] == "Twilio error"
    update_delivery.assert_called_once_with(5, conn=conn, delivery_status="pending_delivery", provider_message_id=None)


def test_process_one_failure_at_max_attempts_is_terminal():
    conn = mock.MagicMock()
    event = {
        "id": 3,
        "event_type": "driver_dispatch_sms",
        "aggregate_type": "load",
        "aggregate_id": "7",
        "payload": {"to": "+15551234567", "message": "hi"},
        "attempt_count": outbox_processor.MAX_ATTEMPTS - 1,
        "actor": "dispatcher@calitranscorp.com",
    }
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.outbox_repo.mark_failed") as mark_failed, mock.patch(
        "services.driver_sms_service.send_sms", return_value=(False, "Twilio error")
    ):
        result = outbox_processor.process_one()

    assert result["outcome"] == "failed"
    mark_failed.assert_called_once_with(conn, 3, error="Twilio error")


def test_process_one_unknown_event_type_fails_immediately_without_calling_a_handler():
    conn = mock.MagicMock()
    event = {
        "id": 4,
        "event_type": "totally_unregistered_event",
        "aggregate_type": "load",
        "aggregate_id": "7",
        "payload": {},
        "attempt_count": 0,
        "actor": None,
    }
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.outbox_repo.mark_failed") as mark_failed:
        result = outbox_processor.process_one()

    assert result["outcome"] == "failed"
    mark_failed.assert_called_once()
    assert mark_failed.call_args.args[1] == 4


def test_process_one_handler_exception_is_sanitized_not_leaked():
    """Regression: outbox_events.last_error must never contain credential
    material, whichever path produced the error text. Fake DSN/password
    only - never a real secret in a test."""
    conn = mock.MagicMock()
    event = {
        "id": 5,
        "event_type": "driver_dispatch_sms",
        "aggregate_type": "load",
        "aggregate_id": "7",
        "payload": {"to": "+15551234567", "message": "hi"},
        "attempt_count": 0,
        "actor": None,
    }
    boom = RuntimeError("connection to postgresql://user:hunter2@db.internal:5432/prod failed")
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.outbox_repo.mark_retry") as mark_retry, mock.patch(
        "services.driver_sms_service.send_sms", side_effect=boom
    ):
        result = outbox_processor.process_one()

    assert "hunter2" not in result["error"]
    mark_retry.assert_called_once()
    # Assert on the value that would actually be persisted to
    # outbox_events.last_error (mark_retry's `error` kwarg), not just the
    # process_one() return value the caller happens to also see.
    assert "hunter2" not in mark_retry.call_args.kwargs["error"]


def test_process_one_handler_returned_failure_string_is_also_sanitized():
    """A handler's own returned failure string (e.g. a provider's raw
    error response body) is not an exception - it must be sanitized on
    the same choke point as the exception path, not skipped because it
    took a different code path through the handler. Fake credentials
    only - never a real secret in a test."""
    conn = mock.MagicMock()
    event = {
        "id": 6,
        "event_type": "driver_dispatch_sms",
        "aggregate_type": "load",
        "aggregate_id": "7",
        "payload": {"to": "+15551234567", "message": "hi"},
        "attempt_count": 0,
        "actor": None,
    }
    fake_error = (
        "Twilio error (401): could not authenticate - "
        "see https://api.twilio.com/2010-04-01/Accounts.json?api_key=FAKEKEY123 "
        "or retry with Authorization: Bearer fake.jwt.token"
    )
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.outbox_repo.mark_retry") as mark_retry, mock.patch(
        "services.driver_sms_service.send_sms", return_value=(False, fake_error)
    ):
        result = outbox_processor.process_one()

    for secret in ("FAKEKEY123", "fake.jwt.token"):
        assert secret not in result["error"]
        assert secret not in mark_retry.call_args.kwargs["error"]


def test_process_pending_reclaims_stale_processing_before_claiming():
    """Crash-window A fix: every process_pending() run reclaims events
    stuck 'processing' before attempting to claim anything new - not only
    when an operator remembers to pass --reclaim-stuck-minutes."""
    conn = mock.MagicMock()
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.reclaim_stuck_processing"
    ) as reclaim, mock.patch("services.outbox_processor.process_one", return_value=None):
        outbox_processor.process_pending(max_events=5)

    reclaim.assert_called_once_with(conn, older_than=outbox_processor.RECLAIM_STALE_AFTER)


def test_process_pending_stops_when_nothing_left_to_claim():
    call_count = {"n": 0}

    def _fake_process_one():
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return {"id": call_count["n"], "event_type": "driver_dispatch_sms", "outcome": "delivered", "error": ""}
        return None

    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(mock.MagicMock())), mock.patch(
        "repositories.outbox_repo.reclaim_stuck_processing"
    ), mock.patch("services.outbox_processor.process_one", side_effect=_fake_process_one):
        results = outbox_processor.process_pending(max_events=50)

    assert len(results) == 3


def test_process_pending_respects_max_events_cap():
    with mock.patch(
        "services.outbox_processor.transaction", return_value=_fake_transaction(mock.MagicMock())
    ), mock.patch("repositories.outbox_repo.reclaim_stuck_processing"), mock.patch(
        "services.outbox_processor.process_one",
        return_value={"id": 1, "event_type": "driver_dispatch_sms", "outcome": "delivered", "error": ""},
    ) as process_one:
        results = outbox_processor.process_pending(max_events=5)

    assert len(results) == 5
    assert process_one.call_count == 5
