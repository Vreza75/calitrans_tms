# repositories/work_item_repo.py

"""
Operations Inbox work-item repository for the Phase 1 backend boundary.

Framework-neutral: no Streamlit import, no rendering, no session state.
Adds server-side pagination/sorting/filtering on top of the same
order_intake/operations_cases tables repositories/inbox_repo.py already
reads, so the Operations Queue (and, later, FastAPI/Next.js) can request
one page of work items instead of the full open queue.

Reuses repositories.inbox_repo's WHERE-fragment helpers (also
framework-neutral) instead of redefining the terminal-status/email-source
lists a second time.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from db_client import execute, read_df, transaction
from repositories.inbox_repo import inbox_review_where_clause

# Public sort key -> real SQL expression. Whitelisted so sort_by can never
# be interpolated into SQL directly (no injection surface).
SORTABLE_COLUMNS: dict[str, str] = {
    "received_at": "oi.source_received_at",
    "created_at": "oi.created_at",
    "subject": "oi.source_subject",
    "sender": "oi.source_sender",
    "customer": "(oi.parsed_data ->> 'Customer')",
    "confidence": "oi.confidence_score",
    "status": "oi.review_status",
    "request_type": "oi.request_type",
    "id": "oi.id",
}

DEFAULT_SORT_BY = "received_at"
DEFAULT_SORT_DIRECTION = "desc"
DEFAULT_PAGE_SIZE = 25
ALLOWED_PAGE_SIZES = (10, 25, 50)

_PARSED_FIELD_COLUMNS = {
    "customer": "Customer",
    "booking_number": "Booking Number",
    "container_number": "Container Number",
    "reference_number": "Reference Number",
    "service_flow": "TYPE",
}


def normalize_sort(sort_by: str | None, sort_direction: str | None) -> tuple[str, str]:
    resolved_sort_by = sort_by if sort_by in SORTABLE_COLUMNS else DEFAULT_SORT_BY
    resolved_direction = str(sort_direction or DEFAULT_SORT_DIRECTION).strip().lower()
    if resolved_direction not in {"asc", "desc"}:
        resolved_direction = DEFAULT_SORT_DIRECTION
    return resolved_sort_by, resolved_direction


def normalize_page_size(page_size: int | None) -> int:
    if page_size in ALLOWED_PAGE_SIZES:
        return int(page_size)
    return DEFAULT_PAGE_SIZE


def build_work_item_filters(
    *,
    queue: str | None = None,
    service_flow: str | None = None,
    customer: str | None = None,
    search: str | None = None,
    subject: str | None = None,
    status: str | None = None,
    attachment_status: str | None = None,
    only_open: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Build a WHERE clause (no leading "where") and bound params for the
    work-item queue query. Every value is bound - none are string-formatted
    into the SQL - so this is safe against injection regardless of what a
    caller (Streamlit form, FastAPI query param) passes in."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if only_open:
        # inbox_review_where_clause() returns "where coalesce(...)..." -
        # strip the leading "where" so it composes with the other clauses.
        review_clause = inbox_review_where_clause()
        clauses.append(review_clause[len("where "):] if review_clause.lower().startswith("where ") else review_clause)

    if queue:
        clauses.append("oi.work_queue = :queue")
        params["queue"] = queue

    if status:
        clauses.append("oi.review_status = :status")
        params["status"] = status

    if service_flow:
        clauses.append("(oi.parsed_data ->> 'TYPE') ilike :service_flow")
        params["service_flow"] = f"%{service_flow}%"

    if customer:
        clauses.append("(oi.parsed_data ->> 'Customer') ilike :customer")
        params["customer"] = f"%{customer}%"

    if subject:
        clauses.append("oi.source_subject ilike :subject")
        params["subject"] = f"%{subject}%"

    if search:
        clauses.append(
            """(
                oi.source_subject ilike :search
                or oi.source_sender ilike :search
                or (oi.parsed_data ->> 'Booking Number') ilike :search
                or (oi.parsed_data ->> 'Container Number') ilike :search
                or (oi.parsed_data ->> 'Reference Number') ilike :search
            )"""
        )
        params["search"] = f"%{search}%"

    if attachment_status == "has_attachments":
        clauses.append(
            "(jsonb_typeof(oi.parsed_data -> '_operations_attachments') = 'array'"
            " and jsonb_array_length(oi.parsed_data -> '_operations_attachments') > 0)"
        )
    elif attachment_status == "no_attachments":
        clauses.append(
            "coalesce(jsonb_array_length(oi.parsed_data -> '_operations_attachments'), 0) = 0"
        )

    where_sql = " and ".join(clauses) if clauses else "true"
    return where_sql, params


def count_work_items(where_sql: str, params: dict[str, Any]) -> int:
    df = read_df(
        f"""
        select count(*) as total
        from order_intake oi
        where {where_sql}
        """,
        params,
    )
    if df.empty:
        return 0
    return int(df.iloc[0]["total"])


def count_work_items_by_queue(where_sql: str, params: dict[str, Any]) -> dict[str, int]:
    """One bounded aggregation query for queue-nav counts (Phase 10A) -
    never N+1 (one count-per-queue call). `where_sql`/`params` normally
    come from build_work_item_filters(queue=None, ...) so counts reflect
    the currently active search/service_flow/etc filters, same as the
    per-queue counts Streamlit's Operations Queue radio already shows."""
    df = read_df(
        f"""
        select coalesce(oi.work_queue, '') as queue, count(*) as total
        from order_intake oi
        where {where_sql}
        group by oi.work_queue
        """,
        params,
    )
    if df.empty:
        return {}
    return {str(row["queue"]): int(row["total"]) for _, row in df.iterrows()}


