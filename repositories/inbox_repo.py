# repositories/inbox_repo.py

"""
Operations Inbox repository.

This module is the database access layer for Operations Inbox records.
For the first refactor phase, these functions can either be copied from app.py
or delegated through services/operations_inbox_service.py while the project is
being cleaned up.
"""

from __future__ import annotations

import pandas as pd

from db_client import execute, read_df


# These constants should eventually move into a shared config/constants module.
OPERATIONS_ATTACHMENTS_KEY = "_operations_attachments"
OPERATIONS_PDF_ATTACHMENTS_KEY = "_operations_pdf_attachments"

INBOX_TERMINAL_REVIEW_STATUSES = [
    "Order Created",
    "Attached",
    "Quote Created",
    "Order Cancelled",
    "Closed",
]

OPERATIONS_EMAIL_SYNC_SOURCES = [
    "operations_email",
    "operations_email_sent",
    "email_body",
    "email_combined",
]


def _sql_literal_list(values: list[str]) -> str:
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)


def operations_email_source_filter(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"{prefix}source in ({_sql_literal_list(OPERATIONS_EMAIL_SYNC_SOURCES)})"


def conversation_join_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"coalesce("
        f"nullif({prefix}conversation_key, ''), "
        f"nullif({prefix}email_thread_id, ''), "
        f"nullif({prefix}source_message_id, ''), "
        f"nullif({prefix}email_normalized_subject, ''), "
        f"lower(coalesce({prefix}source_subject, ''))"
        f")"
    )


def inbox_review_where_clause() -> str:
    terminal = ", ".join([f"'{status}'" for status in INBOX_TERMINAL_REVIEW_STATUSES])
    return f"where coalesce(review_status, 'Open') not in ({terminal})"


def load_operations_inbox_df(where_clause: str) -> pd.DataFrame:
    return read_df(
        f"""
        select
            oi.id,
            oi.created_at,
            oi.source_received_at,
            oi.source,
            oi.source_subject,
            oi.source_sender,
            oi.source_message_id,
            oi.email_direction,
            oi.email_mailbox,
            oi.email_thread_id,
            oi.email_normalized_subject,
            oi.conversation_status,
            oi.email_in_reply_to,
            oi.email_references,
            oi.filename,
            oi.file_path,
            oi.parsed_data,
            left(coalesce(oi.raw_text, ''), 1200) as raw_text_preview,
            case
                when jsonb_typeof(oi.parsed_data -> :pdf_attachments_key) = 'array'
                    then jsonb_array_length(oi.parsed_data -> :pdf_attachments_key)
                when oi.filename is not null and oi.filename <> '' then 1
                else 0
            end as pdf_count,
            case
                when jsonb_typeof(oi.parsed_data -> :attachments_key) = 'array'
                    then jsonb_array_length(oi.parsed_data -> :attachments_key)
                when jsonb_typeof(oi.parsed_data -> :pdf_attachments_key) = 'array'
                    then jsonb_array_length(oi.parsed_data -> :pdf_attachments_key)
                when oi.filename is not null and oi.filename <> '' then 1
                else 0
            end as attachment_count,
            case
                when coalesce(oi.parsed_data #>> '{{_email_sync,source_attachment_count}}', '') ~ '^[0-9]+$'
                    then (oi.parsed_data #>> '{{_email_sync,source_attachment_count}}')::int
                else 0
            end as source_attachment_count,
            oi.intake_status,
            oi.request_type,
            oi.conversation_key,
            oi.matched_load_id,
            oi.case_id,
            oc.case_number,
            oc.status as case_status,
            oc.owner as case_owner,
            oc.priority as case_priority,
            oc.customer as case_customer,
            oc.linked_load_id as case_linked_load_id,
            oc.next_action as case_next_action,
            oc.sla_status as case_sla_status,
            oc.message_count as case_message_count,
            oc.last_message_at as case_last_message_at,
            oc.last_message_direction as case_last_message_direction,
            oc.first_response_due_at as case_first_response_due_at,
            oc.resolution_due_at as case_resolution_due_at,
            oc.customer_wait_started_at as case_customer_wait_started_at,
            oc.department_wait_started_at as case_department_wait_started_at,
            oi.confidence_score,
            oi.action_required,
            oi.review_status
        from (
            select *
            from order_intake
            {where_clause}
        ) oi
        left join operations_cases oc on oc.id = oi.case_id
        order by oi.created_at desc
        """,
        {
            "pdf_attachments_key": OPERATIONS_PDF_ATTACHMENTS_KEY,
            "attachments_key": OPERATIONS_ATTACHMENTS_KEY,
        },
    )


