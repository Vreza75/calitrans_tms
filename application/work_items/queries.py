from __future__ import annotations

import math
from typing import Any

from application.exceptions import NotFoundError
from application.work_items.models import (
    AttachmentMeta,
    AttachmentSummary,
    CommunicationSummary,
    ConversationMessage,
    ConversationPage,
    FilterMeta,
    OrderDraftSummary,
    SortMeta,
    WorkItemDetail,
    WorkItemPage,
    WorkItemSummary,
)
from repositories import inbox_repo, work_item_repo
from services import operations_attachment_core as attachment_core


def _coerce_json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def get_work_item_queue_page(
    *,
    page: int = 1,
    page_size: int = work_item_repo.DEFAULT_PAGE_SIZE,
    sort_by: str = work_item_repo.DEFAULT_SORT_BY,
    sort_direction: str = work_item_repo.DEFAULT_SORT_DIRECTION,
    queue: str | None = None,
    service_flow: str | None = None,
    customer: str | None = None,
    search: str | None = None,
    subject: str | None = None,
    status: str | None = None,
    attachment_status: str | None = None,
    only_open: bool = True,
) -> WorkItemPage:
    """Server-side paginated, sorted, filtered Operations Queue page.

    Sorting/filtering happen in PostgreSQL (repositories.work_item_repo) -
    this function never loads the full open queue into Pandas."""
    resolved_page = max(int(page or 1), 1)
    resolved_page_size = work_item_repo.normalize_page_size(page_size)
    resolved_sort_by, resolved_direction = work_item_repo.normalize_sort(sort_by, sort_direction)

    where_sql, params = work_item_repo.build_work_item_filters(
        queue=queue,
        service_flow=service_flow,
        customer=customer,
        search=search,
        subject=subject,
        status=status,
        attachment_status=attachment_status,
        only_open=only_open,
    )

    total_items = work_item_repo.count_work_items(where_sql, params)
    total_pages = max(math.ceil(total_items / resolved_page_size), 1) if total_items else 1
    resolved_page = min(resolved_page, total_pages)

    df = work_item_repo.list_work_items_page(
        where_sql,
        params,
        sort_by=resolved_sort_by,
        sort_direction=resolved_direction,
        page=resolved_page,
        page_size=resolved_page_size,
    )

    items = [
        WorkItemSummary(
            id=int(row["id"]),
            created_at=row.get("created_at"),
            source_received_at=row.get("source_received_at"),
            source_subject=str(row.get("source_subject") or ""),
            source_sender=str(row.get("source_sender") or ""),
            email_direction=str(row.get("email_direction") or "inbound"),
            request_type=str(row.get("request_type") or ""),
            work_queue=str(row.get("work_queue") or ""),
            department_lane=str(row.get("department_lane") or ""),
            review_status=str(row.get("review_status") or "Open"),
            confidence_score=int(row.get("confidence_score") or 0),
            matched_load_id=int(row["matched_load_id"]) if row.get("matched_load_id") else None,
            conversation_key=str(row.get("conversation_key") or ""),
            case_id=int(row["case_id"]) if row.get("case_id") else None,
            customer=str(row.get("customer") or ""),
            booking_number=str(row.get("booking_number") or ""),
            container_number=str(row.get("container_number") or ""),
            reference_number=str(row.get("reference_number") or ""),
            service_flow=str(row.get("service_flow") or ""),
            attachment_count=int(row.get("attachment_count") or 0),
        )
        for _, row in df.iterrows()
    ]

    return WorkItemPage(
        items=items,
        page=resolved_page,
        page_size=resolved_page_size,
        total_items=total_items,
        total_pages=total_pages,
        sort=SortMeta(sort_by=resolved_sort_by, sort_direction=resolved_direction),
        filters=FilterMeta(
            queue=queue,
            service_flow=service_flow,
            customer=customer,
            search=search,
            subject=subject,
            status=status,
            attachment_status=attachment_status,
        ),
    )


