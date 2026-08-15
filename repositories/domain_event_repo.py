# repositories/domain_event_repo.py

from __future__ import annotations

"""Phase 9: durable domain/change event persistence. See
database/domain_events_migration.sql for the domain_events table this
module reads/writes.

Framework-neutral: no Streamlit import - callable from application
commands/services (Streamlit + API) and from the standalone realtime
publisher (services/realtime_publisher.py, scripts/
process_realtime_events.py) alike.

Same shape as repositories/outbox_repo.py and repositories/
worker_job_repo.py, deliberately: proven claim/retry/backoff/reclaim
lifecycle, not reinvented. See database/domain_events_migration.sql's
header comment for why this is a third, separate table rather than
reusing either of those two.

Two distinct call shapes, deliberately (same split as outbox_repo.py):
  - enqueue_domain_event(conn=...) - conn is REQUIRED. Must be the same
    connection/transaction as the business-state write it accompanies -
    this function never opens or commits its own transaction. Calling it
    outside a caller-managed transaction is a programming error.
  - claim_next_pending / mark_published / mark_retry / mark_failed - conn
    is REQUIRED too, but each of these is its own short-lived transaction
    (see services/realtime_publisher.py) - the publisher must not hold a
    transaction open across the network call to the broadcast provider,
    only around the claim and around the terminal status write.
"""

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def enqueue_domain_event(
    *,
    conn: Connection,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    version: str | None = None,
    actor: str | None = None,
) -> None:
    """Insert one domain_events row using the caller's open transaction.

    ON CONFLICT (idempotency_key) DO NOTHING: recording the same logical
    event twice (e.g. a command retried after a network timeout) is a
    no-op, not a duplicate row - see each emitter's idempotency-key
    construction for what "same logical event" means for that event
    type."""
    conn.execute(
        text(
            """
            insert into domain_events
                (event_type, aggregate_type, aggregate_id, version, payload, idempotency_key, actor)
            values
                (:event_type, :aggregate_type, :aggregate_id, :version, cast(:payload as jsonb), :idempotency_key, :actor)
            on conflict (idempotency_key) do nothing
            """
        ),
        {
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id),
            "version": version,
            "payload": json.dumps(payload, default=str),
            "idempotency_key": idempotency_key,
            "actor": actor,
        },
    )


def claim_next_pending(conn: Connection) -> dict[str, Any] | None:
    """Atomically claim one pending, due event and mark it 'processing' -
    caller must commit this short transaction immediately before
    performing the broadcast call, so no transaction sits open across a
    network call.

    FOR UPDATE SKIP LOCKED (PostgreSQL-only) lets multiple publisher
    processes poll the same table concurrently: each claims a different
    row instead of blocking on or double-claiming the same one."""
    row = conn.execute(
        text(
            """
            select id, event_type, aggregate_type, aggregate_id, version, payload, attempt_count, actor, created_at
            from domain_events
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
        text("update domain_events set status = 'processing', claimed_at = now() where id = :id"),
        {"id": row["id"]},
    )
    return dict(row)


def reclaim_stuck_processing(conn: Connection, *, older_than: timedelta) -> int:
    """Recover events stuck in 'processing' because the publisher that
    claimed them crashed before recording a result - resets them to
    'pending' so a future claim can retry. Uses claimed_at, not
    created_at - same reasoning as repositories/outbox_repo.py's
    identically-named function."""
    result = conn.execute(
        text(
            """
            update domain_events
            set status = 'pending', claimed_at = null
            where status = 'processing' and claimed_at < :cutoff
            """
        ),
        {"cutoff": datetime.utcnow() - older_than},
    )
    return result.rowcount or 0


def mark_published(conn: Connection, event_id: int) -> None:
    conn.execute(
        text(
            """
            update domain_events
            set status = 'published', published_at = now(), claimed_at = null, last_error = null
            where id = :id
            """
        ),
        {"id": event_id},
    )


def mark_retry(conn: Connection, event_id: int, *, error: str, delay: timedelta) -> None:
    """Push the event back to 'pending' with a future available_at and an
    incremented attempt_count. Error text passed through
    utils.error_sanitizer by the publisher before it reaches this
    function - this module does not sanitize, it just persists."""
    conn.execute(
        text(
            """
            update domain_events
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
    """Terminal state - attempt_count reached the publisher's configured
    max. Unlike outbox_events' mark_failed, this is not a business-
    correctness emergency (see this module's docstring) - it just means a
    client may miss one realtime notification and rely on its next
    manual refetch/cache-expiry cycle instead."""
    conn.execute(
        text(
            """
            update domain_events
            set status = 'failed', attempt_count = attempt_count + 1, claimed_at = null, last_error = :error
            where id = :id
            """
        ),
        {"id": event_id, "error": error},
    )


# ---------------------------------------------------------------------------
# Operator/CLI tooling - see scripts/process_realtime_events.py. Every
# function here takes only an integer id/status/limit, never free text that
# could shape a query - no injection surface from CLI args.
# ---------------------------------------------------------------------------

_EVENT_COLUMNS = (
    "id, event_type, aggregate_type, aggregate_id, version, status, attempt_count, "
    "available_at, created_at, claimed_at, published_at, last_error, actor"
)


def list_by_status(conn: Connection, status: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """List events in `status`, most recent first. Deliberately excludes
    `payload` - list output is for a quick operator scan; use get_event
    for full detail."""
    rows = conn.execute(
        text(f"select {_EVENT_COLUMNS} from domain_events where status = :status order by id desc limit :limit"),
        {"status": status, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_event(conn: Connection, event_id: int) -> dict[str, Any] | None:
    """Full row, including payload - for inspecting one specific event by
    id."""
    row = conn.execute(
        text(f"select {_EVENT_COLUMNS}, payload from domain_events where id = :id"), {"id": event_id}
    ).mappings().first()
    return dict(row) if row else None


def retry_event(conn: Connection, event_id: int, *, reset_attempts: bool = False) -> bool:
    """Manually requeue one 'failed' event - an explicit operator action.
    Only rows currently 'failed' are eligible. Preserves the row's
    identity (same id, same idempotency_key)."""
    if reset_attempts:
        sql = (
            "update domain_events set status='pending', available_at=now(), attempt_count=0, last_error=null "
            "where id=:id and status='failed'"
        )
    else:
        sql = "update domain_events set status='pending', available_at=now() where id=:id and status='failed'"
    result = conn.execute(text(sql), {"id": event_id})
    return (result.rowcount or 0) > 0


def count_by_status(conn: Connection) -> dict[str, int]:
    """Aggregate queue depth per status - minimal operator/health view,
    mirroring repositories/outbox_repo.py::count_by_status and
    repositories/worker_job_repo.py::count_by_status. Statuses with zero
    rows are absent from the dict."""
    rows = conn.execute(text("select status, count(*) as n from domain_events group by status")).mappings().all()
    return {row["status"]: int(row["n"]) for row in rows}


def retry_all_failed(conn: Connection, *, reset_attempts: bool = False) -> int:
    """Bulk version of retry_event - requeues every currently-'failed'
    row. Returns the number of rows requeued."""
    if reset_attempts:
        sql = "update domain_events set status='pending', available_at=now(), attempt_count=0, last_error=null where status='failed'"
    else:
        sql = "update domain_events set status='pending', available_at=now() where status='failed'"
    result = conn.execute(text(sql))
    return result.rowcount or 0
