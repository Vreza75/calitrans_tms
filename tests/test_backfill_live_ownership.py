"""PR #19 Stage 2 / Task B + P1: deterministic, flag-INDEPENDENT live-vs-
backfill ownership.

The bug: live sync and historical backfill share the
`inbox.process_message:<message-id>` idempotency key, and
repositories.worker_job_repo.enqueue_job's ON CONFLICT (idempotency_key) DO
UPDATE is a no-op that never rewrites the payload. Classification used to be
driven by a producer-stamped `backfill: True` payload flag, so whichever
producer enqueued the message FIRST froze its classification - a backfill of a
recent, still-actionable email could win that race and bury a live message.

The fix (P1): the producer flag is NEVER consulted. Classification is an
intrinsic property of the message - its received date vs INBOX_LIVE_CUTOFF -
evaluated at process time, with INDETERMINATE inputs handled explicitly. These
assert the control logic; DB/IMAP are mocked.
"""
from __future__ import annotations

from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# classify_message - the deterministic, flag-free rule
# ---------------------------------------------------------------------------

def test_pre_cutoff_message_is_historical(monkeypatch):
    from services.inbox_backfill_policy import HISTORICAL, classify_message

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    decision = classify_message({"received_at": "2026-01-15T10:00:00-08:00"})
    assert decision.status == HISTORICAL
    assert decision.is_historical is True


def test_post_cutoff_message_is_live(monkeypatch):
    from services.inbox_backfill_policy import LIVE, classify_message

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    decision = classify_message({"received_at": "2026-09-20T09:30:00-07:00"})
    assert decision.status == LIVE
    assert decision.is_historical is False


def test_cutoff_boundary_is_inclusive_live(monkeypatch):
    from services.inbox_backfill_policy import LIVE, classify_message

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    assert classify_message({"received_at": "2026-06-01T00:00:00+00:00"}).status == LIVE


def test_no_cutoff_is_indeterminate(monkeypatch):
    from services.inbox_backfill_policy import INDETERMINATE, classify_message

    monkeypatch.delenv("INBOX_LIVE_CUTOFF", raising=False)
    decision = classify_message({"received_at": "2026-01-15T10:00:00-08:00"})
    assert decision.status == INDETERMINATE
    assert decision.reason == "no_cutoff"


def test_unparseable_received_date_is_indeterminate(monkeypatch):
    from services.inbox_backfill_policy import INDETERMINATE, classify_message

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    decision = classify_message({"subject": "no date header"})
    assert decision.status == INDETERMINATE
    assert decision.reason == "unparseable_received_date"


def test_classification_carries_a_policy_version(monkeypatch):
    from services.inbox_backfill_policy import POLICY_VERSION, classify_message

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    assert classify_message({"received_at": "2026-09-20T00:00:00+00:00"}).policy_version == POLICY_VERSION


# ---------------------------------------------------------------------------
# Handler: flag is ignored; INDETERMINATE resolves to LIVE (safe default).
# ---------------------------------------------------------------------------

def _run_handler(payload):
    import workers.inbox_handlers as h

    seen: list = []
    with mock.patch(
        "services.operations_inbox_service._insert_operations_email_message",
        side_effect=lambda m, **kw: seen.append(kw.get("force_review_status")) or {"conversation_key": "c"},
    ), mock.patch("services.operations_inbox_service.sync_conversation_status") as sync:
        h.handle_inbox_process_message(payload)
    return seen[0], sync


def test_handler_ignores_backfill_flag_entirely(monkeypatch):
    """THE P1 GUARANTEE: a recent (post-cutoff) message is LIVE no matter which
    producer's payload won the enqueue race - flag present or absent."""
    from services.operations_inbox_service import HISTORICAL_BACKFILL_REVIEW_STATUS

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    recent = {"subject": "live booking", "received_at": "2026-09-20T09:30:00-07:00"}

    status_flagged, _ = _run_handler({"message": recent, "backfill": True})
    status_plain, _ = _run_handler({"message": recent})

    assert status_flagged is None and status_plain is None  # LIVE both ways
    assert HISTORICAL_BACKFILL_REVIEW_STATUS not in (status_flagged, status_plain)


def test_handler_classifies_old_mail_historical_even_without_flag(monkeypatch):
    from services.operations_inbox_service import HISTORICAL_BACKFILL_REVIEW_STATUS

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    old = {"subject": "old mail", "received_at": "2026-02-01T10:00:00-08:00"}

    status_plain, sync = _run_handler({"message": old})  # live producer, no flag
    assert status_plain == HISTORICAL_BACKFILL_REVIEW_STATUS
    sync.assert_not_called()  # historical -> no live reconciliation


def test_indeterminate_resolves_to_live_safe_default(monkeypatch):
    """No cutoff -> INDETERMINATE -> LIVE: never bury a possibly-actionable
    message. Backfill of genuinely old mail can't reach here (it requires a
    cutoff), so this cannot flood the queue with historical mail."""
    monkeypatch.delenv("INBOX_LIVE_CUTOFF", raising=False)
    status, sync = _run_handler({"message": {"subject": "x", "received_at": "2026-02-01T00:00:00+00:00"}})
    assert status is None  # live
    sync.assert_called_once()


# ---------------------------------------------------------------------------
# Order-independence / concurrency.
# ---------------------------------------------------------------------------

def test_concurrent_classification_yields_one_deterministic_result(monkeypatch):
    import threading

    from services.inbox_backfill_policy import classify_message

    monkeypatch.setenv("INBOX_LIVE_CUTOFF", "2026-06-01")
    recent = {"received_at": "2026-09-20T09:30:00-07:00"}

    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        verdict = classify_message(recent).status
        with lock:
            results.append(verdict)

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 40
    assert set(results) == {"live"}  # unanimous, no scheduling-dependent split


# ---------------------------------------------------------------------------
# Backfill producer refuses to run without a configured cutoff.
# ---------------------------------------------------------------------------

def test_backfill_refuses_without_cutoff(monkeypatch):
    from datetime import date

    import scripts.backfill_inbox as bf

    monkeypatch.delenv("INBOX_LIVE_CUTOFF", raising=False)
    with pytest.raises(bf.BackfillConfigurationError):
        bf.run_backfill(since=date(2026, 1, 1), until=date(2026, 1, 31), batch=10, dry_run=True)


def test_backfill_cli_exits_2_without_cutoff(monkeypatch):
    import scripts.backfill_inbox as bf

    monkeypatch.delenv("INBOX_LIVE_CUTOFF", raising=False)
    monkeypatch.setattr(
        "sys.argv", ["backfill_inbox.py", "--since", "2026-01-01", "--until", "2026-01-31", "--batch", "10", "--dry-run"]
    )
    assert bf.main() == 2
