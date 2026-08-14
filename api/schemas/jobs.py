from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from application.jobs.models import WorkerJobStatus, WorkerRuntimeStatus


class WorkerJobStatusOut(BaseModel):
    id: int
    job_type: str
    status: str
    attempt_count: int
    created_at: datetime | None
    claimed_at: datetime | None
    completed_at: datetime | None
    last_error: str | None

    @classmethod
    def from_domain(cls, status: WorkerJobStatus) -> "WorkerJobStatusOut":
        return cls(**status.__dict__)


class WorkerRuntimeStatusOut(BaseModel):
    worker_jobs_by_status: dict[str, int]
    outbox_events_by_status: dict[str, int]
    checked_at: datetime | None

    @classmethod
    def from_domain(cls, status: WorkerRuntimeStatus) -> "WorkerRuntimeStatusOut":
        return cls(**status.__dict__)