def list_work_items_page(
    where_sql: str,
    params: dict[str, Any],
    *,
    sort_by: str,
    sort_direction: str,
    page: int,
    page_size: int,
) -> pd.DataFrame:
    """One page of the queue, sorted and filtered in PostgreSQL - never the
    full table pulled into Pandas and filtered client-side."""
    sort_column = SORTABLE_COLUMNS.get(sort_by, SORTABLE_COLUMNS[DEFAULT_SORT_BY])
    direction = "asc" if sort_direction == "asc" else "desc"
    offset = max(page - 1, 0) * page_size

    query_params = dict(params)
    query_params["limit"] = page_size
    query_params["offset"] = offset

    return read_df(
        f"""
        select
            oi.id,
            oi.created_at,
            oi.source_received_at,
            oi.source_subject,
            oi.source_sender,
            oi.email_direction,
            oi.request_type,
            oi.work_queue,
            oi.department_lane,
            oi.review_status,
            oi.confidence_score,
            oi.matched_load_id,
            oi.conversation_key,
            oi.case_id,
            oi.parsed_data ->> 'Customer' as customer,
            oi.parsed_data ->> 'Booking Number' as booking_number,
            oi.parsed_data ->> 'Container Number' as container_number,
            oi.parsed_data ->> 'Reference Number' as reference_number,
            oi.parsed_data ->> 'TYPE' as service_flow,
            coalesce(jsonb_array_length(oi.parsed_data -> '_operations_attachments'), 0) as attachment_count
        from order_intake oi
        where {where_sql}
        order by {sort_column} {direction} nulls last, oi.id desc
        limit :limit offset :offset
        """,
        query_params,
    )


