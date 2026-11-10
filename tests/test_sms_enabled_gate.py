"""Tests for the SMS_ENABLED gate on driver_dispatch_sms.

When SMS is disabled (the default), the outbox handler must not call Twilio;
the event is marked terminally 'skipped' with no retry, and the run stays
green. Enabling SMS later must never deliver events created while it was off
(terminal 'skipped' is never re-claimed). When enabled, behavior is unchanged.

Processor-level tests mock everything DB/network (same approach as
tests/test_outbox_processor.py). One PostgreSQL-gated test proves the
"never re-claimed" guarantee against the real claim query.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest import mock

import pytest

from services import outbox_processor
from services.outbox_processor import SkipEvent

MIGRATION_TEST_DATABASE_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")

_SMS_EVENT = {
    "id": 1,
    "event_type": "driver_dispatch_sms",
    "aggregate_type": "load",
    "aggregate_id": "7",
    "payload": {"to": "+15551234567", "message": "hi", "dispatch_message_id": 99},
    "attempt_count": 0,
    "actor": "dispatcher@calitranscorp.com",
}


def _fake_transaction(conn):
    ctx = mock.MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


# --------------------------------------------------------------------------
# Config switch
# --------------------------------------------------------------------------

def test_sms_enabled_defaults_false(monkeypatch):
    import services.driver_sms_service as sms

    monkeypatch.setattr(sms, "get_secret", lambda name, default=None: default)
    assert sms.sms_enabled() is False


def test_sms_enabled_true_values(monkeypatch):
    import services.driver_sms_service as sms

    for raw in ["true", "True", "1", "yes", "on"]:
        monkeypatch.setattr(sms, "get_secret", lambda name, default=None, _r=raw: _r)
        assert sms.sms_enabled() is True
    for raw in ["false", "0", "no", "", "off"]:
        monkeypatch.setattr(sms, "get_secret", lambda name, default=None, _r=raw: _r)
        assert sms.sms_enabled() is False


# --------------------------------------------------------------------------
# Handler: disabled raises SkipEvent without ever calling Twilio
# --------------------------------------------------------------------------

def test_handler_disabled_raises_skip_without_sending(monkeypatch):
    monkeypatch.setattr("services.driver_sms_service.sms_enabled", lambda: False)
    with mock.patch("services.driver_sms_service.send_sms") as send_sms:
        with pytest.raises(SkipEvent) as exc:
            outbox_processor._handle_driver_dispatch_sms(_SMS_EVENT["payload"])
    assert exc.value.reason == "SMS disabled"
    send_sms.assert_not_called()


def test_handler_enabled_sends(monkeypatch):
    monkeypatch.setattr("services.driver_sms_service.sms_enabled", lambda: True)
    with mock.patch("services.driver_sms_service.send_sms", return_value=(True, "SM123")) as send_sms:
        result = outbox_processor._handle_driver_dispatch_sms(_SMS_EVENT["payload"])
    assert result == (True, "SM123", "")
    send_sms.assert_called_once_with("+15551234567", "hi")


# --------------------------------------------------------------------------
# process_one: disabled -> terminal skip, no retry, no delivery
# --------------------------------------------------------------------------

def test_process_one_disabled_marks_skipped_no_retry_no_send():
    conn = mock.MagicMock()
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.claim_next_pending", return_value=dict(_SMS_EVENT)
    ), mock.patch("services.driver_sms_service.sms_enabled", lambda: False), mock.patch(
        "services.driver_sms_service.send_sms"
    ) as send_sms, mock.patch(
        "repositories.outbox_repo.mark_skipped"
    ) as mark_skipped, mock.patch(
        "repositories.outbox_repo.mark_delivered"
    ) as mark_delivered, mock.patch(
        "repositories.outbox_repo.mark_retry"
    ) as mark_retry, mock.patch(
        "repositories.outbox_repo.mark_failed"
    ) as mark_failed, mock.patch(
        "services.dispatch_data_service.update_dispatch_message_delivery"
    ) as update_delivery, mock.patch(
        "realtime.events.publish_event"
    ):
        result = outbox_processor.process_one()

    assert result == {"id": 1, "event_type": "driver_dispatch_sms", "outcome": "skipped", "error": "SMS disabled"}
    send_sms.assert_not_called()  # no Twilio
    mark_skipped.assert_called_once_with(conn, 1, reason="SMS disabled")
    mark_delivered.assert_not_called()
    mark_retry.assert_not_called()  # no retry
    mark_failed.assert_not_called()  # not a failure -> run stays green
    # UI sees it as skipped, not stuck pending_delivery.
    update_delivery.assert_called_once_with(99, conn=conn, delivery_status="skipped", provider_message_id=None)


def test_process_one_enabled_still_delivers():
    conn = mock.MagicMock()
    with mock.patch("services.outbox_processor.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.outbox_repo.claim_next_pending", return_value=dict(_SMS_EVENT)
    ), mock.patch("services.driver_sms_service.sms_enabled", lambda: True), mock.patch(
        "services.driver_sms_service.send_sms", return_value=(True, "SM123")
    ) as send_sms, mock.patch(
        "repositories.outbox_repo.mark_delivered"
    ) as mark_delivered, mock.patch(
        "repositories.outbox_repo.mark_skipped"
    ) as mark_skipped, mock.patch(
        "services.dispatch_data_service.update_dispatch_message_delivery"
    ):
        result = outbox_processor.process_one()

    assert result["outcome"] == "delivered"
    send_sms.assert_called_once_with("+15551234567", "hi")
    mark_delivered.assert_called_once_with(conn, 1, provider_message_id="SM123")
    mark_skipped.assert_not_called()


# --------------------------------------------------------------------------
# Dispatch command: UI-facing sms_status reflects the switch
# --------------------------------------------------------------------------

def test_mark_ready_to_dispatch_reports_disabled_when_sms_off():
    """When SMS is off, the command still enqueues the event (audit + atomic)
    but returns sms_status='disabled' so the UI tells the dispatcher to notify
    the driver manually - and never calls Twilio."""
    from application.loads import commands
    from application.auth.models import AuthenticatedActor, Role

    conn = mock.MagicMock()
    actor = AuthenticatedActor(actor="dispatcher@calitranscorp.com", role=Role.DISPATCHER)
    with mock.patch("db_client.transaction") as transaction, mock.patch(
        "db_client.DispatchDatabaseClient"
    ), mock.patch(
        "services.dispatch_data_service._insert_dispatch_message", return_value=99
    ), mock.patch("repositories.outbox_repo.enqueue_outbox_event") as enqueue_event, mock.patch(
        "realtime.events.publish_event"
    ), mock.patch("services.driver_sms_service.send_sms") as send_sms, mock.patch(
        "services.driver_sms_service.sms_enabled", return_value=False
    ):
        transaction.return_value.__enter__.return_value = conn
        result = commands.mark_load_ready_to_dispatch(
            actor=actor,
            load_id=7,
            driver_name="Joe Driver",
            truck="T-100",
            chassis="C-200",
            phone="5551234567",
            message="You're dispatched.",
        )

    assert result.ok is True
    assert result.sms_status == "disabled"
    enqueue_event.assert_called_once()  # event still enqueued (will be skipped by the processor)
    send_sms.assert_not_called()


# --------------------------------------------------------------------------
# Req 3 (PostgreSQL): a skipped event is terminal - never re-claimed,
# even after SMS is re-enabled.
# --------------------------------------------------------------------------

@contextmanager
def _scratch_database(url: str):
    import db_client

    original = db_client.get_secret

    def _forced(name, default=None):
        return url if name == "DATABASE_URL" else original(name, default)

    with mock.patch.object(db_client, "get_secret", _forced):
        db_client._ENGINE_CACHE.pop(url, None)
        yield
        db_client._ENGINE_CACHE.pop(url, None)


@pytest.mark.skipif(
    not MIGRATION_TEST_DATABASE_URL,
    reason="Set MIGRATION_TEST_DATABASE_URL to a disposable Postgres to run the skip-terminality DB test.",
)
def test_db_skipped_event_is_never_reclaimed_after_enable():
    import db_client
    from scripts.run_migrations import run as run_migrations
    from repositories import outbox_repo

    url = MIGRATION_TEST_DATABASE_URL
    assert run_migrations(url) == 0
    with _scratch_database(url):
        db_client.execute("delete from outbox_events where event_type = 'driver_dispatch_sms'", {})
        # Event created while SMS was off, then terminally skipped.
        db_client.execute(
            "insert into outbox_events "
            "(event_type, aggregate_type, aggregate_id, payload, idempotency_key, actor, status, last_error) "
            "values ('driver_dispatch_sms','load','7', cast('{}' as jsonb), "
            "'driver_dispatch_sms:skip-test','dispatcher','skipped','SMS disabled')",
            {},
        )
        # Even now (as if SMS were re-enabled), the processor's claim query
        # must not pick it up - it is terminal, not pending.
        from db_client import transaction

        with transaction() as conn:
            claimed = outbox_repo.claim_next_pending(conn)
        assert claimed is None, "a terminally skipped event must never be re-claimed"

        db_client.execute("delete from outbox_events where event_type = 'driver_dispatch_sms'", {})
