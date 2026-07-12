# repositories/case_repo.py

"""
Operations Case repository.

This module is the database access layer for operations_cases and related
case timeline tables. It is intentionally focused on database reads/writes.
Business logic can live in services/case_service.py later.
"""

from __future__ import annotations

import pandas as pd

from db_client import execute, read_df


def int_or_none(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    value_text = str(value or "").strip()
    if not value_text:
        return None
    try:
        return int(float(value_text))
    except Exception:
        return None


def load_operations_case_by_id(case_id) -> dict:
    case_id = int_or_none(case_id)
    if case_id is None:
        return {}

    case_df = read_df(
        """
        select *
        from operations_cases
        where id = :case_id
        limit 1
        """,
        {"case_id": case_id},
    )
    return case_df.iloc[0].to_dict() if not case_df.empty else {}


def load_operations_case_by_conversation(conversation_key: str) -> dict:
    if not str(conversation_key or "").strip():
        return {}

    case_df = read_df(
        """
        select *
        from operations_cases
        where conversation_key = :conversation_key
        order by updated_at desc, id desc
        limit 1
        """,
        {"conversation_key": conversation_key},
    )
    return case_df.iloc[0].to_dict() if not case_df.empty else {}


def load_operations_case_by_number(case_number: str) -> dict:
    if not str(case_number or "").strip():
        return {}

    case_df = read_df(
        """
        select *
        from operations_cases
        where case_number = :case_number
        limit 1
        """,
        {"case_number": case_number},
    )
    return case_df.iloc[0].to_dict() if not case_df.empty else {}


def load_operations_case_timeline(case_id) -> pd.DataFrame:
    case_id = int_or_none(case_id)
    if case_id is None:
        return pd.DataFrame()

    return read_df(
        """
        select *
        from (
            select
                coalesce(source_received_at, created_at) as event_at,
                case
                    when coalesce(email_direction, 'inbound') = 'outbound'
                        then 'Reply Sent'
                    else 'Customer Email'
                end as event_type,
                coalesce(nullif(source_sender, ''), coalesce(email_direction, 'inbound')) as actor,
                case
                    when coalesce(email_direction, 'inbound') = 'outbound'
                         and lower(coalesce(email_mailbox, '')) = 'tms'
                        then 'Reply sent from TMS'
                    when coalesce(email_direction, 'inbound') = 'outbound'
                        then 'Reply synced from email'
                    else coalesce(source_subject, 'Customer email')
                end as title,
                left(coalesce(raw_text, ''), 1200) as details
            from order_intake
            where case_id = :case_id

            union all

            select
                created_at as event_at,
                case
                    when note_type = 'internal' then 'Internal Note'
                    when note_type = 'status_change' then 'Status Change'
                    else note_type
                end as event_type,
                coalesce(created_by, 'dispatcher') as actor,
                'Case Note' as title,
                note_body as details
            from operations_case_notes
            where case_id = :case_id

            union all

            select
                created_at as event_at,
                'Load Action' as event_type,
                coalesce(direction, 'internal') as actor,
                coalesce(communication_type, 'Load Communication') as title,
                left(coalesce(message_body, ''), 1200) as details
            from load_communications
            where case_id = :case_id

            union all

            select
                created_at as event_at,
                initcap(replace(event_type, '_', ' ')) as event_type,
                coalesce(actor, 'system') as actor,
                coalesce(title, event_type) as title,
                coalesce(details, '') as details
            from operations_case_events
            where case_id = :case_id
              and event_type <> 'note'
        ) timeline
        order by event_at asc
        """,
        {"case_id": case_id},
    )


def add_operations_case_note(case_id, note_body: str, note_type: str = "internal", created_by: str = "dispatcher") -> None:
    case_id = int_or_none(case_id)
    note_body = str(note_body or "").strip()
    if case_id is None or not note_body:
        return

    execute(
        """
        insert into operations_case_notes (
            case_id,
            note_body,
            note_type,
            created_by
        )
        values (
            :case_id,
            :note_body,
            :note_type,
            :created_by
        )
        """,
        {
            "case_id": case_id,
            "note_body": note_body,
            "note_type": note_type,
            "created_by": created_by,
        },
    )
    execute("update operations_cases set updated_at = now() where id = :case_id", {"case_id": case_id})


def log_operations_case_event(
    case_id,
    event_type: str,
    title: str = "",
    details: str = "",
    actor: str = "system",
    department: str = "",
) -> None:
    case_id = int_or_none(case_id)
    if case_id is None or not str(event_type or "").strip():
        return

    execute(
        """
        insert into operations_case_events (
            case_id,
            event_type,
            title,
            details,
            actor,
            department
        )
        values (
            :case_id,
            :event_type,
            :title,
            :details,
            :actor,
            :department
        )
        """,
        {
            "case_id": case_id,
            "event_type": event_type,
            "title": title or None,
            "details": details or None,
            "actor": actor or "system",
            "department": department or None,
        },
    )


def update_operations_case(
    *,
    case_id,
    status: str,
    owner: str,
    priority: str,
    linked_load_id=None,
    next_action: str = "",
) -> None:
    case_id = int_or_none(case_id)
    linked_load_id = int_or_none(linked_load_id)
    if case_id is None:
        return

    execute(
        """
        update operations_cases
        set status = :status,
            owner = :owner,
            priority = :priority,
            linked_load_id = :linked_load_id,
            next_action = nullif(:next_action, ''),
            customer_wait_started_at = case
                when :status = 'Waiting Customer' then coalesce(customer_wait_started_at, now())
                when :status <> 'Waiting Customer' then null
                else customer_wait_started_at
            end,
            department_wait_started_at = case
                when :status like 'Waiting %' and :status <> 'Waiting Customer' then coalesce(department_wait_started_at, now())
                when :status not like 'Waiting %' then null
                else department_wait_started_at
            end,
            closed_at = case when :status = 'Closed' then coalesce(closed_at, now()) else closed_at end,
            resolved_at = case when :status = 'Closed' then coalesce(resolved_at, now()) else resolved_at end,
            reopened_at = case when :status = 'Reopened' then now() else reopened_at end,
            updated_at = now()
        where id = :case_id
        """,
        {
            "case_id": case_id,
            "status": status,
            "owner": owner,
            "priority": priority,
            "linked_load_id": linked_load_id,
            "next_action": next_action or None,
        },
    )


def set_operations_case_status(case_id, status: str, next_action: str = "") -> None:
    case_id = int_or_none(case_id)
    if case_id is None:
        return

    execute(
        """
        update operations_cases
        set status = :status,
            next_action = coalesce(nullif(:next_action, ''), next_action),
            customer_wait_started_at = case
                when :status = 'Waiting Customer' then coalesce(customer_wait_started_at, now())
                when :status <> 'Waiting Customer' then null
                else customer_wait_started_at
            end,
            department_wait_started_at = case
                when :status like 'Waiting %' and :status <> 'Waiting Customer' then coalesce(department_wait_started_at, now())
                when :status not like 'Waiting %' then null
                else department_wait_started_at
            end,
            closed_at = case when :status = 'Closed' then now() else closed_at end,
            resolved_at = case when :status = 'Closed' then coalesce(resolved_at, now()) else resolved_at end,
            reopened_at = case when :status = 'Reopened' then now() else reopened_at end,
            updated_at = now()
        where id = :case_id
        """,
        {"case_id": case_id, "status": status, "next_action": next_action or None},
    )


def operations_case_metrics() -> dict:
    metrics = {
        "open": 0,
        "waiting_dispatch": 0,
        "waiting_customer": 0,
        "closed": 0,
    }

    case_df = read_df(
        """
        select coalesce(status, 'New') as status, count(*) as case_count
        from operations_cases
        group by coalesce(status, 'New')
        """
    )

    for _, row in case_df.iterrows():
        status = str(row.get("status", "New") or "New").strip()
        count = int(row.get("case_count", 0) or 0)
        if status != "Closed":
            metrics["open"] += count
        if status == "Waiting Dispatcher":
            metrics["waiting_dispatch"] += count
        elif status == "Waiting Customer":
            metrics["waiting_customer"] += count
        elif status == "Closed":
            metrics["closed"] += count

    return metrics
