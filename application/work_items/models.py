from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class WorkItemSummary:
    """One row in a paginated Operations Queue page."""

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


@dataclass(frozen=True)
class SortMeta:
    sort_by: str
    sort_direction: str


@dataclass(frozen=True)
class FilterMeta:
    queue: str | None = None
    service_flow: str | None = None
    customer: str | None = None
    search: str | None = None
    subject: str | None = None
    status: str | None = None
    attachment_status: str | None = None


@dataclass(frozen=True)
class WorkItemPage:
    items: list[WorkItemSummary]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    sort: SortMeta
    filters: FilterMeta


@dataclass(frozen=True)
class AttachmentMeta:
    filename: str
    file_path: str
    content_type: str
    is_pdf: bool
    source_work_item_id: int | None = None
    source_message_id: str = ""
    source_subject: str = ""
    source_received_at: str = ""
    # Opaque, stable reference an API client can pass back to
    # GET /api/v1/attachments/{attachment_ref}/content. Never the raw
    # server file_path - API schemas must not serialize file_path.
    attachment_ref: str = ""


@dataclass(frozen=True)
class AttachmentSummary:
    current: list[AttachmentMeta] = field(default_factory=list)
    prior: list[AttachmentMeta] = field(default_factory=list)


@dataclass(frozen=True)
class CommunicationSummary:
    message_count: int
    last_message_at: datetime | None


@dataclass(frozen=True)
class OrderDraftSummary:
    exists: bool
    draft_status: str | None = None
    booking_number: str | None = None
    container_number: str | None = None
    container_qty: int | None = None
    service_flow: str | None = None


@dataclass(frozen=True)
class WorkItemDetail:
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
    attachments: AttachmentSummary
    communication_summary: CommunicationSummary
    order_draft: OrderDraftSummary
    allowed_actions: list[str]


@dataclass(frozen=True)
class ConversationMessage:
    id: int
    source_received_at: datetime | None
    email_direction: str
    source_sender: str
    source_subject: str
    message_preview: str
    conversation_status: str


@dataclass(frozen=True)
class ConversationPage:
    conversation_key: str
    messages: list[ConversationMessage]
    total_messages: int
