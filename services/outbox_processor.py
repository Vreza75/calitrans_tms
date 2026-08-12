# services/outbox_processor.py

from __future__ import annotations

"""Phase 6: transactional outbox processor. Framework-neutral - no
Streamlit import - runnable from scripts/process_outbox.py, a future
worker, or a scheduled job. Not embedded in Streamlit rendering.

Lifecycle per event (see repositories/outbox_repo.py's module docstring
for why claim and result are two separate short transactions):

    claim (short transaction, commits immediately)
        -> perform external side effect (no open transaction during this)
    result (short transaction: outbox_events status + any accompanying
            audit-row projection, e.g. dispatch_messages.delivery_status)

A crash between claim-commit and result-commit leaves the event
'processing' - repositories.outbox_repo.reclaim_stuck_processing recovers
those automatically at the start of every process_pending() run (see
RECLAIM_STALE_AFTER below), not only via an operator's explicit call.

Delivery guarantee, stated precisely (see docs/architecture/OUTBOX.md's
"Crash windows and delivery guarantees" section for the full analysis):
this processor provides **at-least-once** delivery, not exactly-once.
If a worker crashes after the external call (e.g. Twilio) has already
succeeded but before mark_delivered() commits, the event is later
reclaimed as stale and reprocessed - the handler runs again and a
second message is sent. services/driver_sms_service.py's Twilio
integration has no confirmed provider-side idempotency mechanism in this
codebase (not verified against current Twilio API capabilities in this
pass - a documented future improvement, not assumed here), so this
duplicate-send risk is not eliminated, only minimized: the window
between the handler call returning and mark_delivered() committing is
kept as small as possible (one function call, no other work in between),
and RECLAIM_STALE_AFTER is set well above any realistic single handler
call so a merely-slow (not crashed) worker is never falsely reclaimed.
"""

import json
from datetime import timedelta
from typing import Any, Callable

from db_client import transaction
from repositories import outbox_repo
from utils.error_sanitizer import sanitize_exception_message, sanitize_message

# Bounded retry: after MAX_ATTEMPTS failed deliveries, an event moves to
# the terminal 'failed' state instead of retrying forever - an operator
# must look at it (see repositories.outbox_repo.mark_failed's docstring).
MAX_ATTEMPTS = 5
_BASE_BACKOFF_SECONDS = 30
_MAX_BACKOFF_SECONDS = 3600

# Crash-window A (worker claims an event, commits 'processing', then dies
# before calling the handler): process_pending automatically reclaims any
# event that has sat 'processing' longer than this on every run, so a
# crashed claim self-heals on the next periodic invocation without an
# operator having to remember `--reclaim-stuck-minutes`. Comfortably
# longer than any realistic single handler call (Twilio's own HTTP
# timeout is 15s - see services/driver_sms_service.py) so a merely-slow,
# still-alive worker is never falsely reclaimed and double-processed
# while genuinely still working.
RECLAIM_STALE_AFTER = timedelta(minutes=10)


def _backoff_for(attempt_count: int) -> timedelta:
    """Exponential backoff, capped - not a full scheduler, just enough to
    avoid hammering a provider that's already failing (30s, 60s, 120s,
    240s, ... capped at 1 hour)."""
    seconds = min(_BASE_BACKOFF_SECONDS * (2**attempt_count), _MAX_BACKOFF_SECONDS)
    return timedelta(seconds=seconds)


def _handle_driver_dispatch_sms(payload: dict[str, Any]) -> tuple[bool, str | None, str]:
    """Handler for event_type='driver_dispatch_sms' - the one event type
    Phase 6 converts (application/loads/commands.py::
    mark_load_ready_to_dispatch). Returns (success, provider_message_id,
    error) - error is "" on success, never None, so callers can always
    str() it safely."""
    from services.driver_sms_service import send_sms

    sent, sid_or_error = send_sms(payload["to"], payload["message"])
    if sent:
        return True, sid_or_error, ""
    return False, None, str(sid_or_error)


