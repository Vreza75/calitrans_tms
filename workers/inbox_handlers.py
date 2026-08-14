# workers/inbox_handlers.py

from __future__ import annotations

"""Phase 7: Inbox job handlers, registered in workers/processor.py::
JOB_HANDLERS. Kept out of workers/processor.py itself so the generic
processor stays free of email-specific branching (Phase 7 design
principle) - this module is the "future side effect" integration point
processor.py's JOB_HANDLERS docstring describes.

Wraps services.operations_inbox_service's existing, unchanged functions
(sync fetch/dedup logic, the message-processing pipeline) - Phase 7
reuses this codebase's proven parsing/sync/classification pipeline, it
does not rewrite it (see docs/architecture/WORKER_RUNTIME.md and
CLAUDE.md's "do not rewrite proven parsing algorithms" rule).

services.operations_inbox_service::sync_operations_email_engine itself
is UNCHANGED and still called directly by the Streamlit "Sync Email
Engine" button (pages_app/operations_inbox.py) and the admin debug sync
(pages_app/email_imports.py) - this module's handle_inbox_sync no longer
calls it (see below); the two paths are now independent, and neither's
behavior depends on the other.
"""

import time
from typing import Any

# Recorded as the `actor` on inbox.process_message jobs enqueued by the
# worker itself - Phase 7 STEP 22: background processing is system
# execution of previously-authorized work (request_inbox_sync already
# ran require_permission for the human who triggered the sync this job
# chain descends from), not a fresh user-triggered mutation, so it must
# not be attributed to a hardcoded human role like "dispatcher".
SYSTEM_ACTOR_INBOX_WORKER = "system:inbox-worker"


def handle_inbox_sync(payload: dict[str, Any]) -> tuple[bool, str]:
    """Handler for job_type='inbox.sync' (application/inbox/commands.py::
    request_inbox_sync).

    STEP 16/17: fetches messages and enqueues one inbox.process_message
    job per new message - it does NOT process messages inline anymore
    (that was this handler's batch-3 behavior, safe to change since
    nothing in production depended on it yet - see docs/architecture/
    WORKER_RUNTIME.md). Delegates to _fetch_and_enqueue_inbox_messages
    below rather than services.operations_inbox_service::
    sync_operations_email_engine, which still does full inline processing
    for the Streamlit button's benefit and is otherwise untouched.

    Returns (success, error) per workers/processor.py's handler contract:
    success=False must mean the fetch's own infrastructure failed
    (mailbox unreachable, missing credentials, an uncaught exception) -
    not that some individual messages couldn't be enqueued (counted,
    recorded, fetch continues - see _fetch_and_enqueue_inbox_messages).

    payload may override `limit`/`time_budget_seconds`; defaults match
    sync_operations_email_engine's own interactive defaults (docs/
    OPERATIONS_INBOX_REQUIREMENTS.md's Email Sync Requirements section:
    8-12 messages, 20-30s time budget)."""
    limit = int(payload.get("limit", 12))
    time_budget_seconds = int(payload.get("time_budget_seconds", 25))

    result = _fetch_and_enqueue_inbox_messages(limit=limit, time_budget_seconds=time_budget_seconds)

    error = str(result.get("error") or "")
    return (not error), error


