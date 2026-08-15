"""Phase 9: realtime/events.py tests - publish_event's metadata guard and
time_bucketed_key's dedup-window behavior. The transactional-coupling
half of publish_event is exercised end-to-end by
tests/test_domain_event_repo.py (enqueue_domain_event) and by the
instrumented-command tests (tests/test_dispatch_transition_service.py,
tests/test_work_item_commands.py, tests/test_inbox_message_insert_idempotency.py).
"""
from __future__ import annotations

from unittest import mock

import pytest

from realtime.events import publish_event, time_bucketed_key


def test_publish_event_rejects_a_disallowed_metadata_key():
    conn = mock.MagicMock()
    with pytest.raises(ValueError, match="disallowed metadata keys"):
        publish_event(
            conn=conn,
            event_type="load.updated",
            aggregate_type="load",
            aggregate_id="1",
            idempotency_key="k",
            metadata={"customer_email": "someone@example.com"},
        )
    conn.execute.assert_not_called()


def test_publish_event_allows_a_known_metadata_key_and_enqueues():
    conn = mock.MagicMock()
    with mock.patch("realtime.events.enqueue_domain_event") as enqueue:
        publish_event(
            conn=conn,
            event_type="load.status_changed",
            aggregate_type="load",
            aggregate_id="1",
            idempotency_key="k",
            metadata={"new_status": "Dispatched"},
        )
    enqueue.assert_called_once()
    assert enqueue.call_args.kwargs["conn"] is conn
    assert enqueue.call_args.kwargs["payload"] == {"new_status": "Dispatched"}


def test_publish_event_with_no_metadata_defaults_to_empty_payload():
    conn = mock.MagicMock()
    with mock.patch("realtime.events.enqueue_domain_event") as enqueue:
        publish_event(conn=conn, event_type="load.updated", aggregate_type="load", aggregate_id="1", idempotency_key="k")
    assert enqueue.call_args.kwargs["payload"] == {}


def test_time_bucketed_key_same_bucket_same_key():
    window = 300
    base_time = (1_700_000_000 // window) * window
    same_bucket_later = base_time + window - 1

    assert time_bucketed_key("load.status_changed", "1", "Dispatched", now=base_time) == time_bucketed_key(
        "load.status_changed", "1", "Dispatched", now=same_bucket_later
    )


def test_time_bucketed_key_different_bucket_different_key():
    window = 300
    base_time = (1_700_000_000 // window) * window
    next_window = base_time + window + 1

    assert time_bucketed_key("load.status_changed", "1", "Dispatched", now=base_time) != time_bucketed_key(
        "load.status_changed", "1", "Dispatched", now=next_window
    )


def test_time_bucketed_key_different_content_different_key():
    t = 1_700_000_000.0
    assert time_bucketed_key("load.status_changed", "1", "Dispatched", now=t) != time_bucketed_key(
        "load.status_changed", "1", "Cancelled", now=t
    )
