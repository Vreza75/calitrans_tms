from __future__ import annotations

"""Phase 7: Inbox application command boundary. See docs/architecture/
WORKER_RUNTIME.md.

This module owns commands that operate on the Inbox pipeline itself
(requesting a sync, and - in a later batch - processing one source
message). It does not duplicate application/work_items/commands.py,
which already owns commands that operate on an existing work item
(close_work_item, link_work_item_to_load, create/update_load_from_work_item)
- those are unrelated to this module and are not touched here."""

import time

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, require_permission
from application.inbox.models import InboxSyncRequestResult

# request_inbox_sync's idempotency-key time bucket. This system syncs
# exactly one configured mailbox today (config.py's IMAP_* secrets) - if
# that ever becomes multi-mailbox, _SYNC_SOURCE below becomes a parameter
# instead of a constant. Unlike the SMS dedupe key (application/loads/
# commands.py::_driver_dispatch_sms_idempotency_key), there is no
# "permanently suppresses a legitimate future resend" risk here - syncing
# twice is harmless (idempotent per-message dedup happens downstream in
# the sync pipeline itself), so this window only exists to collapse an
# accidental double-click/page-rerun into one job, not to gate legitimate
# resends the way the SMS key must.
_SYNC_DEDUPE_WINDOW_SECONDS = 60
_SYNC_SOURCE = "primary_mailbox"

# Narrowly scoped identity recorded on inbox.sync jobs enqueued by the
# unattended scheduler (scripts/enqueue_inbox_sync.py). Deliberately NOT a
# dispatcher/manager/admin role and NOT an AuthenticatedActor: scheduled
# execution is system-initiated background work, so it must never be
# attributed to - or authorized as - a human. Mirrors workers/
# inbox_handlers.py::SYSTEM_ACTOR_INBOX_WORKER.
_SYNC_SCHEDULER_ACTOR = "system:inbox-scheduler"

# Default polling cadence for the scheduled producer. Overridable via the
# INBOX_SYNC_INTERVAL_MINUTES secret/env without touching the workflow cron,
# so the effective frequency is configurable independently of GitHub
# Actions' fixed */5 schedule (a finer cron simply collapses into one job
# per interval via the bucketed idempotency key below).
_DEFAULT_SYNC_INTERVAL_MINUTES = 5


def _inbox_sync_idempotency_key(*, now: float | None = None) -> str:
    window = int((now if now is not None else time.time()) // _SYNC_DEDUPE_WINDOW_SECONDS)
    return f"inbox.sync:{_SYNC_SOURCE}:{window}"


def _sync_interval_minutes() -> int:
    from config import get_secret

    raw = get_secret("INBOX_SYNC_INTERVAL_MINUTES", str(_DEFAULT_SYNC_INTERVAL_MINUTES))
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_SYNC_INTERVAL_MINUTES
    return value if value >= 1 else _DEFAULT_SYNC_INTERVAL_MINUTES


def _scheduled_inbox_sync_idempotency_key(*, interval_minutes: int, now: float | None = None) -> str:
    bucket = int((now if now is not None else time.time()) // (interval_minutes * 60))
    return f"inbox.sync:scheduled:{_SYNC_SOURCE}:{bucket}"


def request_inbox_sync(*, actor: AuthenticatedActor) -> InboxSyncRequestResult:
    """Enqueue an inbox.sync job and return immediately - does not fetch
    email, parse, classify, match, or process attachments itself. That
    work happens later, in the worker runtime (workers/processor.py, not
    yet built as of this batch - the job sits 'pending' until it is).

    Requires Permission.WORK_ITEM_MANAGE before doing anything else - an
    unauthorized actor triggers zero enqueue, same actor identity used by
    every other Operations Inbox command (application/work_items/
    commands.py). Reusing this permission rather than adding a new one:
    triggering a sync is a facet of operating the Inbox, not a distinct
    capability from managing the work items it produces."""
    require_permission(actor, Permission.WORK_ITEM_MANAGE)

    from db_client import transaction
    from repositories.worker_job_repo import enqueue_job

    idempotency_key = _inbox_sync_idempotency_key()

    with transaction() as conn:
        job_id = enqueue_job(
            conn=conn,
            job_type="inbox.sync",
            aggregate_type="mailbox",
            aggregate_id=_SYNC_SOURCE,
            payload={},
            idempotency_key=idempotency_key,
            actor=actor.actor,
        )

    return InboxSyncRequestResult(ok=True, job_id=job_id, status="queued")


def request_scheduled_inbox_sync(*, now: float | None = None) -> InboxSyncRequestResult:
    """System-scheduled inbox.sync producer - the unattended counterpart of
    request_inbox_sync. Enqueues the SAME inbox.sync job the dispatcher
    "Sync Email Engine" button does, reusing the entire existing
    fetch/dedupe/process/retry pipeline unchanged (handle_inbox_sync ->
    _fetch_and_enqueue_inbox_messages -> inbox.process_message). It does NOT
    fetch, parse, send email, create loads, or change assignments itself.

    Differences from the human path:
    - Runs under a narrowly scoped system identity (_SYNC_SCHEDULER_ACTOR),
      never a dispatcher actor, and never calls require_permission - there
      is no human to authorize. request_inbox_sync's Permission.WORK_ITEM_MANAGE
      boundary is left exactly as-is for the interactive path.
    - Overlap guard: if an inbox.sync is already pending or processing, it
      enqueues nothing (a slow fetch must not accumulate redundant syncs).
      A worker crash that leaves a job stuck 'processing' does NOT block
      syncing forever: workers/processor.py::process_pending reclaims stale
      'processing' jobs (RECLAIM_STALE_AFTER) back to 'pending' and runs
      them, after which the next interval enqueues normally.
    - Same-interval guard: the idempotency key is bucketed by
      INBOX_SYNC_INTERVAL_MINUTES (default 5). If a job for the current
      window already exists in ANY status (including a completed/failed one
      this interval), it is reported "skipped" rather than routed through
      enqueue_job - whose ON CONFLICT (idempotency_key) DO UPDATE is a
      deliberate no-op that returns the existing row's id without reverting a
      terminal job, and so would otherwise be misreported as a fresh
      "queued". The next interval gets a genuinely new job: "no duplicates,
      future runs still allowed".

    Returns status="queued" (with job_id) only when a genuinely new job was
    enqueued, or status="skipped" (job_id None, with a reason) when either
    guard suppressed it. "skipped" is a normal outcome, not an error - ok
    stays True."""
    from db_client import transaction
    from repositories.worker_job_repo import (
        enqueue_job,
        has_active_job,
        job_exists_for_idempotency_key,
    )

    interval_minutes = _sync_interval_minutes()
    idempotency_key = _scheduled_inbox_sync_idempotency_key(interval_minutes=interval_minutes, now=now)

    with transaction() as conn:
        if has_active_job(conn, "inbox.sync"):
            return InboxSyncRequestResult(
                ok=True,
                job_id=None,
                status="skipped",
                reason="An inbox.sync job is already pending or processing.",
            )

        if job_exists_for_idempotency_key(conn, idempotency_key):
            return InboxSyncRequestResult(
                ok=True,
                job_id=None,
                status="skipped",
                reason="An inbox.sync job was already enqueued for this interval window.",
            )

        job_id = enqueue_job(
            conn=conn,
            job_type="inbox.sync",
            aggregate_type="mailbox",
            aggregate_id=_SYNC_SOURCE,
            payload={},
            idempotency_key=idempotency_key,
            actor=_SYNC_SCHEDULER_ACTOR,
        )

    return InboxSyncRequestResult(ok=True, job_id=job_id, status="queued")
