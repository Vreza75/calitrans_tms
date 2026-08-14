from __future__ import annotations

from datetime import datetime, timezone

from application.exceptions import NotFoundError
from application.jobs.models import WorkerJobStatus, WorkerRuntimeStatus


def get_worker_job_status(job_id: int) -> WorkerJobStatus:
    """One consolidated, safe-fields-only read for a single worker job -
    no payload. Read-only: no claim, no mutation."""
    from db_client import transaction
    from repositories import worker_job_repo

    with transaction() as conn:
        job = worker_job_repo.get_job(conn, job_id)

    if job is None:
        raise NotFoundError(f"Job {job_id} not found.")

    return WorkerJobStatus(
        id=int(job["id"]),
        job_type=str(job["job_type"]),
        status=str(job["status"]),
        attempt_count=int(job["attempt_count"]),
        created_at=job.get("created_at"),
        claimed_at=job.get("claimed_at"),
        completed_at=job.get("completed_at"),
        last_error=job.get("last_error"),
    )


def get_worker_runtime_status() -> WorkerRuntimeStatus:
    """Minimal operator/health view (Phase 7 STEP 5) - see
    WorkerRuntimeStatus's docstring for what this does and does not
    prove."""
    from db_client import transaction
    from repositories import outbox_repo, worker_job_repo

    with transaction() as conn:
        worker_counts = worker_job_repo.count_by_status(conn)
        outbox_counts = outbox_repo.count_by_status(conn)

    return WorkerRuntimeStatus(
        worker_jobs_by_status=worker_counts,
        outbox_events_by_status=outbox_counts,
        checked_at=datetime.now(timezone.utc),
    )