def get_work_item_queue_counts(
    *,
    service_flow: str | None = None,
    customer: str | None = None,
    search: str | None = None,
    subject: str | None = None,
    status: str | None = None,
    attachment_status: str | None = None,
    only_open: bool = True,
) -> dict[str, int]:
    """One bounded aggregation query for queue-nav counts (Phase 10A) -
    counts reflect the same active filters as get_work_item_queue_page,
    minus queue itself (a queue's own count must not be pre-filtered to
    that queue)."""
    where_sql, params = work_item_repo.build_work_item_filters(
        queue=None,
        service_flow=service_flow,
        customer=customer,
        search=search,
        subject=subject,
        status=status,
        attachment_status=attachment_status,
        only_open=only_open,
    )
    return work_item_repo.count_work_items_by_queue(where_sql, params)


def _allowed_actions(request_type: str, matched_load_id: int | None) -> list[str]:
    """Lightweight capability hint for API consumers (e.g. a future
    Next.js UI) - not a redefinition of the dispatcher classification/
    decision rules, which remain canonical in
    services.operations_inbox_service."""
    if matched_load_id:
        return ["update_existing_load", "close_work_item"]
    if request_type in {"New Booking", "New Order"}:
        return ["create_load", "close_work_item", "link_to_load"]
    return ["close_work_item", "link_to_load"]


def get_work_item_detail(work_item_id: int) -> WorkItemDetail:
    """One consolidated read for a single work item. Reads only persisted
    data: no IMAP fetch, no AI call, no PDF parsing, no database write."""
    record_df = inbox_repo.load_operations_inbox_record(int(work_item_id))
    if record_df.empty:
        raise NotFoundError(f"Work item {work_item_id} not found.")

    record = record_df.iloc[0].to_dict()
    parsed = _coerce_json_dict(record.get("parsed_data"))
    conversation_key = str(record.get("conversation_key") or "")

    attachments = get_attachment_summary(work_item_id, record=record, parsed=parsed)

    summary = (
        work_item_repo.get_conversation_summary(conversation_key)
        if conversation_key
        else {"message_count": 0, "last_message_at": None}
    )

    draft_row = work_item_repo.get_order_draft(conversation_key) if conversation_key else None
    order_draft = (
        OrderDraftSummary(
            exists=True,
            draft_status=draft_row.get("draft_status"),
            booking_number=draft_row.get("booking_number"),
            container_number=draft_row.get("container_number"),
            container_qty=draft_row.get("container_qty"),
            service_flow=draft_row.get("service_flow"),
        )
        if draft_row
        else OrderDraftSummary(exists=False)
    )

    request_type = str(record.get("request_type") or "Needs Classification")
    matched_load_id = int(record["matched_load_id"]) if record.get("matched_load_id") else None

    return WorkItemDetail(
        id=int(record["id"]),
        source_sender=str(record.get("source_sender") or ""),
        source_subject=str(record.get("source_subject") or ""),
        source_received_at=record.get("source_received_at"),
        original_email_body=str(record.get("raw_text") or ""),
        parsed_data=parsed,
        request_type=request_type,
        matched_load_id=matched_load_id,
        conversation_key=conversation_key,
        review_status=str(record.get("review_status") or "Open"),
        confidence_score=int(record.get("confidence_score") or 0),
        attachments=attachments,
        communication_summary=CommunicationSummary(
            message_count=int(summary["message_count"]),
            last_message_at=summary["last_message_at"],
        ),
        order_draft=order_draft,
        allowed_actions=_allowed_actions(request_type, matched_load_id),
    )


