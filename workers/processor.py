# workers/processor.py

from __future__ import annotations

"""Phase 7: durable worker job processor. Framework-neutral - no
Streamlit import - runnable from scripts/process_worker_jobs.py, a
future dedicated worker process, or a scheduled job. Not embedded in
Streamlit rendering.

Lifecycle per job (see repositories/worker_job_repo.py's module
docstring for why claim and result are two separate short transactions):

    claim (short transaction, commits immediately)
        -> perform the job's actual work (no open transaction during this -
           email retrieval, PDF parsing, AI calls, network requests)
    result (short transaction: worker_jobs status, and - once a handler
            needs it - any accompanying domain-state projection, same
            _PROJECTORS pattern as services/outbox_processor.py; no
            handler needs one yet, so none exists here)

A crash between claim-commit and result-commit leaves the job
'processing' - repositories.worker_job_repo.reclaim_stuck_processing
recovers those automatically at the start of every process_pending() run
(see RECLAIM_STALE_AFTER below), not only via an operator's explicit
call.

Handler contract - IMPORTANT (Phase 7 design principle: distinguish
processing failure from review outcome): a handler returns
(success, error) where `success=False` means the job's own code failed
to execute (infrastructure/program error - network unreachable, parser
raised, missing config) and should retry/eventually terminal-fail.
Business ambiguity a handler successfully processes (e.g. "this email
was fetched, parsed, and classified, but the booking number is
ambiguous") is NOT a handler failure - the handler returns
success=True and encodes that ambiguity in whatever domain state it
writes (e.g. a work item's review_status = 'Needs Review'), the same
way services.operations_inbox_service already distinguishes "processed
successfully, flagged for human review" from an actual exception today.
workers/inbox_handlers.py::handle_inbox_sync follows this contract
already; a future inbox.process_message handler must too - not report
success=False for a parse it completed but couldn't fully resolve - see
docs/architecture/WORKER_RUNTIME.md.

Delivery guarantee, same as services/outbox_processor.py: **at-least-
once**, not exactly-once. If a worker crashes after a handler's work
genuinely completed but before mark_completed() commits, the job is
later reclaimed as stale and reprocessed - handlers must be safe to run
more than once for the same logical job (idempotent effects), the same
requirement Phase 6B's document finalize met by checking "does the
final file already exist" before acting.
"""

import json
from datetime import timedelta
from typing import Any, Callable

from db_client import transaction
from repositories import worker_job_repo
from utils.error_sanitizer import sanitize_exception_message, sanitize_message
from workers.inbox_handlers import handle_inbox_process_message, handle_inbox_sync

# Bounded retry: after MAX_ATTEMPTS failed attempts, a job moves to the
# terminal 'failed' state instead of retrying forever - an operator must
# look at it (see repositories.worker_job_repo.mark_failed's docstring).
# Same policy as services/outbox_processor.py's outbox events.
MAX_ATTEMPTS = 5
_BASE_BACKOFF_SECONDS = 30
_MAX_BACKOFF_SECONDS = 3600

# Crash-window A (worker claims a job, commits 'processing', then dies
# before calling the handler): process_pending automatically reclaims any
# job that has sat 'processing' longer than this on every run. 15 minutes
# (longer than services/outbox_processor.py's 10 - deliberately
# conservative: unlike an SMS send, a future inbox job's handler may
# involve PDF parsing, AI calls, or attachment processing with less
# predictable latency than Twilio's fixed 15s HTTP timeout). Revisit once
# a real handler's typical/worst-case duration is known - this is a
# documented placeholder, not measured data.
RECLAIM_STALE_AFTER = timedelta(minutes=15)

# job_type -> handler(payload) -> (success, error). error is "" on
# success, never None, so callers can always str() it safely. Registering
# a new job_type here (email, motive, communications-delivery, ...) is
# the whole integration point for a future job type - see workers/
# inbox_handlers.py for the one handler that exists so far, and this
# module's own docstring for why the handler body lives there rather than
# inline here. A claimed job with no registered handler fails cleanly -
# see process_one below - rather than sitting unprocessed with no
# operator visibility.
JOB_HANDLERS: dict[str, Callable[[dict[str, Any]], tuple[bool, str]]] = {
    "inbox.sync": handle_inbox_sync,
    "inbox.process_message": handle_inbox_process_message,
}


def _backoff_for(attempt_count: int) -> timedelta:
    """Exponential backoff, capped - not a full scheduler, just enough to
    avoid hammering a failing dependency (30s, 60s, 120s, 240s, ...
    capped at 1 hour). Same formula as services/outbox_processor.py."""
    seconds = min(_BASE_BACKOFF_SECONDS * (2**attempt_count), _MAX_BACKOFF_SECONDS)
    return timedelta(seconds=seconds)


def _parse_payload(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else json.loads(raw)


def process_one() -> dict[str, Any] | None:
    """Claim and process exactly one pending, due worker job. Returns a
    small result dict for logging/observability, or None if nothing was
    claimable right now (an empty queue is not an error)."""
    with transaction() as conn:
        job = worker_job_repo.claim_next_job(conn)
    if job is None:
        return None

    handler = JOB_HANDLERS.get(job["job_type"])
    payload = _parse_payload(job["payload"])

    if handler is None:
        error = f"No handler registered for job_type={job['job_type']!r}."
        with transaction() as conn:
            worker_job_repo.mark_failed(conn, job["id"], error=error)
        return {"id": job["id"], "job_type": job["job_type"], "outcome": "failed", "error": error}

    try:
        success, error = handler(payload)
        # Sanitized here too, not just on the exception path below: a
        # handler's own returned failure string is not an exception's
        # str() and so never passes through sanitize_exception_message
        # otherwise - every write to worker_jobs.last_error goes through
        # this one choke point regardless of which path produced it.
        error = sanitize_message(error) if error else error
    except Exception as exc:  # noqa: BLE001 - a handler bug must not crash the whole processor loop
        success, error = False, sanitize_exception_message(exc)

    with transaction() as conn:
        if success:
            worker_job_repo.mark_completed(conn, job["id"])
            outcome = "completed"
        elif job["attempt_count"] + 1 >= MAX_ATTEMPTS:
            worker_job_repo.mark_failed(conn, job["id"], error=error)
            outcome = "failed"
        else:
            worker_job_repo.mark_retry(conn, job["id"], error=error, delay=_backoff_for(job["attempt_count"]))
            outcome = "retrying"

    return {"id": job["id"], "job_type": job["job_type"], "outcome": outcome, "error": error}


def process_pending(*, max_jobs: int = 50) -> list[dict[str, Any]]:
    """Process up to max_jobs pending jobs, stopping early once nothing is
    claimable. No daemon, no scheduler loop (same scope decision as Phase
    6's outbox processor) - scripts/process_worker_jobs.py calls this once
    per invocation; an operator/cron/Task Scheduler runs it periodically.

    Reclaims stale 'processing' jobs (see RECLAIM_STALE_AFTER) before
    claiming anything new - crash-window A's fix. An operator can still
    force a different threshold via `scripts/process_worker_jobs.py
    --reclaim-stuck-minutes N` before this runs."""
    with transaction() as conn:
        worker_job_repo.reclaim_stuck_processing(conn, older_than=RECLAIM_STALE_AFTER)

    results = []
    for _ in range(max_jobs):
        result = process_one()
        if result is None:
            break
        results.append(result)
    return results
