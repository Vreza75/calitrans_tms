from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import MUTATE_OPERATIONS, AuthenticatedActor, require_auth, require_role
from api.schemas.inbox import InboxSyncOut
from application.inbox.commands import request_inbox_sync

router = APIRouter(
    prefix="/inbox",
    tags=["inbox"],
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)


@router.post("/sync", response_model=InboxSyncOut, status_code=202)
def sync_inbox(actor: AuthenticatedActor = Depends(require_auth)) -> InboxSyncOut:
    """Enqueues an inbox.sync job and returns immediately - does not fetch
    or parse email inline, does not wait for the inbox.process_message
    jobs the worker will enqueue once it processes this one. See
    application/inbox/commands.py::request_inbox_sync for the
    authorization boundary (Permission.WORK_ITEM_MANAGE, checked before
    any enqueue) and workers/inbox_handlers.py for what actually consumes
    this job."""
    result = request_inbox_sync(actor=actor)
    return InboxSyncOut.from_domain(result)