# event_type -> handler(payload) -> (success, provider_message_id, error).
# Registering a new event_type here is the whole integration point for a
# future side effect (email, Motive, webhooks) - see docs/architecture/
# OUTBOX.md's "Future integrations" section for the naming convention
# (e.g. "motive.load.sync") new event types should follow.
_EVENT_HANDLERS: dict[str, Callable[[dict[str, Any]], tuple[bool, str | None, str]]] = {
    "driver_dispatch_sms": _handle_driver_dispatch_sms,
}


def _parse_payload(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else json.loads(raw)


def _project_delivery_status(conn, payload: dict[str, Any], outcome: str, provider_message_id: str | None) -> None:
    """Project the outbox result onto the dispatch_messages row it
    accompanies, if the payload references one - not every event_type
    will (only driver_dispatch_sms does today)."""
    dispatch_message_id = payload.get("dispatch_message_id")
    if dispatch_message_id is None:
        return

    from services.dispatch_data_service import update_dispatch_message_delivery

    delivery_status = {"delivered": "delivered", "retrying": "pending_delivery", "failed": "failed"}[outcome]
    update_dispatch_message_delivery(
        int(dispatch_message_id), conn=conn, delivery_status=delivery_status, provider_message_id=provider_message_id
    )


def process_one() -> dict[str, Any] | None:
    """Claim and process exactly one pending, due outbox event. Returns a
    small result dict for logging/observability, or None if nothing was
    claimable right now (an empty queue is not an error)."""
    with transaction() as conn:
        event = outbox_repo.claim_next_pending(conn)
    if event is None:
        return None

    handler = _EVENT_HANDLERS.get(event["event_type"])
    payload = _parse_payload(event["payload"])

    if handler is None:
        error = f"No handler registered for event_type={event['event_type']!r}."
        with transaction() as conn:
            outbox_repo.mark_failed(conn, event["id"], error=error)
        return {"id": event["id"], "event_type": event["event_type"], "outcome": "failed", "error": error}

    try:
        success, provider_message_id, error = handler(payload)
        # Sanitized here too, not just on the exception path below: a
        # handler's own returned failure string (e.g. a provider's raw
        # error response body) is not an exception's str() and so never
        # passes through sanitize_exception_message otherwise - every
        # write to outbox_events.last_error goes through this one choke
        # point regardless of which path produced the error text.
        error = sanitize_message(error) if error else error
    except Exception as exc:  # noqa: BLE001 - a handler bug must not crash the whole processor loop
        success, provider_message_id, error = False, None, sanitize_exception_message(exc)

    with transaction() as conn:
        if success:
            outbox_repo.mark_delivered(conn, event["id"], provider_message_id=provider_message_id)
            outcome = "delivered"
        elif event["attempt_count"] + 1 >= MAX_ATTEMPTS:
            outbox_repo.mark_failed(conn, event["id"], error=error)
            outcome = "failed"
        else:
            outbox_repo.mark_retry(conn, event["id"], error=error, delay=_backoff_for(event["attempt_count"]))
            outcome = "retrying"

        _project_delivery_status(conn, payload, outcome, provider_message_id)

    return {"id": event["id"], "event_type": event["event_type"], "outcome": outcome, "error": error}


def process_pending(*, max_events: int = 50) -> list[dict[str, Any]]:
    """Process up to max_events pending events, stopping early once
    nothing is claimable. This is the whole "processor" for this phase -
    no daemon, no scheduler loop (task scope explicitly avoids that,
    "a simple processor callable periodically is sufficient initially") -
    scripts/process_outbox.py calls this once per invocation; an
    operator/cron/Task Scheduler runs it periodically.

    Reclaims stale 'processing' events (see RECLAIM_STALE_AFTER) before
    claiming anything new - crash-window A's fix. An operator can still
    force a different threshold via `scripts/process_outbox.py
    --reclaim-stuck-minutes N` before this runs."""
    with transaction() as conn:
        outbox_repo.reclaim_stuck_processing(conn, older_than=RECLAIM_STALE_AFTER)

    results = []
    for _ in range(max_events):
        result = process_one()
        if result is None:
            break
        results.append(result)
    return results
