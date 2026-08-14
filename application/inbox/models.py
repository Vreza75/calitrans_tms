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


@dataclass(frozen=True)
class InboxSyncRequestResult:
    """Result of requesting an inbox sync. `job_id` is None only when
    `ok` is False (unauthorized - see request_inbox_sync) - a caller
    never gets a job_id for a sync that wasn't actually enqueued.
    `status` is always "queued" on success: this command enqueues
    inbox.sync and returns immediately, same "queued not sent" honesty
    as Phase 6's ReadyToDispatchResult.sms_status - it does not wait for
    or claim mailbox processing happened."""

    ok: bool
    job_id: int | None
    status: str = ""
    reason: str = ""
