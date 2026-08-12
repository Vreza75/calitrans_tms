from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.auth import MUTATE_OPERATIONS, READ_OPERATIONS, AuthenticatedActor, require_auth, require_role
from api.schemas.conversations import ConversationPageOut
from api.schemas.loads import CreateLoadIn, CreateLoadOut, UpdateLoadIn, UpdateLoadOut
from api.schemas.order_drafts import OrderDraftOut, UpdateOrderDraftIn, UpdateOrderDraftOut
from api.schemas.work_items import (
    AttachmentSummaryOut,
    CloseWorkItemIn,
    CommandResultOut,
    LinkLoadIn,
    WorkItemDetailOut,
    WorkItemPageOut,
)
from application.attachments.queries import get_attachments_for_work_item
from application.conversations.queries import get_conversation_history
from application.loads.commands import create_load_from_work_item, update_load_from_work_item
from application.order_drafts.commands import update_order_draft
from application.order_drafts.queries import get_order_draft
from application.work_items import commands as work_item_commands
from application.work_items.queries import get_work_item_detail, get_work_item_queue_page

router = APIRouter(
    prefix="/work-items",
    tags=["work-items"],
    # Router-level floor: every route on this router at least requires
    # READ_OPERATIONS (excludes accounting - see loads.py's separate
    # READ_LOADS group for that role's access). READ_OPERATIONS and
    # MUTATE_OPERATIONS happen to share the same membership today, but
    # mutation routes below also declare require_role(*MUTATE_OPERATIONS)
    # explicitly rather than relying on that coincidence - if the two
    # groups ever diverge, mutations stay correctly gated without anyone
    # having to remember this router's default.
    dependencies=[Depends(require_role(*READ_OPERATIONS))],
)


@router.get("", response_model=WorkItemPageOut)
def list_work_items(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort_by: str = Query("received_at"),
    sort_direction: str = Query("desc"),
    queue: str | None = None,
    service_flow: str | None = None,
    customer: str | None = None,
    search: str | None = None,
    subject: str | None = None,
    status: str | None = None,
    attachment_status: str | None = None,
) -> WorkItemPageOut:
    result = get_work_item_queue_page(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_direction=sort_direction,
        queue=queue,
        service_flow=service_flow,
        customer=customer,
        search=search,
        subject=subject,
        status=status,
        attachment_status=attachment_status,
    )
    return WorkItemPageOut.from_domain(result)


@router.get("/{work_item_id}", response_model=WorkItemDetailOut)
def get_work_item(work_item_id: int) -> WorkItemDetailOut:
    detail = get_work_item_detail(work_item_id)
    return WorkItemDetailOut.from_domain(detail)


@router.get("/{work_item_id}/attachments", response_model=AttachmentSummaryOut)
def get_work_item_attachments(work_item_id: int) -> AttachmentSummaryOut:
    summary = get_attachments_for_work_item(work_item_id)
    return AttachmentSummaryOut.from_domain(summary)


@router.get("/{work_item_id}/conversation", response_model=ConversationPageOut)
def get_work_item_conversation(
    work_item_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> ConversationPageOut:
    detail = get_work_item_detail(work_item_id)
    history = get_conversation_history(detail.conversation_key, page=page, page_size=page_size)
    return ConversationPageOut.from_domain(history)


@router.get("/{work_item_id}/draft", response_model=OrderDraftOut)
def get_work_item_draft(work_item_id: int) -> OrderDraftOut:
    detail = get_work_item_detail(work_item_id)
    draft = get_order_draft(detail.conversation_key)
    return OrderDraftOut.from_domain(draft)


@router.put(
    "/{work_item_id}/draft",
    response_model=UpdateOrderDraftOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def update_work_item_draft(
    work_item_id: int,
    payload: UpdateOrderDraftIn,
    actor: AuthenticatedActor = Depends(require_auth),
) -> UpdateOrderDraftOut:
    detail = get_work_item_detail(work_item_id)
    result = update_order_draft(detail.conversation_key, payload.fields, actor=actor)
    return UpdateOrderDraftOut(**result.__dict__)


@router.post(
    "/{work_item_id}/create-load",
    response_model=CreateLoadOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def create_load(
    work_item_id: int,
    payload: CreateLoadIn,
    actor: AuthenticatedActor = Depends(require_auth),
) -> CreateLoadOut:
    result = create_load_from_work_item(work_item_id, payload.approved_fields, actor=actor)
    return CreateLoadOut(ok=result.ok, load_id=result.load_id, review_status=result.review_status)


@router.post(
    "/{work_item_id}/update-load",
    response_model=UpdateLoadOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def update_load(
    work_item_id: int,
    payload: UpdateLoadIn,
    actor: AuthenticatedActor = Depends(require_auth),
) -> UpdateLoadOut:
    result = update_load_from_work_item(
        work_item_id,
        payload.load_id,
        payload.approved_fields,
        actor=actor,
        fill_blank_only=payload.fill_blank_only,
    )
    return UpdateLoadOut(
        ok=result.ok,
        load_id=result.load_id,
        updated_fields=result.updated_fields,
        skipped_fields=result.skipped_fields,
    )


@router.post(
    "/{work_item_id}/close",
    response_model=CommandResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def close_work_item(
    work_item_id: int,
    payload: CloseWorkItemIn,
    actor: AuthenticatedActor = Depends(require_auth),
) -> CommandResultOut:
    result = work_item_commands.close_work_item(work_item_id, actor=actor, reason=payload.reason)
    return CommandResultOut(**result.__dict__)


@router.post(
    "/{work_item_id}/link-load",
    response_model=CommandResultOut,
    dependencies=[Depends(require_role(*MUTATE_OPERATIONS))],
)
def link_work_item_to_load(
    work_item_id: int,
    payload: LinkLoadIn,
    actor: AuthenticatedActor = Depends(require_auth),
) -> CommandResultOut:
    result = work_item_commands.link_work_item_to_load(work_item_id, payload.load_id, actor=actor)
    return CommandResultOut(**result.__dict__)
