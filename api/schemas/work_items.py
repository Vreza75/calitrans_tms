from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from application.work_items.models import WorkItemDetail, WorkItemPage, WorkItemSummary


class WorkItemSummaryOut(BaseModel):
    id: int
    created_at: datetime | None
    source_received_at: datetime | None
    source_subject: str
    source_sender: str
    email_direction: str
    request_type: str
    work_queue: str
    department_lane: str
    review_status: str
    confidence_score: int
    matched_load_id: int | None
    conversation_key: str
    case_id: int | None
    customer: str
    booking_number: str
    container_number: str
    reference_number: str
    service_flow: str
    attachment_count: int

    @classmethod
    def from_domain(cls, item: WorkItemSummary) -> "WorkItemSummaryOut":
        return cls(**item.__dict__)


class SortMetaOut(BaseModel):
    sort_by: str
    sort_direction: str


class FilterMetaOut(BaseModel):
    queue: str | None = None
    service_flow: str | None = None
    customer: str | None = None
    search: str | None = None
    subject: str | None = None
    status: str | None = None
    attachment_status: str | None = None


class WorkItemPageOut(BaseModel):
    items: list[WorkItemSummaryOut]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    sort: SortMetaOut
    filters: FilterMetaOut

    @classmethod
    def from_domain(cls, page: WorkItemPage) -> "WorkItemPageOut":
        return cls(
            items=[WorkItemSummaryOut.from_domain(item) for item in page.items],
            page=page.page,
            page_size=page.page_size,
            total_items=page.total_items,
            total_pages=page.total_pages,
            sort=SortMetaOut(sort_by=page.sort.sort_by, sort_direction=page.sort.sort_direction),
            filters=FilterMetaOut(**page.filters.__dict__),
        )


class AttachmentMetaOut(BaseModel):
    """Deliberately excludes file_path - see application/work_items/models.py
    AttachmentMeta.attachment_ref for why."""

    filename: str
    content_type: str
    is_pdf: bool
    attachment_ref: str
    source_work_item_id: int | None = None
    source_message_id: str = ""
    source_subject: str = ""
    source_received_at: str = ""


class AttachmentSummaryOut(BaseModel):
    current: list[AttachmentMetaOut]
    prior: list[AttachmentMetaOut]

    @classmethod
    def from_domain(cls, summary) -> "AttachmentSummaryOut":
        def _meta(item):
            data = dict(item.__dict__)
            data.pop("file_path", None)
            return AttachmentMetaOut(**data)

        return cls(
            current=[_meta(item) for item in summary.current],
            prior=[_meta(item) for item in summary.prior],
        )


class CommunicationSummaryOut(BaseModel):
    message_count: int
    last_message_at: datetime | None


class OrderDraftSummaryOut(BaseModel):
    exists: bool
    draft_status: str | None = None
    booking_number: str | None = None
    container_number: str | None = None
    container_qty: int | None = None
    service_flow: str | None = None


class WorkItemDetailOut(BaseModel):
    id: int
    source_sender: str
    source_subject: str
    source_received_at: datetime | None
    original_email_body: str
    parsed_data: dict[str, Any]
    request_type: str
    matched_load_id: int | None
    conversation_key: str
    review_status: str
    confidence_score: int
    attachments: AttachmentSummaryOut
    communication_summary: CommunicationSummaryOut
    order_draft: OrderDraftSummaryOut
    allowed_actions: list[str]

    @classmethod
    def from_domain(cls, detail: WorkItemDetail) -> "WorkItemDetailOut":
        return cls(
            id=detail.id,
            source_sender=detail.source_sender,
            source_subject=detail.source_subject,
            source_received_at=detail.source_received_at,
            original_email_body=detail.original_email_body,
            parsed_data=detail.parsed_data,
            request_type=detail.request_type,
            matched_load_id=detail.matched_load_id,
            conversation_key=detail.conversation_key,
            review_status=detail.review_status,
            confidence_score=detail.confidence_score,
            attachments=AttachmentSummaryOut.from_domain(detail.attachments),
            communication_summary=CommunicationSummaryOut(**detail.communication_summary.__dict__),
            order_draft=OrderDraftSummaryOut(**detail.order_draft.__dict__),
            allowed_actions=detail.allowed_actions,
        )


class CloseWorkItemIn(BaseModel):
    reason: str = ""


class LinkLoadIn(BaseModel):
    load_id: int


class CommandResultOut(BaseModel):
    ok: bool
    work_item_id: int
    detail: str = ""
