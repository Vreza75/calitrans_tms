# realtime/events.py

from __future__ import annotations

"""Phase 9 STEP 3: the one framework-neutral write-side entry point
application commands/services use to record a domain/change event.
No Streamlit import, no FastAPI import, no Supabase-specific payload
shape leaks through here - callers only ever see this function's plain
keyword arguments.

publish_event() does not itself talk to any realtime transport - it only
persists a domain_events row (repositories/domain_event_repo.py) using
the caller's own open transaction (`conn` is required, same convention
as repositories/outbox_repo.py::enqueue_outbox_event and repositories/
worker_job_repo.py::enqueue_job), so the event is durably recorded
exactly when the business transaction that produced it commits (STEP 6).
The actual broadcast to connected clients happens later, out-of-band, via
services/realtime_publisher.py - see that module and realtime/
publisher.py for the transport-facing half.

Named publish_event to match the Phase 9 spec's conceptual API, even
though "publish" happens later, asynchronously - the name describes the
caller's intent ("tell clients about this"), not the literal synchronous
effect of this call."""

import hashlib
import time
from typing import Any

from sqlalchemy.engine import Connection

from repositories.domain_event_repo import enqueue_domain_event

# Default dedupe window for time-bucketed idempotency keys (see
# time_bucketed_key below) - same value and same rationale as
# application/loads/commands.py::_RETRY_DEDUPE_WINDOW_SECONDS: long
# enough to collapse a genuine same-click retry, short enough that a
# later, legitimately distinct change to the same aggregate still gets
# its own event.
_DEFAULT_DEDUPE_WINDOW_SECONDS = 300


def time_bucketed_key(*parts: str, window_seconds: int = _DEFAULT_DEDUPE_WINDOW_SECONDS, now: float | None = None) -> str:
    """Content-addressed AND time-windowed idempotency key, for emitters
    whose triggering command can legitimately be retried (a network
    timeout, a double-click) but must not permanently suppress a later,
    genuinely distinct occurrence of the same event. Same "neither pure
    content nor pure timestamp alone" reasoning as application/loads/
    commands.py::_driver_dispatch_sms_idempotency_key - see that
    function's docstring for the full rationale. `now` is injectable for
    tests."""
    bucket = int((now if now is not None else time.time()) // window_seconds)
    return hashlib.sha256(":".join((*parts, str(bucket))).encode("utf-8")).hexdigest()


def publish_event(
    *,
    conn: Connection,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    idempotency_key: str,
    version: str | None = None,
    actor: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record one domain/change event in the caller's transaction.

    `idempotency_key` is required, not derived here - only the caller
    knows what "the same logical event, retried" means for its own
    command (same pattern as enqueue_outbox_event/enqueue_job: this
    module does not guess a dedup key on the caller's behalf).

    `metadata` becomes the event's payload. Callers must not put
    sensitive fields in it - see realtime/channels.py::
    assert_no_sensitive_metadata, which services/realtime_publisher.py
    runs before every broadcast as a defense-in-depth check (STEP 16)."""
    from realtime.channels import assert_no_sensitive_metadata

    payload = metadata or {}
    assert_no_sensitive_metadata(payload)

    enqueue_domain_event(
        conn=conn,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        payload=payload,
        idempotency_key=idempotency_key,
        version=version,
        actor=actor,
    )
