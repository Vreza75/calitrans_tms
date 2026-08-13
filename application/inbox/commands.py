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


def _inbox_sync_idempotency_key(*, now: float | None = None) -> str:
    window = int((now if now is not None else time.time()) // _SYNC_DEDUPE_WINDOW_SECONDS)
    return f"inbox.sync:{_SYNC_SOURCE}:{window}"


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
