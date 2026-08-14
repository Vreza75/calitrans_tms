from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import READ_OPERATIONS, require_role
from api.schemas.jobs import WorkerJobStatusOut, WorkerRuntimeStatusOut
from application.jobs.queries import get_worker_job_status, get_worker_runtime_status

router = APIRouter(
    tags=["jobs"],
    dependencies=[Depends(require_role(*READ_OPERATIONS))],
)


@router.get("/jobs/{job_id}", response_model=WorkerJobStatusOut)
def get_job_status(job_id: int) -> WorkerJobStatusOut:
    """Safe worker_jobs fields only - never the raw payload (may contain
    message bodies/attachment metadata). See application/jobs/queries.py::
    get_worker_job_status; raises 404 via application.exceptions.
    NotFoundError if the job doesn't exist (handled by api/errors.py)."""
    status = get_worker_job_status(job_id)
    return WorkerJobStatusOut.from_domain(status)


@router.get("/worker/status", response_model=WorkerRuntimeStatusOut)
def get_worker_status() -> WorkerRuntimeStatusOut:
    """Minimal operator/health view (Phase 7 STEP 5) - queue depths per
    status for both worker_jobs and outbox_events. Reflects database
    state only; does not and cannot prove the external scheduled runner
    (.github/workflows/process-jobs.yml) is actually running - see
    application/jobs/models.py::WorkerRuntimeStatus's docstring."""
    status = get_worker_runtime_status()
    return WorkerRuntimeStatusOut.from_domain(status)
