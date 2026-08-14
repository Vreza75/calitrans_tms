# repositories/outbox_repo.py

from __future__ import annotations

"""Phase 6: transactional outbox persistence. See
database/outbox_migration.sql for the outbox_events table this module
reads/writes.

Framework-neutral: no Streamlit import, no rendering, no session state -
callable from application commands (Streamlit + API) and from the
standalone processor (scripts/process_outbox.py) alike.

Two distinct call shapes, deliberately:
  - enqueue_outbox_event(conn=...) - conn is REQUIRED. Must be the same
    connection/transaction as the business-state write it accompanies
    (db_client.transaction()) - this function never opens or commits its
    own transaction. Calling it outside a caller-managed transaction is a
    programming error, not a convenience default.
  - claim_next_pending / mark_delivered / mark_retry / mark_failed -
    conn is REQUIRED too, but each of these is its own short-lived
    transaction (see services/outbox_processor.py) - the processor must
    not hold a transaction open across the external network call, only
    around the claim and around the terminal status write.
"""

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def enqueue_outbox_event(
    *,
    conn: Connection,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    actor: str | None = None,
) -> None:
    """Insert one outbox row using the caller's open transaction.

    ON CONFLICT (idempotency_key) DO NOTHING: enqueueing the same logical
    event twice (e.g. a command retried after a network timeout, resubmitting
    identical content) is a no-op, not a duplicate row - see each command's
    idempotency-key construction for what "same logical event" means for
    that event type."""
    conn.execute(
        text(
            """
            insert into outbox_events
                (event_type, aggregate_type, aggregate_id, payload, idempotency_key, actor)
            values
                (:event_type, :aggregate_type, :aggregate_id, cast(:payload as jsonb), :idempotency_key, :actor)
            on conflict (idempotency_key) do nothing
            """
        ),
        {
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id),
            "payload": json.dumps(payload, default=str),
            "idempotency_key": idempotency_key,
            "actor": actor,
        },
    )


def claim_next_pending(conn: Connection) -> dict[str, Any] | None:
    """Atomically claim one pending, due event and mark it 'processing' -
    caller must commit this short transaction immediately (see
    services/outbox_processor.py's claim step) before performing the
    external side effect, so no transaction sits open across a network
    call.

    FOR UPDATE SKIP LOCKED (PostgreSQL-only - not supported by SQLite,
    which unit tests use for everything else in this module) lets
    multiple worker processes poll the same table concurrently: each
    claims a different row instead of blocking on or double-claiming the
    same one. A crashed worker that claimed a row but never marked it
    delivered/retry/failed leaves that row 'processing' forever unless
    something recovers it - see reclaim_stuck_processing below."""
    row = conn.execute(
        text(
            """
            select id, event_type, aggregate_type, aggregate_id, payload, attempt_count, actor
            from outbox_events
            where status = 'pending' and available_at <= now()
            order by id
            for update skip locked
            limit 1
            """
        )
    ).mappings().first()
    if row is None:
        return None

    conn.execute(
        text("update outbox_events set status = 'processing', claimed_at = now() where id = :id"),
        {"id": row["id"]},
    )
    return dict(row)


def reclaim_stuck_processing(conn: Connection, *, older_than: timedelta) -> int:
    """Recover events stuck in 'processing' because the worker that
    claimed them crashed or was killed before recording a result - resets
    them to 'pending' so a future claim can retry.

    Uses claimed_at, not created_at: a row can sit 'pending' for a long
    time across earlier retries before this particular claim happens, so
    created_at is already stale by the time this claim needs bounding -
    using it would reclaim a row that was claimed a second ago just
    because it was originally created hours ago.

    Called automatically by services.outbox_processor.process_pending at
    the start of every run (see RECLAIM_STALE_AFTER there) - a crashed
    worker's claimed-but-unprocessed event self-heals on the next
    periodic run without requiring an operator to remember a flag. Also
    callable directly/via `scripts/process_outbox.py
    --reclaim-stuck-minutes` for an operator who wants a different
    threshold than the built-in default right now."""
    result = conn.execute(
        text(
            """
            update outbox_events
            set status = 'pending', claimed_at = null
            where status = 'processing' and claimed_at < :cutoff
            """
        ),
        {"cutoff": datetime.utcnow() - older_than},
    )
    return result.rowcount or 0


def mark_delivered(conn: Connection, event_id: int, *, provider_message_id: str | None = None) -> None:
    conn.execute(
        text(
            """
            update outbox_events
            set status = 'delivered', processed_at = now(), claimed_at = null, last_error = null
            where id = :id
            """
        ),
        {"id": event_id},
    )


