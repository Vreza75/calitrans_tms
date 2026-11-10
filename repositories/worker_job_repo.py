# repositories/worker_job_repo.py

from __future__ import annotations

"""Phase 7: durable worker job queue persistence. See
database/worker_jobs_migration.sql for the worker_jobs table this module
reads/writes.

Framework-neutral: no Streamlit import, no rendering, no session state -
callable from application commands (Streamlit + API) and from the
standalone worker runtime (workers/processor.py, scripts/
process_worker_jobs.py) alike.

Distinct from repositories/outbox_repo.py: outbox_events represents "a
business transaction committed; an external side effect must happen"
(SMS, future email/Motive/webhooks). worker_jobs represents "internal
asynchronous work must be performed" (mailbox sync, message processing) -
no external provider, no business transaction necessarily preceding it.
Same proven schema/lifecycle shape as outbox_repo.py (claim/retry/
backoff/reclaim), deliberately kept as a separate table/module rather
than overloading outbox_events for both concepts.

Two distinct call shapes, deliberately (same split as outbox_repo.py):
  - enqueue_job(conn=...) - conn is REQUIRED. If enqueued alongside a
    business-state write, must be the same connection/transaction (see
    application/inbox/commands.py::request_inbox_sync) - this function
    never opens or commits its own transaction.
  - claim_next_job / mark_completed / mark_retry / mark_failed - conn is
    REQUIRED too, but each of these is its own short-lived transaction
    (see workers/processor.py) - the worker must not hold a transaction
    open across the actual job work (email fetch, parsing, AI calls),
    only around the claim and around the terminal status write.
"""

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def enqueue_job(
    *,
    conn: Connection,
    job_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    actor: str | None = None,
) -> int:
    """Insert one worker_jobs row using the caller's open transaction.
    Returns the job's id - either the freshly inserted row's, or the
    already-existing row's if this idempotency_key was already enqueued
    (see below). A caller like application/inbox/commands.py::
    request_inbox_sync needs this id back to report which job a sync
    request resolved to.

    ON CONFLICT (idempotency_key) DO UPDATE ... RETURNING id: enqueueing
    the same logical job twice (e.g. a retried "Sync Inbox" click, or the
    same provider message id arriving twice) resolves to the existing row,
    not a duplicate insert - the "do update set idempotency_key =
    excluded.idempotency_key" is a no-op write (re-sets the column to its
    own value) used only so RETURNING always yields a row, including on
    conflict; it never touches status/attempt_count/any other column of
    an existing job."""
    result = conn.execute(
        text(
            """
            insert into worker_jobs
                (job_type, aggregate_type, aggregate_id, payload, idempotency_key, actor)
            values
                (:job_type, :aggregate_type, :aggregate_id, cast(:payload as jsonb), :idempotency_key, :actor)
            on conflict (idempotency_key) do update set idempotency_key = excluded.idempotency_key
            returning id
            """
        ),
        {
            "job_type": job_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id),
            "payload": json.dumps(payload, default=str),
            "idempotency_key": idempotency_key,
            "actor": actor,
        },
    )
    return int(result.scalar_one())


