from __future__ import annotations

"""Phase 7: framework-neutral Inbox application models.

Deliberately lean for this batch - only what request_inbox_sync needs.
application/work_items/models.py (WorkItemSummary, WorkItemDetail,
FilterMeta, WorkItemPage, ...) already covers the read side of "an Inbox
work item" - this package does not redefine those. Models for the
message-processing pipeline (InboxProcessingResult, InboxSourceMessage,
etc.) are added in the batch that extracts message processing (Phase 7
STEP 18), not here - adding them now would be speculative, unused code."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class InboxSyncRequestResult:
    """Result of requesting an inbox sync. For the interactive path
    (request_inbox_sync) `job_id` is None only when `ok` is False
    (unauthorized) and `status` is "queued" on success. The scheduled path
    (request_scheduled_inbox_sync) additionally returns status="skipped"
    with job_id None and a `reason` when its overlap guard or idempotency
    key suppressed the enqueue - "skipped" is a normal outcome, not an
    error, so `ok` stays True. Either way this command enqueues (or
    deliberately does not) and returns immediately - it never waits for or
    claims that mailbox processing happened."""

    ok: bool
    job_id: int | None
    status: str = ""
    reason: str = ""


@dataclass(frozen=True)
class InboxSyncHealth:
    """Sanitized unattended-sync health snapshot (application/inbox/
    queries.py::get_inbox_sync_health). Backlog counts, the last successful
    completion and its processing duration, and the most recent failure's
    already-sanitized last_error (never raw SQL/credentials/message bodies -
    workers/processor.py sanitizes before persisting). A read-only operator
    view, not a monitoring platform."""

    pending: int
    processing: int
    completed: int
    failed: int
    last_success_at: datetime | None
    last_duration_seconds: float | None
    last_error: str | None
    checked_at: datetime
