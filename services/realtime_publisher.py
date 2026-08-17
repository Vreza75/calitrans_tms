# services/realtime_publisher.py

from __future__ import annotations

"""Phase 9: durable domain-event publisher. Framework-neutral - no
Streamlit import - runnable from scripts/process_realtime_events.py, a
scheduled workflow, or a future worker. Mirrors services/
outbox_processor.py's structure closely (same claim/act/record-result
shape) - see that module's docstring for the crash-window/reclaim
reasoning, which applies identically here.

Delivery guarantee, stated precisely (see docs/architecture/
REALTIME_EVENTS.md's "Delivery semantics" section for the full
analysis): domain_events rows are durably persisted at-least-once
relative to the business transaction that created them (STEP 6's
same-transaction coupling). Broadcasting them to Supabase is
at-least-once from this processor's side (a crash after a successful
broadcast but before mark_published() commits causes a duplicate
broadcast on reclaim) and NOT exactly-once to the browser (Supabase
Realtime Broadcast itself is fire-and-forget over a websocket - a
disconnected client simply never receives a given broadcast at all).
This is why the client invalidation contract (STEP 17) always ends in an
authoritative API refetch, never a broadcast payload treated as the
source of truth."""

import json
from datetime import timedelta
from typing import Any

from db_client import transaction
from realtime.channels import channels_for
from realtime.publisher import get_publisher
from repositories import domain_event_repo
from utils.error_sanitizer import sanitize_exception_message, sanitize_message

# Bounded retry - after MAX_ATTEMPTS failed broadcasts, an event moves to
# the terminal 'failed' state. Lower stakes than outbox's MAX_ATTEMPTS
# (see repositories/domain_event_repo.py's module docstring: loss here is
# recoverable, not a business-correctness emergency), but still bounded -
# an operator can inspect/retry via scripts/process_realtime_events.py.
MAX_ATTEMPTS = 5
_BASE_BACKOFF_SECONDS = 15
_MAX_BACKOFF_SECONDS = 900

# Same crash-window self-heal as services/outbox_processor.py::
# RECLAIM_STALE_AFTER - a publisher that claims an event, commits
# 'processing', then dies before broadcasting is recovered automatically
# on the next periodic run.
RECLAIM_STALE_AFTER = timedelta(minutes=10)


def _parse_payload(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else json.loads(raw)


def _envelope_payload(event: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """Phase 10 addition: the broadcast payload was previously just the
    raw metadata dict, with no event_id/aggregate_id/occurred_at - a
    listening client had no ordering token and, on a collection channel
    (no aggregate_id in the topic name), no way to know which aggregate
    changed. Wraps metadata under its own key so realtime/channels.py's
    ALLOWED_METADATA_KEYS allowlist keeps governing only the business
    fields, not this envelope's own field names."""
    occurred_at = event.get("created_at")
    return {
        "event_id": event["id"],
        "aggregate_type": event["aggregate_type"],
        "aggregate_id": event["aggregate_id"],
        "version": event.get("version"),
        "occurred_at": occurred_at.isoformat() if hasattr(occurred_at, "isoformat") else occurred_at,
        "metadata": metadata,
    }


def process_one() -> dict[str, Any] | None:
    """Claim and broadcast exactly one pending, due domain event. Returns
    a small result dict for logging/observability, or None if nothing was
    claimable right now (an empty queue is not an error)."""
    with transaction() as conn:
        event = domain_event_repo.claim_next_pending(conn)
    if event is None:
        return None

    metadata = _parse_payload(event["payload"])
    envelope = _envelope_payload(event, metadata)
    publisher = get_publisher()

    try:
        success = True
        error = ""
        for channel in channels_for(event["aggregate_type"], event["aggregate_id"]):
            ok, channel_error = publisher.broadcast(channel=channel, event_type=event["event_type"], payload=envelope)
            if not ok:
                success = False
                error = channel_error
                break
        error = sanitize_message(error) if error else error
    except Exception as exc:  # noqa: BLE001 - a publisher bug must not crash the whole loop
        success, error = False, sanitize_exception_message(exc)

    with transaction() as conn:
        if success:
            domain_event_repo.mark_published(conn, event["id"])
            outcome = "published"
        elif event["attempt_count"] + 1 >= MAX_ATTEMPTS:
            domain_event_repo.mark_failed(conn, event["id"], error=error)
            outcome = "failed"
        else:
            domain_event_repo.mark_retry(
                conn, event["id"], error=error, delay=_backoff_for(event["attempt_count"])
            )
            outcome = "retrying"

    return {"id": event["id"], "event_type": event["event_type"], "outcome": outcome, "error": error}


def _backoff_for(attempt_count: int) -> timedelta:
    seconds = min(_BASE_BACKOFF_SECONDS * (2**attempt_count), _MAX_BACKOFF_SECONDS)
    return timedelta(seconds=seconds)


def process_pending(*, max_events: int = 100) -> list[dict[str, Any]]:
    """Process up to max_events pending events, stopping early once
    nothing is claimable. Same "no daemon, no scheduler loop" scope as
    services/outbox_processor.py::process_pending - scripts/
    process_realtime_events.py calls this once per invocation; the same
    scheduled workflow that runs the outbox/worker processors
    (.github/workflows/process-jobs.yml) runs this too.

    Reclaims stale 'processing' events before claiming anything new -
    same self-heal as services/outbox_processor.py::process_pending."""
    with transaction() as conn:
        domain_event_repo.reclaim_stuck_processing(conn, older_than=RECLAIM_STALE_AFTER)

    results = []
    for _ in range(max_events):
        result = process_one()
        if result is None:
            break
        results.append(result)
    return results
