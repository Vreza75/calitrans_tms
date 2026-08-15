"""Phase 9: services/realtime_publisher.py tests. Same testing style as
tests/test_outbox_processor.py - everything DB/network is mocked
(repositories.domain_event_repo functions, db_client.transaction, the
BroadcastPublisher returned by realtime.publisher.get_publisher) - these
tests are about the publisher's own claim/broadcast/retry/terminal-
failure logic, not about real persistence (that's
tests/test_domain_event_repo.py) or a real Supabase call.
"""
from __future__ import annotations

from unittest import mock

from services import realtime_publisher


def _fake_transaction(conn):
    ctx = mock.MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


class _FakePublisher:
    def __init__(self, *, success: bool = True, error: str = ""):
        self.success = success
        self.error = error
        self.calls = []

    def broadcast(self, *, channel, event_type, payload):
        self.calls.append((channel, event_type, dict(payload)))
        return self.success, self.error


def test_process_one_returns_none_when_nothing_claimable():
    conn = mock.MagicMock()
    with mock.patch("services.realtime_publisher.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.domain_event_repo.claim_next_pending", return_value=None
    ):
        result = realtime_publisher.process_one()
    assert result is None


def test_process_one_success_marks_published_and_broadcasts_to_both_channels():
    conn = mock.MagicMock()
    event = {
        "id": 1,
        "event_type": "load.status_changed",
        "aggregate_type": "load",
        "aggregate_id": "381",
        "version": None,
        "payload": {"new_status": "Dispatched"},
        "attempt_count": 0,
        "actor": "dispatcher@calitranscorp.com",
    }
    publisher = _FakePublisher(success=True)
    with mock.patch("services.realtime_publisher.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.domain_event_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.domain_event_repo.mark_published") as mark_published, mock.patch(
        "services.realtime_publisher.get_publisher", return_value=publisher
    ):
        result = realtime_publisher.process_one()

    assert result == {"id": 1, "event_type": "load.status_changed", "outcome": "published", "error": ""}
    mark_published.assert_called_once_with(conn, 1)
    # "load" has both a collection channel ("loads") and a resource
    # channel ("load:381") - both must receive the broadcast.
    channels_used = [call[0] for call in publisher.calls]
    assert channels_used == ["loads", "load:381"]


def test_process_one_broadcast_payload_is_an_envelope_around_the_metadata():
    """Phase 10 addition: the wire payload must carry event_id/
    aggregate_type/aggregate_id/version/occurred_at alongside the
    caller's metadata - not just the raw metadata dict - so a listening
    client has an ordering token and an aggregate id even on a
    collection channel (see docs/architecture/REALTIME_EVENTS.md's
    "Wire payload" subsection)."""
    import datetime

    conn = mock.MagicMock()
    occurred_at = datetime.datetime(2026, 8, 15, 18, 4, 2, tzinfo=datetime.timezone.utc)
    event = {
        "id": 1042,
        "event_type": "load.status_changed",
        "aggregate_type": "load",
        "aggregate_id": "381",
        "version": None,
        "payload": {"new_status": "Dispatched", "old_status": "Verified"},
        "attempt_count": 0,
        "actor": "dispatcher@calitranscorp.com",
        "created_at": occurred_at,
    }
    publisher = _FakePublisher(success=True)
    with mock.patch("services.realtime_publisher.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.domain_event_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.domain_event_repo.mark_published"), mock.patch(
        "services.realtime_publisher.get_publisher", return_value=publisher
    ):
        realtime_publisher.process_one()

    assert len(publisher.calls) == 2
    for _channel, _event_type, sent_payload in publisher.calls:
        assert sent_payload == {
            "event_id": 1042,
            "aggregate_type": "load",
            "aggregate_id": "381",
            "version": None,
            "occurred_at": "2026-08-15T18:04:02+00:00",
            "metadata": {"new_status": "Dispatched", "old_status": "Verified"},
        }


def test_process_one_document_event_only_broadcasts_to_its_collection_channel():
    """"document" has no resource-level channel this pass (realtime/
    channels.py's _RESOURCE_CHANNEL_AGGREGATE_TYPES) - only "documents"
    should receive the broadcast, not a per-document channel."""
    conn = mock.MagicMock()
    event = {
        "id": 2,
        "event_type": "document.available",
        "aggregate_type": "document",
        "aggregate_id": "42",
        "version": None,
        "payload": {"status": "available"},
        "attempt_count": 0,
        "actor": None,
    }
    publisher = _FakePublisher(success=True)
    with mock.patch("services.realtime_publisher.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.domain_event_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.domain_event_repo.mark_published"), mock.patch(
        "services.realtime_publisher.get_publisher", return_value=publisher
    ):
        realtime_publisher.process_one()

    assert [call[0] for call in publisher.calls] == ["documents"]