def list_conversation_messages(
    conversation_key: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> pd.DataFrame:
    """Paginated conversation history for one business conversation. Not
    called by the work-item detail query - only when history is explicitly
    requested, so opening a work item never pays for the full thread."""
    if not str(conversation_key or "").strip():
        return pd.DataFrame()

    return read_df(
        """
        select
            id,
            source_received_at,
            created_at,
            coalesce(email_direction, 'inbound') as email_direction,
            coalesce(source_sender, '') as source_sender,
            coalesce(source_subject, '') as source_subject,
            left(coalesce(raw_text, ''), 1200) as message_preview,
            coalesce(conversation_status, 'New Conversation') as conversation_status
        from order_intake
        where conversation_key = :conversation_key
        order by coalesce(source_received_at, created_at) desc, id desc
        limit :limit offset :offset
        """,
        {"conversation_key": conversation_key, "limit": limit, "offset": offset},
    )


def count_conversation_messages(conversation_key: str) -> int:
    if not str(conversation_key or "").strip():
        return 0
    df = read_df(
        "select count(*) as total from order_intake where conversation_key = :conversation_key",
        {"conversation_key": conversation_key},
    )
    return int(df.iloc[0]["total"]) if not df.empty else 0


def get_conversation_summary(conversation_key: str) -> dict:
    """One cheap round trip (count + latest timestamp) for the work-item
    detail view - callers that need the full thread call
    list_conversation_messages() separately, only when requested."""
    if not str(conversation_key or "").strip():
        return {"message_count": 0, "last_message_at": None}

    df = read_df(
        """
        select count(*) as message_count, max(coalesce(source_received_at, created_at)) as last_message_at
        from order_intake
        where conversation_key = :conversation_key
        """,
        {"conversation_key": conversation_key},
    )
    if df.empty:
        return {"message_count": 0, "last_message_at": None}
    row = df.iloc[0]
    return {
        "message_count": int(row["message_count"]),
        "last_message_at": row["last_message_at"],
    }


# Columns a dispatcher-submitted draft edit may set directly. Dispatcher
# input is the strongest source in the field-precedence order (see
# .claude/rules/operations-inbox.md, "Pending Order Draft Rules": a weaker
# parser must never overwrite dispatcher-confirmed data) - so a direct,
# whitelisted column UPDATE here does not need the automated-parser merge
# logic that reply/email ingestion goes through.
DRAFT_EDITABLE_COLUMNS = {
    "booking_number",
    "container_number",
    "reference_number",
    "container_qty",
    "service_flow",
    "origin_port",
    "destination_warehouse",
    "delivery_address",
    "delivery_need_date",
    "lfd",
    "container_size",
    "dispatcher_notes",
}


def update_order_draft_fields(conversation_key: str, fields: dict[str, Any]) -> bool:
    """Whitelisted, dispatcher-confirmed draft field update. Returns False
    if no matching draft row exists (caller decides whether that's a 404)."""
    updates = {k: v for k, v in fields.items() if k in DRAFT_EDITABLE_COLUMNS}
    if not updates:
        return False

    set_clause = ", ".join(f"{column} = :{column}" for column in updates)
    params = dict(updates)
    params["conversation_key"] = conversation_key

    with transaction() as conn:
        from sqlalchemy import text

        result = conn.execute(
            text(
                f"""
                update order_intake_drafts
                set {set_clause},
                    dispatcher_confirmed_at = now(),
                    updated_at = now()
                where conversation_key = :conversation_key
                """
            ),
            params,
        )
        return result.rowcount > 0


def get_order_draft(conversation_key: str) -> dict | None:
    """Persisted pending-order-draft row for one business conversation, if
    one exists. Read-only projection - does not merge/validate fields
    (that authority stays in the existing pending-draft workflow)."""
    if not str(conversation_key or "").strip():
        return None
    df = read_df(
        "select * from order_intake_drafts where conversation_key = :conversation_key limit 1",
        {"conversation_key": conversation_key},
    )
    return df.iloc[0].to_dict() if not df.empty else None


def update_review_status(work_item_id: int, review_status: str, *, conn=None) -> None:
    sql = "update order_intake set review_status = :review_status where id = :id"
    params = {"id": int(work_item_id), "review_status": review_status}
    if conn is not None:
        from sqlalchemy import text

        conn.execute(text(sql), params)
    else:
        execute(sql, params)


def link_to_load(work_item_id: int, load_id: int, *, conn=None) -> None:
    sql = (
        "update order_intake set matched_load_id = :load_id, review_status = 'Attached' "
        "where id = :id"
    )
    params = {"id": int(work_item_id), "load_id": int(load_id)}
    if conn is not None:
        from sqlalchemy import text

        conn.execute(text(sql), params)
    else:
        execute(sql, params)


def insert_case_event(
    *,
    case_id: int,
    event_type: str,
    title: str,
    details: str = "",
    actor: str = "dispatcher",
    conn,
) -> None:
    """Must be called with the same `conn` used for the rest of the command
    (see db_client.transaction()) so the audit row commits or rolls back
    together with the change it documents."""
    from sqlalchemy import text

    conn.execute(
        text(
            """
            insert into operations_case_events (case_id, event_type, title, details, actor)
            values (:case_id, :event_type, :title, :details, :actor)
            """
        ),
        {
            "case_id": int(case_id),
            "event_type": event_type,
            "title": title,
            "details": details,
            "actor": actor,
        },
    )