def load_operations_inbox_record(intake_id: int) -> pd.DataFrame:
    return read_df(
        """
        select
            oi.id,
            oi.created_at,
            oi.source_received_at,
            oi.source,
            oi.source_subject,
            oi.source_sender,
            oi.source_message_id,
            oi.email_direction,
            oi.email_mailbox,
            oi.email_thread_id,
            oi.email_normalized_subject,
            oi.conversation_status,
            oi.email_in_reply_to,
            oi.email_references,
            oi.filename,
            oi.file_path,
            oi.parsed_data,
            oi.raw_text,
            oi.intake_status,
            oi.request_type,
            oi.conversation_key,
            oi.matched_load_id,
            oi.case_id,
            oc.case_number,
            oc.status as case_status,
            oc.owner as case_owner,
            oc.priority as case_priority,
            oc.customer as case_customer,
            oc.linked_load_id as case_linked_load_id,
            oc.next_action as case_next_action,
            oc.sla_status as case_sla_status,
            oc.message_count as case_message_count,
            oc.last_message_at as case_last_message_at,
            oc.last_message_direction as case_last_message_direction,
            oc.first_response_due_at as case_first_response_due_at,
            oc.resolution_due_at as case_resolution_due_at,
            oc.customer_wait_started_at as case_customer_wait_started_at,
            oc.department_wait_started_at as case_department_wait_started_at,
            oi.confidence_score,
            oi.action_required,
            oi.review_status
        from order_intake oi
        left join operations_cases oc on oc.id = oi.case_id
        where oi.id = :intake_id
        limit 1
        """,
        {"intake_id": int(intake_id)},
    )


def load_operations_inbox_record_set(where_clause: str) -> pd.DataFrame:
    return read_df(
        f"""
        select
            id,
            source_subject,
            source_sender,
            source_message_id,
            email_direction,
            email_thread_id,
            email_normalized_subject,
            conversation_status,
            parsed_data,
            raw_text,
            request_type,
            conversation_key,
            matched_load_id,
            case_id,
            confidence_score,
            action_required
        from order_intake
        {where_clause}
        order by created_at desc
        """
    )


def load_operations_conversation_summary_df() -> pd.DataFrame:
    conversation_key_expr = conversation_join_expr()
    return read_df(
        f"""
        select
            {conversation_key_expr} as conversation_join_key,
            count(*) as conversation_message_count,
            max(source_received_at) as last_message_at,
            (array_agg(coalesce(email_direction, 'inbound') order by source_received_at desc nulls last, created_at desc))[1] as latest_direction,
            (array_agg(coalesce(source_sender, '') order by source_received_at desc nulls last, created_at desc))[1] as latest_sender,
            (array_agg(coalesce(conversation_status, 'New Conversation') order by source_received_at desc nulls last, created_at desc))[1] as latest_conversation_status,
            max(case when coalesce(email_direction, 'inbound') = 'inbound' then source_received_at end) as last_inbound_at,
            max(case when coalesce(email_direction, 'inbound') = 'outbound' then source_received_at end) as last_outbound_at,
            max(matched_load_id) as thread_matched_load_id
        from order_intake
        where {operations_email_source_filter()}
        group by {conversation_key_expr}
        """
    )


def load_operations_conversation_timeline(conversation_key: str) -> pd.DataFrame:
    if not str(conversation_key or "").strip():
        return pd.DataFrame()

    conversation_key_expr = conversation_join_expr()
    return read_df(
        f"""
        select
            id,
            source_received_at,
            created_at,
            coalesce(email_direction, 'inbound') as email_direction,
            coalesce(email_mailbox, '') as email_mailbox,
            coalesce(source_sender, '') as source_sender,
            coalesce(source_subject, '') as source_subject,
            coalesce(source_message_id, '') as source_message_id,
            coalesce(email_thread_id, '') as email_thread_id,
            coalesce(conversation_key, '') as conversation_key,
            matched_load_id,
            parsed_data,
            coalesce(conversation_status, 'New Conversation') as conversation_status,
            coalesce(review_status, 'Open') as review_status,
            left(coalesce(raw_text, ''), 1200) as message_preview
        from order_intake
        where {operations_email_source_filter()}
          and {conversation_key_expr} = :conversation_key
        order by coalesce(source_received_at, created_at) asc, id asc
        """,
        {"conversation_key": conversation_key},
    )


def store_operations_parsed_data(intake_id: int, parsed_data_json: str, action_required: str | None = None) -> None:
    execute(
        """
        update order_intake
        set parsed_data = cast(:parsed_data as jsonb),
            action_required = coalesce(:action_required, action_required)
        where id = :intake_id
        """,
        {
            "intake_id": int(intake_id),
            "parsed_data": parsed_data_json,
            "action_required": action_required,
        },
    )


def update_intake_classification(
    intake_id: int,
    request_type: str,
    conversation_key: str,
    matched_load_id,
    confidence_score: int,
    action_required: str | None = None,
) -> None:
    execute(
        """
        update order_intake
        set request_type = :request_type,
            conversation_key = :conversation_key,
            matched_load_id = :matched_load_id,
            confidence_score = :confidence_score,
            action_required = coalesce(:action_required, action_required)
        where id = :intake_id
        """,
        {
            "intake_id": intake_id,
            "request_type": request_type,
            "conversation_key": conversation_key or None,
            "matched_load_id": matched_load_id,
            "confidence_score": confidence_score,
            "action_required": action_required,
        },
    )
