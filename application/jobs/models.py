from __future__ import annotations

"""Phase 7 closure: framework-neutral models for worker job / outbox
observability. Deliberately separate from application/inbox/ - jobs and
outbox events are not Inbox-specific concepts (per workers/processor.py's
own design principle: "future handlers should eventually be possible" for
non-inbox job types), so their read models don't belong in an Inbox
package."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class WorkerJobStatus:
    """Single worker_jobs row, safe fields only - never `payload` (may
    contain message bodies/attachment metadata not meant for a generic
    job-status API consumer). `last_error` is already sanitized before
    it's ever written (workers/processor.py routes every error through
    utils.error_sanitizer before persisting) - no further sanitization
    needed at read time."""

    id: int
    job_type: str
    status: str
    attempt_count: int
    created_at: datetime | None
    claimed_at: datetime | None
    completed_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class WorkerRuntimeStatus:
    """Minimal operator/health view (Phase 7 STEP 5) - queue depths and
    failed counts for both queue tables, not a monitoring platform. This
    reflects DATABASE STATE ONLY: it does not and cannot prove the
    external scheduled runner (.github/workflows/process-jobs.yml, or
    whatever a deployment ultimately uses) is actually running - a queue
    with a growing 'pending' count and no recent completions is the
    actual signal an operator should watch for that, not a boolean
    "healthy" flag this dataclass deliberately does not provide (see
    docs/architecture/WORKER_RUNTIME.md's health section for why)."""

    worker_jobs_by_status: dict[str, int] = field(default_factory=dict)
    outbox_events_by_status: dict[str, int] = field(default_factory=dict)
    checked_at: datetime | None = None