def test_process_one_failure_below_max_attempts_retries():
    conn = mock.MagicMock()
    event = {
        "id": 3,
        "event_type": "load.status_changed",
        "aggregate_type": "load",
        "aggregate_id": "381",
        "version": None,
        "payload": {},
        "attempt_count": 1,
        "actor": None,
    }
    publisher = _FakePublisher(success=False, error="Supabase broadcast failed: HTTP 503")
    with mock.patch("services.realtime_publisher.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.domain_event_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.domain_event_repo.mark_retry") as mark_retry, mock.patch(
        "services.realtime_publisher.get_publisher", return_value=publisher
    ):
        result = realtime_publisher.process_one()

    assert result["outcome"] == "retrying"
    assert "503" in result["error"]
    mark_retry.assert_called_once()
    assert mark_retry.call_args.args == (conn, 3)


def test_process_one_failure_at_max_attempts_is_terminal():
    conn = mock.MagicMock()
    event = {
        "id": 4,
        "event_type": "load.status_changed",
        "aggregate_type": "load",
        "aggregate_id": "381",
        "version": None,
        "payload": {},
        "attempt_count": realtime_publisher.MAX_ATTEMPTS - 1,
        "actor": None,
    }
    publisher = _FakePublisher(success=False, error="Supabase broadcast failed: HTTP 500")
    with mock.patch("services.realtime_publisher.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.domain_event_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.domain_event_repo.mark_failed") as mark_failed, mock.patch(
        "services.realtime_publisher.get_publisher", return_value=publisher
    ):
        result = realtime_publisher.process_one()

    assert result["outcome"] == "failed"
    mark_failed.assert_called_once_with(conn, 4, error=mock.ANY)


def test_process_one_publisher_exception_is_sanitized_not_leaked():
    conn = mock.MagicMock()
    event = {
        "id": 5,
        "event_type": "load.status_changed",
        "aggregate_type": "load",
        "aggregate_id": "381",
        "version": None,
        "payload": {},
        "attempt_count": 0,
        "actor": None,
    }

    class _BoomPublisher:
        def broadcast(self, **kwargs):
            raise RuntimeError("connection to postgresql://user:hunter2@db.internal:5432/prod failed")

    with mock.patch("services.realtime_publisher.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.domain_event_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.domain_event_repo.mark_retry") as mark_retry, mock.patch(
        "services.realtime_publisher.get_publisher", return_value=_BoomPublisher()
    ):
        result = realtime_publisher.process_one()

    assert "hunter2" not in result["error"]
    assert "hunter2" not in mark_retry.call_args.kwargs["error"]


def test_process_one_stops_broadcasting_further_channels_after_first_failure():
    """A load event has two channels - if the first broadcast call fails,
    the second must not also be attempted (one retry cycle covers both on
    the next attempt, not a partial per-channel state)."""
    conn = mock.MagicMock()
    event = {
        "id": 6,
        "event_type": "load.status_changed",
        "aggregate_type": "load",
        "aggregate_id": "381",
        "version": None,
        "payload": {},
        "attempt_count": 0,
        "actor": None,
    }
    publisher = _FakePublisher(success=False, error="boom")
    with mock.patch("services.realtime_publisher.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.domain_event_repo.claim_next_pending", return_value=event
    ), mock.patch("repositories.domain_event_repo.mark_retry"), mock.patch(
        "services.realtime_publisher.get_publisher", return_value=publisher
    ):
        realtime_publisher.process_one()

    assert len(publisher.calls) == 1


def test_process_pending_reclaims_stale_processing_before_claiming():
    conn = mock.MagicMock()
    with mock.patch("services.realtime_publisher.transaction", return_value=_fake_transaction(conn)), mock.patch(
        "repositories.domain_event_repo.reclaim_stuck_processing"
    ) as reclaim, mock.patch("services.realtime_publisher.process_one", return_value=None):
        realtime_publisher.process_pending(max_events=5)

    reclaim.assert_called_once_with(conn, older_than=realtime_publisher.RECLAIM_STALE_AFTER)


def test_process_pending_stops_when_nothing_left_to_claim():
    call_count = {"n": 0}

    def _fake_process_one():
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return {"id": call_count["n"], "event_type": "load.status_changed", "outcome": "published", "error": ""}
        return None

    with mock.patch(
        "services.realtime_publisher.transaction", return_value=_fake_transaction(mock.MagicMock())
    ), mock.patch("repositories.domain_event_repo.reclaim_stuck_processing"), mock.patch(
        "services.realtime_publisher.process_one", side_effect=_fake_process_one
    ):
        results = realtime_publisher.process_pending(max_events=50)

    assert len(results) == 3


def test_process_pending_respects_max_events_cap():
    with mock.patch(
        "services.realtime_publisher.transaction", return_value=_fake_transaction(mock.MagicMock())
    ), mock.patch("repositories.domain_event_repo.reclaim_stuck_processing"), mock.patch(
        "services.realtime_publisher.process_one",
        return_value={"id": 1, "event_type": "load.status_changed", "outcome": "published", "error": ""},
    ) as process_one:
        results = realtime_publisher.process_pending(max_events=5)

    assert len(results) == 5
    assert process_one.call_count == 5
