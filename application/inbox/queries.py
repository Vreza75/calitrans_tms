from __future__ import annotations

"""Framework-neutral read side of the Inbox worker pipeline. No Streamlit,
no mutation - safe to call from the API, the scheduler script, or a future
operator dashboard. Mirrors application/jobs/queries.py's read-only,
safe-fields-only shape; scoped specifically to the unattended inbox.sync
producer's observability (requirement: last successful sync, errors, queue
backlog, processing duration)."""

from datetime import datetime, timezone

from application.inbox.models import InboxSyncHealth


def get_inbox_sync_health() -> InboxSyncHealth:
    """One consolidated, sanitized health read for the inbox.sync job type.
    Read-only: no claim, no mutation. last_error is already sanitized at
    write time (workers/processor.py), so nothing raw is surfaced here."""
    from db_client import transaction
    from repositories import worker_job_repo

    with transaction() as conn:
        health = worker_job_repo.get_job_type_health(conn, "inbox.sync")

    return InboxSyncHealth(
        pending=health["pending"],
        processing=health["processing"],
        completed=health["completed"],
        failed=health["failed"],
        last_success_at=health["last_success_at"],
        last_duration_seconds=health["last_duration_seconds"],
        last_error=health["last_error"],
        checked_at=datetime.now(timezone.utc),
    )
