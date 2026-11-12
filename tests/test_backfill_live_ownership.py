"""PR #19 Stage 2 / Task B: deterministic live-vs-backfill ownership.

The bug: live sync and historical backfill share the
`inbox.process_message:<message-id>` idempotency key, and
repositories.worker_job_repo.enqueue_job's ON CONFLICT (idempotency_key) DO
UPDATE is a no-op that never rewrites the payload. Classification was driven
ONLY by a producer-stamped `backfill: True` payload flag, so whichever producer
enqueued the message FIRST froze its classification. A backfill of a recent,
still-actionable email could win that race and bury a live message as
Historical, out of the active dispatch queue.

The fix: historical-vs-live is an INTRINSIC property of the message - its own
received date vs a configured live cutoff (INBOX_LIVE_CUTOFF), evaluated at
process time. Enqueue order can no longer change the outcome. These assert the
control logic; DB/IMAP are mocked.
"""
from __future__ import annotations

from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# message_is_historical - the deterministic rule itself
# ---------------------------------------------------------------------------

def test_cutoff_makes_pre_cutoff_message_historical_regardless_of_flag(monkeypatch):
    from services.inbox_backfill_policy import message_is_historical

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    old = {"received_at": "2026-01-15T10:00:00-08:00"}
    # Historical by date even if the producer did NOT set the backfill flag
    # (e.g. live sync fetched an old message): date is authoritative.
    assert message_is_historical(old, backfill_flag=False) is True
    assert message_is_historical(old, backfill_flag=True) is True


def test_cutoff_keeps_post_cutoff_message_live_even_when_backfill_flag_set(monkeypatch):
    """THE P1 GUARANTEE: a recent (post-cutoff) message is LIVE even if a
    backfill producer stamped backfill=True and won the enqueue race. The flag
    can never bury a live, still-actionable email."""
    from services.inbox_backfill_policy import message_is_historical

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    recent = {"received_at": "2026-09-20T09:30:00-07:00"}
    assert message_is_historical(recent, backfill_flag=True) is False
    assert message_is_historical(recent, backfill_flag=False) is False


def test_cutoff_boundary_is_inclusive_live(monkeypatch):
    """A message received exactly at the cutoff instant is LIVE (historical is
    strictly-before), so go-live-day mail is never hidden."""
    from services.inbox_backfill_policy import message_is_historical

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    at_cutoff = {"received_at": "2026-06-01T00:00:00+00:00"}
    assert message_is_historical(at_cutoff, backfill_flag=True) is False


def test_no_cutoff_falls_back_to_producer_flag(monkeypatch):
    """Legacy fallback: with no cutoff configured, the producer flag decides -
    preserves pre-Stage-2 behavior for operators who have not set a cutoff."""
    from services.inbox_backfill_policy import message_is_historical

    monkeypatch.delenv("INBOX_LIVE_CUTOFF", raising=False)
    msg = {"received_at": "2026-01-15T10:00:00-08:00"}
    assert message_is_historical(msg, backfill_flag=True) is True
    assert message_is_historical(msg, backfill_flag=False) is False


def test_undated_message_with_cutoff_falls_back_to_flag(monkeypatch):
    """A message whose received date can't be parsed can't be dated against the
    cutoff; fall back to the producer flag (a degenerate edge - real email
    always carries a Date). A live-sync undated message stays live."""
    from services.inbox_backfill_policy import message_is_historical

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    undated = {"subject": "no date header"}
    assert message_is_historical(undated, backfill_flag=True) is True
    assert message_is_historical(undated, backfill_flag=False) is False


# ---------------------------------------------------------------------------
# Order-independence / concurrency: same message, either enqueue winner,
# identical classification.
# ---------------------------------------------------------------------------

def test_classification_is_order_independent_at_process_time(monkeypatch):
    """Simulate the enqueue race's two possible outcomes: the stored job's
    payload carries backfill=True (backfill won ON CONFLICT) OR backfill absent
    (live won). Processing either yields the SAME force_review_status, because
    the date-vs-cutoff rule - not the frozen payload flag - decides."""
    import workers.inbox_handlers as h
    from services.operations_inbox_service import HISTORICAL_BACKFILL_REVIEW_STATUS

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    recent_message = {"subject": "live booking", "received_at": "2026-09-20T09:30:00-07:00"}

    seen: list = []
    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        side_effect=lambda m, **kw: seen.append(kw.get("force_review_status")) or {"conversation_key": "c"},
    ), mock.patch("services.operations_inbox_service.sync_conversation_status"):
        # Backfill producer won the race (payload has backfill=True)...
        h.handle_inbox_process_message({"message": recent_message, "backfill": True})
        # ...or live producer won (no flag). Same message either way.
        h.handle_inbox_process_message({"message": recent_message})

    # Recent message is LIVE in BOTH orderings - backfill could not bury it.
    assert seen == [None, None]
    assert HISTORICAL_BACKFILL_REVIEW_STATUS not in seen


def test_pre_cutoff_message_is_historical_in_both_orderings(monkeypatch):
    import workers.inbox_handlers as h
    from services.operations_inbox_service import HISTORICAL_BACKFILL_REVIEW_STATUS

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    old_message = {"subject": "old mail", "received_at": "2026-02-01T10:00:00-08:00"}

    seen: list = []
    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        side_effect=lambda m, **kw: seen.append(kw.get("force_review_status")) or {"conversation_key": "c"},
    ), mock.patch("services.operations_inbox_service.sync_conversation_status"):
        h.handle_inbox_process_message({"message": old_message, "backfill": True})
        h.handle_inbox_process_message({"message": old_message})  # even with no flag

    # Old message is Historical in BOTH orderings - including the live-sync path
    # that did NOT set the flag, because its date is authoritative.
    assert seen == [HISTORICAL_BACKFILL_REVIEW_STATUS, HISTORICAL_BACKFILL_REVIEW_STATUS]


def test_concurrent_classification_yields_one_deterministic_result_per_message(monkeypatch):
    """True concurrency: many threads classify the same message under both
    producer flags at once; the result must be a single deterministic value,
    never a mix that depends on scheduling."""
    import threading

    from services.inbox_backfill_policy import message_is_historical

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    recent = {"received_at": "2026-09-20T09:30:00-07:00"}

    results: list[bool] = []
    lock = threading.Lock()

    def worker(flag: bool) -> None:
        verdict = message_is_historical(recent, backfill_flag=flag)
        with lock:
            results.append(verdict)

    threads = [threading.Thread(target=worker, args=(i % 2 == 0,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 40
    assert set(results) == {False}  # live, unanimously - no scheduling-dependent split


# ---------------------------------------------------------------------------
# Historical processing must not trigger live side effects.
# ---------------------------------------------------------------------------

def test_historical_message_does_not_reconcile_live_conversation_status(monkeypatch):
    """A date-historical message (even one a LIVE producer enqueued without the
    flag) must not run sync_conversation_status, which could flip an active
    conversation's status based on old mail."""
    import workers.inbox_handlers as h

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    old_message = {"subject": "x", "received_at": "2026-02-01T10:00:00-08:00"}

    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        return_value={"conversation_key": "conv-1"},
    ), mock.patch("services.operations_inbox_service.sync_conversation_status") as sync:
        h.handle_inbox_process_message({"message": old_message})  # live producer, no flag
        sync.assert_not_called()  # date says historical -> no live reconciliation