def get_attachment_summary(
    work_item_id: int,
    *,
    record: dict | None = None,
    parsed: dict | None = None,
) -> AttachmentSummary:
    """Persisted attachment metadata only - never reads attachment bytes.
    Bytes are read only by the content/preview path (Streamlit's lazy PDF
    panel, or a future GET /api/v1/attachments/{id}/content call)."""
    if record is None:
        record_df = inbox_repo.load_operations_inbox_record(int(work_item_id))
        if record_df.empty:
            raise NotFoundError(f"Work item {work_item_id} not found.")
        record = record_df.iloc[0].to_dict()
    if parsed is None:
        parsed = _coerce_json_dict(record.get("parsed_data"))

    # operations_attachment_core has no streamlit dependency (directly or
    # transitively) - imported at module top, no lazy-import trick needed.
    #
    # Prior-conversation attachment grouping needs each earlier message's
    # own parsed_data, which the lightweight conversation-summary query
    # does not select (by design - Step 3C: don't load full history for a
    # metadata-only call). This summary reports only the current
    # message's attachments; a caller that needs prior-conversation
    # attachments too fetches the full timeline via get_conversation_page()
    # first and re-groups from that.
    grouped = attachment_core.group_operations_source_documents(record, parsed, [])

    def _to_meta(item: dict) -> AttachmentMeta:
        file_path = str(item.get("file_path") or "")
        return AttachmentMeta(
            filename=str(item.get("filename") or ""),
            file_path=file_path,
            content_type=str(item.get("content_type") or ""),
            is_pdf=bool(item.get("is_pdf")),
            source_work_item_id=item.get("source_work_item_id"),
            source_message_id=str(item.get("source_message_id") or ""),
            source_subject=str(item.get("source_subject") or ""),
            source_received_at=str(item.get("source_received_at") or ""),
            attachment_ref=attachment_ref(file_path),
        )

    return AttachmentSummary(
        current=[_to_meta(item) for item in grouped.get("current", [])],
        prior=[_to_meta(item) for item in grouped.get("prior", [])],
    )


def attachment_ref(file_path: str) -> str:
    """Opaque, stable reference derived from the server file_path. Exposed
    to API clients instead of the path itself (Step 12: don't return raw
    file-system paths); get_attachment_content() resolves it back to a
    path by re-deriving refs for that work item's own attachments and
    matching, never by accepting a path from the caller."""
    return attachment_core.attachment_ref(file_path)


def get_attachment_content(work_item_id: int, attachment_ref_value: str) -> tuple[bytes, AttachmentMeta]:
    """Read attachment bytes for one work item's attachment, identified by
    its opaque ref (see attachment_ref()) rather than a client-supplied
    path. Only called from the explicit content/preview path - never as
    part of a work-item read."""
    summary = get_attachment_summary(work_item_id)
    all_items = list(summary.current) + list(summary.prior)
    match = next((item for item in all_items if item.attachment_ref == attachment_ref_value), None)
    if match is None or not match.file_path:
        raise NotFoundError(f"Attachment {attachment_ref_value} not found for work item {work_item_id}.")

    return attachment_core.read_attachment_bytes(match.file_path), match


def get_conversation_page(
    conversation_key: str,
    *,
    page: int = 1,
    page_size: int = 25,
) -> ConversationPage:
    """Full conversation history - only called when the dispatcher (or an
    API client) explicitly requests it, never as part of the queue or
    detail read."""
    resolved_page = max(int(page or 1), 1)
    offset = (resolved_page - 1) * page_size

    total = work_item_repo.count_conversation_messages(conversation_key)
    df = work_item_repo.list_conversation_messages(conversation_key, limit=page_size, offset=offset)

    messages = [
        ConversationMessage(
            id=int(row["id"]),
            source_received_at=row.get("source_received_at"),
            email_direction=str(row.get("email_direction") or "inbound"),
            source_sender=str(row.get("source_sender") or ""),
            source_subject=str(row.get("source_subject") or ""),
            message_preview=str(row.get("message_preview") or ""),
            conversation_status=str(row.get("conversation_status") or "New Conversation"),
        )
        for _, row in df.iterrows()
    ]

    return ConversationPage(conversation_key=conversation_key, messages=messages, total_messages=total)