def _fetch_and_enqueue_inbox_messages(*, limit: int = 12, time_budget_seconds: int = 25) -> dict:
    """Fetch-only half of the Phase 7 sync/process split (STEP 16/17).
    Same fetch + dedup logic services.operations_inbox_service::
    sync_operations_email_engine already uses (same functions, called the
    same way, unchanged) - the difference is what happens to each new
    message: enqueue an inbox.process_message job, instead of processing
    it inline.

    Attachments are saved to disk HERE, at enqueue time (reusing
    services.operations_inbox_service::_save_operations_email_attachments,
    the exact function the inline path already calls - not a second
    implementation), not deferred to the inbox.process_message job.
    Raw attachment bytes cannot safely travel through worker_jobs.payload
    (jsonb): Phase 6B's design doc already rejected carrying file bytes
    in a job payload for exactly this "bloats the table, becomes a
    backup/replication burden" reason, and
    repositories.worker_job_repo.enqueue_job's json.dumps(...,
    default=str) would silently mangle raw bytes into an unusable string
    rather than error loudly. The job payload carries only the
    already-saved attachment metadata (filenames/paths/hashes - see
    services.operations_attachment_service::save_operations_attachment's
    return shape, which never includes raw content) plus the rest of the
    message with its `attachments` key removed.

    Each message's enqueue is its own short transaction (matches
    repositories/worker_job_repo.py's "conn is short-lived, one per
    logical write" convention) - a time-budget cutoff mid-loop still
    preserves every enqueue already committed, not just the ones from a
    single all-or-nothing batch."""
    from db_client import transaction
    from repositories.worker_job_repo import enqueue_job
    from services.operations_inbox_service import (
        _email_sync_unique_message_id,
        _save_operations_email_attachments,
        compute_fetch_time_budget,
        compute_insert_loop_deadline,
        ensure_operations_email_sync_schema,
        load_existing_operations_email_lookup,
        operations_email_already_imported,
        safe_str,
    )

    started = time.perf_counter()
    try:
        time_budget_seconds = max(5, int(time_budget_seconds or 25))
    except Exception:
        time_budget_seconds = 25
    deadline = started + time_budget_seconds

    result: dict[str, Any] = {
        "fetched": 0,
        "inbound_fetched": 0,
        "outbound_fetched": 0,
        "enqueued": 0,
        "skipped": 0,
        "errors": 0,
        "elapsed_seconds": None,
        "stopped_early": False,
        "error": "",
        "error_messages": [],
    }

    try:
        ensure_operations_email_sync_schema()
        from services import email_client

        elapsed_before_fetch = time.perf_counter() - started
        fetch_time_budget = compute_fetch_time_budget(time_budget_seconds, elapsed_before_fetch)
        messages = (
            email_client.fetch_operations_email_sync(limit=int(limit or 12), time_budget_seconds=fetch_time_budget)
            or []
        )
        diagnostics = email_client.get_last_operations_email_sync_diagnostics()
        result["diagnostics"] = diagnostics
        if diagnostics.get("errors") and diagnostics.get("accounts_attempted", 0) == 0:
            result["error"] = (
                "No operations email mailbox could be attempted. "
                "Check email addresses and Yahoo app passwords in secrets."
            )
        result["fetched"] = len(messages)
        result["inbound_fetched"] = sum(
            1 for item in messages if safe_str(item.get("direction")).lower() != "outbound"
        )
        result["outbound_fetched"] = sum(
            1 for item in messages if safe_str(item.get("direction")).lower() == "outbound"
        )

        lookup_limit = max(1000, min(3000, int(limit or 12) * 40))
        existing_lookup = load_existing_operations_email_lookup(limit=lookup_limit)
        enqueue_deadline = compute_insert_loop_deadline(deadline, time.perf_counter())

        for message in messages:
            subject = safe_str(message.get("subject")) or "(no subject)"
            sender = safe_str(message.get("from")) or safe_str(message.get("sender")) or "(unknown sender)"
            received_at = safe_str(message.get("received_at")) or None
            message_id = _email_sync_unique_message_id(message)

            if time.perf_counter() >= enqueue_deadline:
                result["stopped_early"] = True
                result["error_messages"].append(
                    "Email fetch-and-enqueue stopped after reaching the time budget."
                )
                break

            if operations_email_already_imported(
                message_id, subject=subject, sender=sender, received_at=received_at, lookup=existing_lookup
            ):
                result["skipped"] += 1
                continue

            try:
                pre_saved_attachments = _save_operations_email_attachments(message, message_id)
            except Exception as exc:
                pre_saved_attachments = []
                result["error_messages"].append(f"{subject[:80]}: attachment save failed: {exc}")

            job_message = {key: value for key, value in message.items() if key != "attachments"}

            try:
                with transaction() as conn:
                    enqueue_job(
                        conn=conn,
                        job_type="inbox.process_message",
                        aggregate_type="email_message",
                        aggregate_id=message_id,
                        payload={"message": job_message, "pre_saved_attachments": pre_saved_attachments},
                        idempotency_key=f"inbox.process_message:{message_id}",
                        actor=SYSTEM_ACTOR_INBOX_WORKER,
                    )
                result["enqueued"] += 1
                existing_lookup.setdefault("by_message_id", {})[message_id.lower()] = {
                    "source_message_id": message_id
                }
            except Exception as exc:
                result["errors"] += 1
                result["error_messages"].append(f"{subject[:80]}: enqueue failed: {exc}")

    except Exception as exc:
        result["error"] = str(exc)

    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    return result


def handle_inbox_process_message(payload: dict[str, Any]) -> tuple[bool, str]:
    """Handler for job_type='inbox.process_message', enqueued by
    _fetch_and_enqueue_inbox_messages above (via handle_inbox_sync).

    payload shape: {"message": <message dict, no "attachments" key>,
    "pre_saved_attachments": <metadata list from
    services.operations_attachment_service::save_operations_attachment,
    no raw bytes>} - not the raw message dict directly (that changed in
    this batch; nothing in production depended on the old shape, since no
    producer existed before this batch either).

    Wraps services.operations_inbox_service::_insert_operations_email_
    message, passing pre_saved_attachments through so it skips
    re-deriving attachments from `message['attachments']` (which was
    deliberately stripped before this payload was ever built - see
    _fetch_and_enqueue_inbox_messages's docstring for why). This is the
    same function sync_operations_email_engine's own inline per-message
    loop calls, covering the full STEP 18 pipeline: latest-body
    extraction + CASE-010 segmentation, parse, attachment field merge,
    classification/triage, load matching (embedded in the triage/
    classification result), persist (INSERT into order_intake).

    After a successful insert, reconciles the touched conversation's
    status (services.operations_inbox_service::sync_conversation_status)
    - sync_operations_email_engine does the same thing, batched once
    across all touched conversations at the end of a whole sync run; here
    it happens once per processed message instead, since jobs process
    independently and a whole-sync-run boundary no longer exists in the
    async model. Best-effort, matching sync_operations_email_engine's own
    swallowed-failure behavior for this step (a reconciliation failure
    must not fail the whole job - the message itself was already
    successfully inserted).

    No try/except around the insert itself: _prepare_operations_email_
    record (called inside) already catches every parse/attachment/
    classification/triage failure internally and returns
    processing_errors instead of raising - proven by tests/
    test_operations_email_insert_resilience.py. Only the persist step's
    DB insert can still raise, and that IS a genuine infrastructure
    failure, correctly left to propagate to workers/processor.py's
    generic exception handling (retry, then terminal-fail) rather than
    caught and misreported as success here."""
    from services.operations_inbox_service import _insert_operations_email_message, sync_conversation_status

    message = payload.get("message") or {}
    pre_saved_attachments = payload.get("pre_saved_attachments") or []

    inserted = _insert_operations_email_message(message, pre_saved_attachments=pre_saved_attachments)

    conversation_key = inserted.get("conversation_key")
    if conversation_key:
        try:
            sync_conversation_status(conversation_key)
        except Exception:
            pass

    return True, ""