def mark_retry(conn: Connection, event_id: int, *, error: str, delay: timedelta) -> None:
    """Push the event back to 'pending' with a future available_at and an
    incremented attempt_count - the next claim (by this worker or another)
    picks it up once the delay elapses. Error text passed through
    utils.error_sanitizer by the processor before it reaches this
    function - this module does not sanitize, it just persists."""
    conn.execute(
        text(
            """
            update outbox_events
            set status = 'pending',
                attempt_count = attempt_count + 1,
                available_at = now() + make_interval(secs => :delay_seconds),
                claimed_at = null,
                last_error = :error
            where id = :id
            """
        ),
        {"id": event_id, "delay_seconds": delay.total_seconds(), "error": error},
    )


def mark_failed(conn: Connection, event_id: int, *, error: str) -> None:
    """Terminal state - attempt_count reached the processor's configured
    max. Requires an operator to look at last_error and decide (resend
    manually via retry_event below, or leave it) - the processor never
    retries a 'failed' row on its own."""
    conn.execute(
        text(
            """
            update outbox_events
            set status = 'failed', attempt_count = attempt_count + 1, claimed_at = null, last_error = :error
            where id = :id
            """
        ),
        {"id": event_id, "error": error},
    )


# ---------------------------------------------------------------------------
# Operator recovery tooling - see scripts/process_outbox.py for the CLI
# surface. Every function here takes only an integer id/status/limit, never
# free text that could shape a query - no injection surface from CLI args.
# ---------------------------------------------------------------------------

_EVENT_COLUMNS = (
    "id, event_type, aggregate_type, aggregate_id, status, attempt_count, "
    "available_at, created_at, claimed_at, processed_at, last_error, actor"
)


def list_by_status(conn: Connection, status: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """List events in `status`, most recent first. Deliberately excludes
    `payload` - list output is for a quick operator scan, not for reading
    message content; use get_event for that."""
    rows = conn.execute(
        text(f"select {_EVENT_COLUMNS} from outbox_events where status = :status order by id desc limit :limit"),
        {"status": status, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_event(conn: Connection, event_id: int) -> dict[str, Any] | None:
    """Full row, including payload - for inspecting one specific event by
    id, not for bulk listing (see list_by_status)."""
    row = conn.execute(
        text(f"select {_EVENT_COLUMNS}, payload from outbox_events where id = :id"), {"id": event_id}
    ).mappings().first()
    return dict(row) if row else None


def retry_event(conn: Connection, event_id: int, *, reset_attempts: bool = False) -> bool:
    """Manually requeue one 'failed' event - an explicit operator action,
    never automatic. Only rows currently 'failed' are eligible (a
    'pending'/'processing' row doesn't need this - it's already going to
    be picked up or is mid-flight). Preserves the row's identity (same id,
    same idempotency_key - never inserts a new row) and, by default,
    its attempt_count (attempt history) - pass reset_attempts=True to also
    zero it (e.g. after fixing a known root cause, so the event gets a
    full fresh set of attempts instead of immediately re-terminaling on
    the next failure). Returns False if no 'failed' row with this id
    existed (no-op, not an error - lets the CLI report "nothing to do")."""
    if reset_attempts:
        sql = (
            "update outbox_events set status='pending', available_at=now(), attempt_count=0, last_error=null "
            "where id=:id and status='failed'"
        )
    else:
        sql = "update outbox_events set status='pending', available_at=now() where id=:id and status='failed'"
    result = conn.execute(text(sql), {"id": event_id})
    return (result.rowcount or 0) > 0


def count_by_status(conn: Connection) -> dict[str, int]:
    """Aggregate queue depth per status - Phase 7 closure addition for a
    minimal operator/health view (application/jobs/queries.py::
    get_worker_runtime_status), mirroring repositories/worker_job_repo.py::
    count_by_status. Statuses with zero rows are absent from the dict."""
    rows = conn.execute(text("select status, count(*) as n from outbox_events group by status")).mappings().all()
    return {row["status"]: int(row["n"]) for row in rows}


def retry_all_failed(conn: Connection, *, reset_attempts: bool = False) -> int:
    """Bulk version of retry_event - requeues every currently-'failed'
    row. Returns the number of rows requeued."""
    if reset_attempts:
        sql = "update outbox_events set status='pending', available_at=now(), attempt_count=0, last_error=null where status='failed'"
    else:
        sql = "update outbox_events set status='pending', available_at=now() where status='failed'"
    result = conn.execute(text(sql))
    return result.rowcount or 0
