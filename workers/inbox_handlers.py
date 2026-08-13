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