def claim_next_job(conn: Connection) -> dict[str, Any] | None:
    """Atomically claim one pending, due job and mark it 'processing' -
    caller must commit this short transaction immediately (see
    workers/processor.py's claim step) before performing the actual work,
    so no transaction sits open across email retrieval/parsing/AI calls.

    FOR UPDATE SKIP LOCKED (PostgreSQL-only - not supported by SQLite,
    which unit tests use for everything else in this module) lets
    multiple worker processes poll the same table concurrently: each
    claims a different row instead of blocking on or double-claiming the
    same one. A crashed worker that claimed a row but never marked it
    completed/retry/failed leaves that row 'processing' forever unless
    something recovers it - see reclaim_stuck_processing below."""
    row = conn.execute(
        text(
            """
            select id, job_type, aggregate_type, aggregate_id, payload, attempt_count, actor
            from worker_jobs
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
        text("update worker_jobs set status = 'processing', claimed_at = now() where id = :id"),
        {"id": row["id"]},
    )
    return dict(row)


def reclaim_stuck_processing(conn: Connection, *, older_than: timedelta) -> int:
    """Recover jobs stuck in 'processing' because the worker that claimed
    them crashed or was killed before recording a result - resets them to
    'pending' so a future claim can retry.

    Uses claimed_at, not created_at: a row can sit 'pending' for a long
    time across earlier retries before this particular claim happens, so
    created_at is already stale by the time this claim needs bounding -
    using it would reclaim a row that was claimed a second ago just
    because it was originally created hours ago."""
    result = conn.execute(
        text(
            """
            update worker_jobs
            set status = 'pending', claimed_at = null
            where status = 'processing' and claimed_at < :cutoff
            """
        ),
        {"cutoff": datetime.utcnow() - older_than},
    )
    return result.rowcount or 0


def mark_completed(conn: Connection, job_id: int) -> None:
    conn.execute(
        text(
            """
            update worker_jobs
            set status = 'completed', completed_at = now(), claimed_at = null, last_error = null
            where id = :id
            """
        ),
        {"id": job_id},
    )


def mark_retry(conn: Connection, job_id: int, *, error: str, delay: timedelta) -> None:
    """Push the job back to 'pending' with a future available_at and an
    incremented attempt_count - the next claim (by this worker or another)
    picks it up once the delay elapses. Error text passed through
    utils.error_sanitizer by the worker processor before it reaches this
    function - this module does not sanitize, it just persists."""
    conn.execute(
        text(
            """
            update worker_jobs
            set status = 'pending',
                attempt_count = attempt_count + 1,
                available_at = now() + make_interval(secs => :delay_seconds),
                claimed_at = null,
                last_error = :error
            where id = :id
            """
        ),
        {"id": job_id, "delay_seconds": delay.total_seconds(), "error": error},
    )


def mark_failed(conn: Connection, job_id: int, *, error: str) -> None:
    """Terminal state - attempt_count reached the processor's configured
    max. Requires an operator to look at last_error and decide (resend
    manually via retry_job below, or leave it) - the processor never
    retries a 'failed' row on its own."""
    conn.execute(
        text(
            """
            update worker_jobs
            set status = 'failed', attempt_count = attempt_count + 1, claimed_at = null, last_error = :error
            where id = :id
            """
        ),
        {"id": job_id, "error": error},
    )


# ---------------------------------------------------------------------------
# Operator recovery tooling - see scripts/process_worker_jobs.py for the CLI
# surface. Every function here takes only an integer id/status/limit, never
# free text that could shape a query - no injection surface from CLI args.
# ---------------------------------------------------------------------------

_JOB_COLUMNS = (
    "id, job_type, aggregate_type, aggregate_id, status, attempt_count, "
    "available_at, created_at, claimed_at, completed_at, last_error, actor"
)


def list_by_status(conn: Connection, status: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """List jobs in `status`, most recent first. Deliberately excludes
    `payload` - list output is for a quick operator scan, not for reading
    message content; use get_job for that."""
    rows = conn.execute(
        text(f"select {_JOB_COLUMNS} from worker_jobs where status = :status order by id desc limit :limit"),
        {"status": status, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_job(conn: Connection, job_id: int) -> dict[str, Any] | None:
    """Full row, including payload - for inspecting one specific job by
    id, not for bulk listing (see list_by_status)."""
    row = conn.execute(
        text(f"select {_JOB_COLUMNS}, payload from worker_jobs where id = :id"), {"id": job_id}
    ).mappings().first()
    return dict(row) if row else None


def retry_job(conn: Connection, job_id: int, *, reset_attempts: bool = False) -> bool:
    """Manually requeue one 'failed' job - an explicit operator action,
    never automatic. Only rows currently 'failed' are eligible (a
    'pending'/'processing' row doesn't need this - it's already going to
    be picked up or is mid-flight). Preserves the row's identity (same id,
    same idempotency_key - never inserts a new row) and, by default,
    its attempt_count (attempt history) - pass reset_attempts=True to also
    zero it. Returns False if no 'failed' row with this id existed (no-op,
    not an error - lets the CLI report "nothing to do")."""
    if reset_attempts:
        sql = (
            "update worker_jobs set status='pending', available_at=now(), attempt_count=0, last_error=null "
            "where id=:id and status='failed'"
        )
    else:
        sql = "update worker_jobs set status='pending', available_at=now() where id=:id and status='failed'"
    result = conn.execute(text(sql), {"id": job_id})
    return (result.rowcount or 0) > 0


def count_by_status(conn: Connection) -> dict[str, int]:
    """Aggregate queue depth per status - for a minimal operator/health
    view (Phase 7 STEP 5), not a monitoring platform. Statuses with zero
    rows are simply absent from the returned dict rather than present
    with a 0 - callers that want every known status represented should
    default-fill (see application/jobs/queries.py::get_worker_runtime_status)."""
    rows = conn.execute(text("select status, count(*) as n from worker_jobs group by status")).mappings().all()
    return {row["status"]: int(row["n"]) for row in rows}


def retry_all_failed(conn: Connection, *, reset_attempts: bool = False) -> int:
    """Bulk version of retry_job - requeues every currently-'failed' row.
    Returns the number of rows requeued."""
    if reset_attempts:
        sql = "update worker_jobs set status='pending', available_at=now(), attempt_count=0, last_error=null where status='failed'"
    else:
        sql = "update worker_jobs set status='pending', available_at=now() where status='failed'"
    result = conn.execute(text(sql))
    return result.rowcount or 0


def has_active_job(conn: Connection, job_type: str) -> bool:
    """True if a job of this type is currently 'pending' or 'processing'
    (not yet terminal). The scheduled inbox.sync producer
    (application/inbox/commands.py::request_scheduled_inbox_sync) uses this
    to avoid stacking a new sync on top of one still queued or mid-fetch -
    overlap prevention, distinct from the idempotency-key window (which only
    collapses same-bucket re-enqueues, not a slow-processing backlog).
    Read-only: no claim, no mutation."""
    row = conn.execute(
        text(
            "select 1 from worker_jobs "
            "where job_type = :job_type and status in ('pending', 'processing') limit 1"
        ),
        {"job_type": job_type},
    ).first()
    return row is not None


def job_exists_for_idempotency_key(conn: Connection, idempotency_key: str) -> bool:
    """True if a worker_jobs row already carries this idempotency_key, in
    ANY status (pending/processing/completed/failed). The scheduled
    inbox.sync producer uses this to report an honest "skipped" when a job
    for the current interval window already exists, instead of routing a
    same-window repeat through enqueue_job - whose ON CONFLICT (idempotency_key)
    DO UPDATE is a deliberate no-op (it re-sets the key to its own value and
    returns the existing row's id without ever reverting a completed/failed
    job to pending), which would otherwise be misreported as a fresh
    "queued" enqueue. Read-only."""
    row = conn.execute(
        text("select 1 from worker_jobs where idempotency_key = :key limit 1"),
        {"key": idempotency_key},
    ).first()
    return row is not None


def get_job_type_health(conn: Connection, job_type: str) -> dict[str, Any]:
    """Sanitized health snapshot for one job_type: per-status backlog, the
    last successful completion and that run's processing duration, and the
    most recent failure's already-sanitized last_error (workers/processor.py
    sanitizes before persisting last_error - this never exposes raw SQL,
    credentials, or message bodies). Read-only."""
    counts = conn.execute(
        text("select status, count(*) as n from worker_jobs where job_type = :job_type group by status"),
        {"job_type": job_type},
    ).mappings().all()
    by_status = {row["status"]: int(row["n"]) for row in counts}

    last_success = conn.execute(
        text(
            "select completed_at, extract(epoch from (completed_at - claimed_at)) as duration_seconds "
            "from worker_jobs where job_type = :job_type and status = 'completed' "
            "order by completed_at desc nulls last limit 1"
        ),
        {"job_type": job_type},
    ).mappings().first()

    last_failure = conn.execute(
        text(
            "select last_error from worker_jobs "
            "where job_type = :job_type and status = 'failed' order by id desc limit 1"
        ),
        {"job_type": job_type},
    ).mappings().first()

    return {
        "job_type": job_type,
        "pending": by_status.get("pending", 0),
        "processing": by_status.get("processing", 0),
        "completed": by_status.get("completed", 0),
        "failed": by_status.get("failed", 0),
        "last_success_at": last_success["completed_at"] if last_success else None,
        "last_duration_seconds": (
            float(last_success["duration_seconds"])
            if last_success and last_success["duration_seconds"] is not None
            else None
        ),
        "last_error": last_failure["last_error"] if last_failure else None,
    }
