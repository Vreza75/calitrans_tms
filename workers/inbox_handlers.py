# workers/inbox_handlers.py

from __future__ import annotations

"""Phase 7: Inbox job handlers, registered in workers/processor.py::
JOB_HANDLERS. Kept out of workers/processor.py itself so the generic
processor stays free of email-specific branching (Phase 7 design
principle) - this module is the "future side effect" integration point
processor.py's JOB_HANDLERS docstring describes.

Wraps services.operations_inbox_service::sync_operations_email_engine
unchanged - Phase 7 reuses this codebase's proven parsing/sync/
classification pipeline, it does not rewrite it (see docs/architecture/
WORKER_RUNTIME.md and CLAUDE.md's "do not rewrite proven parsing
algorithms" rule)."""

from typing import Any


def handle_inbox_sync(payload: dict[str, Any]) -> tuple[bool, str]:
    """Handler for job_type='inbox.sync' (application/inbox/commands.py::
    request_inbox_sync).

    Returns (success, error) per workers/processor.py's handler contract:
    success=False must mean the sync's own infrastructure failed (mailbox
    unreachable, missing credentials, an uncaught exception) - not that
    some individual messages within a mostly-successful sync couldn't be
    fully parsed. sync_operations_email_engine already handles that
    distinction itself: a per-message parse/insert failure is caught,
    counted (result['errors']), and recorded (result['error_messages']),
    then the sync continues - it does not set result['error'] or raise.
    Only a total failure (e.g. "No operations email mailbox could be
    attempted") sets result['error']. This handler mirrors that
    distinction rather than treating every non-zero error count as a
    handler failure, which is exactly the "processing failure vs. review
    outcome" principle this worker runtime is built around.

    payload may override `limit`/`time_budget_seconds`; defaults match
    the function's own interactive defaults (docs/
    OPERATIONS_INBOX_REQUIREMENTS.md's Email Sync Requirements section:
    8-12 messages, 20-30s time budget) - a worker-triggered sync behaves
    identically to today's interactive sync unless a caller deliberately
    asks for something wider."""
    from services.operations_inbox_service import sync_operations_email_engine

    limit = int(payload.get("limit", 12))
    time_budget_seconds = int(payload.get("time_budget_seconds", 25))

    result = sync_operations_email_engine(limit=limit, time_budget_seconds=time_budget_seconds)

    error = str(result.get("error") or "")
    return (not error), error


def handle_inbox_process_message(payload: dict[str, Any]) -> tuple[bool, str]:
    """Handler for job_type='inbox.process_message' - not yet enqueued by
    anything (no producer exists as of this batch; sync_operations_email_
    engine still processes each fetched message inline, unchanged - see
    docs/architecture/WORKER_RUNTIME.md's "Next batches"). Registered now
    so the pipeline this handler wraps is independently addressable and
    tested ahead of the sync/process split that will eventually enqueue
    it, same incremental order batches 1-3 followed for inbox.sync.

    payload is one message dict, same shape services.email_client.
    fetch_operations_email_sync() already produces (subject/body/from/
    received_at/direction/attachments/...) - not a new schema.

    Wraps services.operations_inbox_service::_insert_operations_email_
    message unchanged - this is the exact function sync_operations_email_
    engine's own per-message loop already calls today
    (services/operations_inbox_service.py:4952), covering the full STEP
    18 pipeline in one already-framework-neutral, already-pure/impure-
    split call: latest-body extraction + CASE-010 segmentation
    (_prepare_operations_email_record -> build_message_scope,
    extract_latest_email_body) -> parse (parse_email_text) -> attachment
    processing (_save_operations_email_attachments,
    merge_saved_attachment_fields) -> classification/triage
    (build_operations_email_classification, triage_operations_email and
    its enforcement passes) -> load matching (embedded in
    classification/triage's matched_load_id) -> persist
    (_insert_operations_email_record_row's INSERT into order_intake).
    Referenced here by its existing (underscore-prefixed) name rather
    than renamed/promoted to public - tests/test_operations_email_insert_
    resilience.py and tests/test_operations_attachment_dispatcher_admin.py
    already pin that name via direct reference/mock.patch, and renaming
    it is out of scope for this batch.

    No try/except here: _prepare_operations_email_record (called inside)
    already catches every parse/attachment/classification/triage failure
    internally and returns processing_errors instead of raising - proven
    by tests/test_operations_email_insert_resilience.py. Only the
    persist step's DB insert can still raise, and that IS a genuine
    infrastructure failure, correctly left to propagate to workers/
    processor.py's generic exception handling (retry, then terminal-fail)
    rather than caught and misreported as success here."""
    from services.operations_inbox_service import _insert_operations_email_message

    _insert_operations_email_message(payload)
    return True, ""
