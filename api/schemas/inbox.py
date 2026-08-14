from __future__ import annotations

from pydantic import BaseModel

from application.inbox.models import InboxSyncRequestResult


class InboxSyncOut(BaseModel):
    ok: bool
    job_id: int | None
    status: str
    reason: str = ""

    @classmethod
    def from_domain(cls, result: InboxSyncRequestResult) -> "InboxSyncOut":
        return cls(**result.__dict__)
