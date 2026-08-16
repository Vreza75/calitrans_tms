from __future__ import annotations

import re
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text as sa_text
from sqlalchemy.engine import Connection

from db_client import execute, read_df, require_schema_ready, transaction
from utils.text_helpers import safe_str
from utils.ttl_cache import ttl_cache
from services.email_parser import extract_latest_email_body, parse_email_text
from services.operations_email_triage_service import (
    flatten_parsed_values_for_scan,
    sanitize_parsed_for_classification,
)
from services.operations_field_service import extract_operational_fields


OPERATIONS_CASE_STATUSES = [
    "New",
    "Open",
    "In Review",
    "Waiting Dispatcher",
    "Waiting Manager",
    "Waiting Customer",
    "Waiting Driver",
    "Waiting Port",
    "Waiting Warehouse",
    "Waiting Steamship",
    "Waiting Billing",
    "Waiting Safety",
    "Attached to Load",
    "Closed",
    "Reopened",
]

OPERATIONS_CASE_OWNERS = [
    "Unassigned",
    "Dispatch",
    "Operations",
    "Billing",
    "Safety",
    "Customer",
    "Driver",
    "Port",
    "Warehouse",
    "Customer Service",
    "Manager",
]

OPERATIONS_CASE_PRIORITIES = ["Critical", "High", "Medium", "Low", "Normal", "Urgent"]

OPERATIONS_SLA_FIRST_RESPONSE_HOURS = 2
OPERATIONS_SLA_RESOLUTION_HOURS = 48

_SCHEMA_READY = False


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    value_text = safe_str(value)
    if not value_text:
        return None

    try:
        return int(float(value_text))
    except Exception:
        return None


def ensure_operations_case_schema() -> None:
    """Verify the operations_cases/operations_case_notes/
    operations_case_owner_history/operations_case_events tables and their
    case_id linkage columns are present - see
    database/operations_email_workflow_migration.sql, which is the sole
    owner of this schema. Raises SchemaNotReadyError if that migration
    has not been applied; never runs DDL itself."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    require_schema_ready(
        "operations_email_replies", "case_id", migration_hint="database/operations_email_workflow_migration.sql"
    )

    _SCHEMA_READY = True


def case_customer_from_sender(sender: str) -> str:
    name, email = parseaddr(str(sender or ""))
    return _safe_str(name) or _safe_str(email) or _safe_str(sender)


def default_operations_case_owner(request_type: str) -> str:
    if request_type in {"New Booking", "Booking Update", "Appointment Update", "Quote Request"}:
        return "Dispatch"
    if request_type == "Billing":
        return "Billing"
    if request_type == "Driver Issue":
        return "Driver"
    if request_type == "Port Issue":
        return "Port"
    if request_type in {"Cancellation", "POD Request"}:
        return "Operations"
    if request_type == "Spam/Marketing":
        return "Operations"
    if request_type in {"Quote Request", "Missing Information", "Customer Request"}:
        return "Customer"
    return "Unassigned"


def operations_case_priority_from_text(subject: str, body: str, request_type: str) -> str:
    text = f"{subject or ''} {body or ''}".lower()
    critical_terms = [
        "urgent",
        "asap",
        "critical",
        "last free day today",
        "lfd today",
        "driver stuck",
        "driver waiting",
        "truck down",
        "no show",
        "gate closed",
        "hold",
    ]
    if any(term in text for term in critical_terms):
        return "Critical"
    if any(term in text for term in ["rush", "last free day", "lfd", "cutoff", "appointment today", "same day"]):
        return "High"
    if request_type in {"Cancellation", "Driver Issue", "Port Issue"}:
        return "High"
    if request_type in {"Billing", "Spam/Marketing"}:
        return "Low"
    return "Medium"


def operations_case_status_for_message(direction: str, current_status: str = "", is_new: bool = False) -> str:
    direction = _safe_str(direction).lower() or "inbound"
    current_status = _safe_str(current_status)
    if direction == "outbound":
        return "Waiting Customer"
    if current_status == "Closed":
        return "Reopened"
    if is_new:
        return "New"
    return "Waiting Dispatcher"


def next_operations_case_number() -> str:
    year = date.today().year
    prefix = f"CASE-{year}-"
    last_number = 0
    try:
        last_df = read_df(
            """
            select max(case_number) as last_case_number
            from operations_cases
            where case_number like :case_prefix
            """,
            {"case_prefix": f"{prefix}%"},
        )
        if not last_df.empty:
            last_case_number = _safe_str(last_df.iloc[0].get("last_case_number", ""))
            match = re.search(r"(\d+)$", last_case_number)
            if match:
                last_number = int(match.group(1))
    except Exception:
        last_number = 0
    return f"{prefix}{last_number + 1:04d}"


def load_operations_case_by_id(case_id, *, conn: Connection | None = None) -> dict:
    case_id = _int_or_none(case_id)
    if case_id is None:
        return {}

    sql = sa_text("select * from operations_cases where id = :case_id limit 1")

    try:
        if conn is not None:
            row = conn.execute(sql, {"case_id": case_id}).mappings().first()
            return dict(row) if row is not None else {}
        case_df = read_df(str(sql), {"case_id": case_id})
    except Exception:
        return {}
    return case_df.iloc[0].to_dict() if not case_df.empty else {}



def normalize_case_subject(subject: str) -> str:
    text = _safe_str(subject).lower()
    text = re.sub(r"^\s*(?:re|fw|fwd)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def case_identity_values(
    *,
    conversation_key: str = "",
    subject: str = "",
    sender: str = "",
    body: str = "",
    parsed: dict | None = None,
    matched_load_id=None,
) -> dict:
    parsed = sanitize_parsed_for_classification(parsed)
    tokens = _extract_reference_tokens(f"{subject}\n{body}\n{flatten_parsed_values_for_scan(parsed)}")
    identifiers = {
        _safe_str(tokens.get("booking_number", "")),
        _safe_str(tokens.get("container_number", "")),
        _safe_str(tokens.get("reference_number", "")),
        _safe_str(parsed.get("Booking Number", "")),
        _safe_str(parsed.get("Container Number", "")),
        _safe_str(parsed.get("Reference Number", "")),
    }
    identifiers = {value.upper() for value in identifiers if len(_safe_str(value)) >= 4}
    sender_domain = _feedback_sender_domain(sender)
    subject_key = _normalize_case_subject(subject)
    return {
        "conversation_key": _safe_str(conversation_key),
        "subject_key": subject_key,
        "sender_domain": sender_domain,
        "identifiers": identifiers,
        "matched_load_id": _int_or_none(matched_load_id),
    }


@ttl_cache(ttl_seconds=30)
def load_operations_case_match_context(limit: int = 1000) -> pd.DataFrame:
    try:
        return read_df(
            """
            select
                oc.id,
                oc.case_number,
                oc.conversation_key,
                oc.status,
                oc.owner,
                oc.priority,
                oc.customer,
                oc.source_subject,
                oc.request_type,
                oc.linked_load_id,
                oc.updated_at,
                lower(coalesce(oc.source_subject, '')) as case_subject_key,
                string_agg(distinct lower(coalesce(oi.conversation_key, '')), ' ') as intake_conversation_keys,
                string_agg(distinct lower(coalesce(oi.email_thread_id, '')), ' ') as intake_thread_ids,
                string_agg(distinct lower(coalesce(oi.email_normalized_subject, '')), ' ') as intake_subject_keys,
                string_agg(distinct lower(coalesce(oi.source_subject, '')), ' ') as intake_subjects,
                string_agg(distinct lower(coalesce(oi.source_sender, '')), ' ') as intake_senders,
                string_agg(
                    distinct upper(
                        concat_ws(
                            ' ',
                            coalesce(oi.conversation_key, ''),
                            coalesce(oi.source_subject, ''),
                            coalesce(left(oi.raw_text, 900), ''),
                            coalesce(oi.parsed_data #>> '{Booking Number}', ''),
                            coalesce(oi.parsed_data #>> '{Container Number}', ''),
                            coalesce(oi.parsed_data #>> '{Reference Number}', ''),
                            coalesce(oi.matched_load_id::text, '')
                        )
                    ),
                    ' '
                ) as identity_blob
            from operations_cases oc
            left join order_intake oi on oi.case_id = oc.id
            where coalesce(oc.status, 'New') <> 'Closed'
            group by oc.id
            order by oc.updated_at desc, oc.id desc
            limit :limit
            """,
            {"limit": int(limit)},
        )
    except Exception:
        return pd.DataFrame()


def score_operations_case_identity(case_row: dict, identity: dict) -> int:
    score = 0
    matched_load_id = identity.get("matched_load_id")
    case_load_id = _int_or_none(case_row.get("linked_load_id"))
    if matched_load_id is not None and case_load_id == matched_load_id:
        score += 110

    conversation_key = _safe_str(identity.get("conversation_key", "")).lower()
    if conversation_key:
        case_conversation = _safe_str(case_row.get("conversation_key", "")).lower()
        intake_keys = _safe_str(case_row.get("intake_conversation_keys", "")).lower()
        intake_threads = _safe_str(case_row.get("intake_thread_ids", "")).lower()
        if conversation_key == case_conversation:
            score += 100
        elif conversation_key in intake_keys or conversation_key in intake_threads:
            score += 90

    identity_blob = _safe_str(case_row.get("identity_blob", "")).upper()
    identifiers = identity.get("identifiers") or set()
    identifier_matches = [value for value in identifiers if value and value in identity_blob]
    if identifier_matches:
        score += 70 + (10 * min(2, len(identifier_matches) - 1))

    subject_key = _safe_str(identity.get("subject_key", "")).lower()
    if len(subject_key) >= 8:
        case_subject_key = _safe_str(case_row.get("case_subject_key", "")).lower()
        intake_subject_keys = _safe_str(case_row.get("intake_subject_keys", "")).lower()
        intake_subjects = _safe_str(case_row.get("intake_subjects", "")).lower()
        if subject_key == case_subject_key or subject_key in intake_subject_keys:
            score += 45
        elif subject_key in intake_subjects:
            score += 30

    sender_domain = _safe_str(identity.get("sender_domain", "")).lower()
    if sender_domain and sender_domain in _safe_str(case_row.get("intake_senders", "")).lower():
        score += 15

    return score


def find_existing_operations_case_for_message(
    *,
    conversation_key: str,
    subject: str,
    sender: str,
    request_type: str,
    matched_load_id=None,
    body: str = "",
    parsed: dict | None = None,
) -> dict:
    identity = _case_identity_values(
        conversation_key=conversation_key,
        subject=subject,
        sender=sender,
        body=body,
        parsed=parsed,
        matched_load_id=matched_load_id,
    )

    exact_case = _load_operations_case_by_conversation(identity["conversation_key"])

    context_df = _load_operations_case_match_context()
    if context_df.empty:
        return exact_case or {}

    scored_cases = []
    for _, row in context_df.iterrows():
        row_dict = row.to_dict()
        score = _score_operations_case_identity(row_dict, identity)
        if score:
            scored_cases.append((score, row_dict))

    if not scored_cases:
        return exact_case or {}

    scored_cases.sort(key=lambda item: (item[0], _safe_str(item[1].get("updated_at", ""))), reverse=True)
    best_score, best_case = scored_cases[0]
    if best_score >= 70 or (best_score >= 60 and request_type in {"Booking Update", "Appointment Update", "Customer Request"}):
        return _load_operations_case_by_id(best_case.get("id"))

    return exact_case or {}


def log_operations_case_event(
    case_id,
    event_type: str,
    title: str = "",
    details: str = "",
    actor: str = "system",
    department: str = "",
    *,
    conn: Connection | None = None,
) -> None:
    """Best-effort audit event - failure here must never block or corrupt
    the operation that triggered it. `conn` is optional; when given, the
    insert runs inside its own SAVEPOINT (conn.begin_nested()) so a
    failure rolls back only this insert, not the caller's whole
    transaction - PostgreSQL aborts an entire transaction on any
    statement error until rollback/savepoint-rollback, so swallowing the
    Python exception alone would leave a shared connection unusable for
    subsequent statements without this."""
    case_id = _int_or_none(case_id)
    if case_id is None or not _safe_str(event_type):
        return

    sql = sa_text(
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
        """
    )
    params = {
        "case_id": case_id,
        "event_type": event_type,
        "title": title or None,
        "details": details or None,
        "actor": actor or "system",
        "department": department or None,
    }

    if conn is not None:
        try:
            with conn.begin_nested():
                conn.execute(sql, params)
        except Exception:
            pass
        return

    try:
        execute(str(sql), params)
    except Exception:
        pass


def record_operations_case_owner_change(
    case_id, old_owner: str, new_owner: str, changed_by: str = "dispatcher", *, conn: Connection | None = None
) -> None:
    """Best-effort audit trail - see log_operations_case_event's docstring
    for why `conn` (when given) must isolate this write in its own
    SAVEPOINT rather than just swallowing the Python exception."""
    case_id = _int_or_none(case_id)
    old_owner = _safe_str(old_owner)
    new_owner = _safe_str(new_owner) or "Unassigned"
    if case_id is None or old_owner == new_owner:
        return

    sql = sa_text(
        """
        insert into operations_case_owner_history (
            case_id,
            old_owner,
            new_owner,
            changed_by
        )
        values (
            :case_id,
            :old_owner,
            :new_owner,
            :changed_by
        )
        """
    )
    params = {
        "case_id": case_id,
        "old_owner": old_owner or None,
        "new_owner": new_owner,
        "changed_by": changed_by,
    }

    if conn is not None:
        try:
            with conn.begin_nested():
                conn.execute(sql, params)
            _log_operations_case_event(
                case_id,
                "assigned",
                "Owner changed",
                f"Owner changed from {old_owner or 'Unassigned'} to {new_owner}.",
                actor=changed_by,
                department=new_owner,
                conn=conn,
            )
        except Exception:
            pass
        return

    try:
        execute(str(sql), params)
        _log_operations_case_event(
            case_id,
            "assigned",
            "Owner changed",
            f"Owner changed from {old_owner or 'Unassigned'} to {new_owner}.",
            actor=changed_by,
            department=new_owner,
        )
    except Exception:
        pass


def update_operations_case_sla(case_id, *, conn: Connection | None = None) -> None:
    """Best-effort SLA-status refresh - see log_operations_case_event's
    docstring for why `conn` (when given) must isolate this write in its
    own SAVEPOINT rather than just swallowing the Python exception."""
    case_id = _int_or_none(case_id)
    if case_id is None:
        return

    sql = sa_text(
        """
        update operations_cases
        set sla_status = case
                when status = 'Closed'
                     and first_response_at is not null
                     and first_response_due_at is not null
                     and first_response_at <= first_response_due_at
                     and (resolution_due_at is null or coalesce(resolved_at, closed_at, now()) <= resolution_due_at)
                    then 'Met'
                when status = 'Closed' then 'Closed'
                when first_response_at is null
                     and first_response_due_at is not null
                     and now() > first_response_due_at
                    then 'First Response Overdue'
                when resolution_due_at is not null
                     and now() > resolution_due_at
                    then 'Resolution Overdue'
                when first_response_at is null
                     and first_response_due_at is not null
                     and now() > first_response_due_at - interval '30 minutes'
                    then 'Warning'
                else 'On Track'
            end,
            updated_at = now()
        where id = :case_id
        """
    )
    params = {"case_id": case_id}

    if conn is not None:
        try:
            with conn.begin_nested():
                conn.execute(sql, params)
        except Exception:
            pass
        return

    try:
        execute(str(sql), params)
    except Exception:
        pass


def refresh_operations_case_sla_statuses() -> None:
    try:
        execute(
            """
            update operations_cases
            set sla_status = case
                    when status = 'Closed'
                         and first_response_at is not null
                         and first_response_due_at is not null
                         and first_response_at <= first_response_due_at
                         and (resolution_due_at is null or coalesce(resolved_at, closed_at, now()) <= resolution_due_at)
                        then 'Met'
                    when status = 'Closed' then 'Closed'
                    when first_response_at is null
                         and first_response_due_at is not null
                         and now() > first_response_due_at
                        then 'First Response Overdue'
                    when resolution_due_at is not null
                         and now() > resolution_due_at
                        then 'Resolution Overdue'
                    when first_response_at is null
                         and first_response_due_at is not null
                         and now() > first_response_due_at - interval '30 minutes'
                        then 'Warning'
                    else 'On Track'
                end
            where status <> 'Closed'
               or sla_status not in ('Met', 'Closed')
            """
        )
    except Exception:
        pass


def get_or_create_operations_case(
    *,
    conversation_key: str,
    subject: str,
    sender: str,
    request_type: str,
    matched_load_id=None,
    direction: str = "inbound",
    next_action: str = "",
    body: str = "",
) -> dict:
    _ensure_operations_case_schema()
    conversation_key = _safe_str(conversation_key)
    body = extract_latest_email_body(body) or body
    parsed_for_identity = {}
    try:
        parsed_for_identity = parse_email_text(subject, body, sender)
    except Exception:
        parsed_for_identity = {}
    existing_case = _find_existing_operations_case_for_message(
        conversation_key=conversation_key,
        subject=subject,
        sender=sender,
        request_type=request_type,
        matched_load_id=matched_load_id,
        body=body,
        parsed=parsed_for_identity,
    )
    linked_load_id = _int_or_none(matched_load_id)
    status = _operations_case_status_for_message(direction, existing_case.get("status", ""), is_new=not existing_case)
    priority = _operations_case_priority_from_text(subject, body, request_type)

    if existing_case:
        case_id = int(existing_case["id"])
        execute(
            """
            update operations_cases
            set status = :status,
                owner = case
                    when coalesce(owner, '') = '' or owner = 'Unassigned' then :owner
                    else owner
                end,
                priority = case
                    when :priority_rank > case
                        when priority = 'Critical' then 5
                        when priority = 'Urgent' then 4
                        when priority = 'High' then 3
                        when priority = 'Medium' then 2
                        when priority = 'Normal' then 2
                        else 1
                    end then :priority
                    else priority
                end,
                customer = coalesce(nullif(customer, ''), :customer),
                source_subject = coalesce(nullif(source_subject, ''), :source_subject),
                request_type = coalesce(:request_type, request_type),
                linked_load_id = coalesce(:linked_load_id, linked_load_id),
                next_action = coalesce(nullif(:next_action, ''), next_action),
                last_message_direction = :last_message_direction,
                last_message_at = now(),
                first_response_at = case
                    when :last_message_direction = 'outbound' then coalesce(first_response_at, now())
                    else first_response_at
                end,
                customer_wait_started_at = case
                    when :status = 'Waiting Customer' then coalesce(customer_wait_started_at, now())
                    else customer_wait_started_at
                end,
                department_wait_started_at = case
                    when :status like 'Waiting %' and :status <> 'Waiting Customer' then coalesce(department_wait_started_at, now())
                    else department_wait_started_at
                end,
                updated_at = now(),
                reopened_at = case when status = 'Closed' and :status = 'Reopened' then now() else reopened_at end,
                closed_at = case when :status = 'Closed' then now() else closed_at end
            where id = :case_id
            """,
            {
                "case_id": case_id,
                "status": status,
                "owner": _default_operations_case_owner(request_type),
                "priority": priority,
                "priority_rank": {"Critical": 5, "Urgent": 5, "High": 4, "Medium": 3, "Normal": 3, "Low": 1}.get(priority, 3),
                "customer": _case_customer_from_sender(sender),
                "source_subject": subject or None,
                "request_type": request_type or None,
                "linked_load_id": linked_load_id,
                "next_action": next_action or None,
                "last_message_direction": _safe_str(direction).lower() or "inbound",
            },
        )
        updated_case = _load_operations_case_by_id(case_id)
        _record_operations_case_owner_change(
            case_id,
            _safe_str(existing_case.get("owner", "")),
            _safe_str(updated_case.get("owner", "")),
            changed_by="system",
        )
        _update_operations_case_sla(case_id)
        return updated_case

    for _ in range(5):
        case_number = _next_operations_case_number()
        try:
            execute(
                """
                insert into operations_cases (
                    case_number,
                    conversation_key,
                    status,
                    owner,
                    priority,
                    customer,
                    source_subject,
                    request_type,
                    linked_load_id,
                    next_action,
                    last_message_direction,
                    last_message_at,
                    first_response_due_at,
                    resolution_due_at,
                    customer_wait_started_at,
                    department_wait_started_at,
                    first_response_at,
                    message_count
                )
                values (
                    :case_number,
                    :conversation_key,
                    :status,
                    :owner,
                    :priority,
                    :customer,
                    :source_subject,
                    :request_type,
                    :linked_load_id,
                    :next_action,
                    :last_message_direction,
                    now(),
                    now() + interval '2 hours',
                    now() + interval '48 hours',
                    case when :status = 'Waiting Customer' then now() else null end,
                    case when :status like 'Waiting %' and :status <> 'Waiting Customer' then now() else null end,
                    case when :last_message_direction = 'outbound' then now() else null end,
                    0
                )
                """,
                {
                    "case_number": case_number,
                    "conversation_key": conversation_key or None,
                    "status": status,
                    "owner": _default_operations_case_owner(request_type),
                    "priority": priority,
                    "customer": _case_customer_from_sender(sender),
                    "source_subject": subject or None,
                    "request_type": request_type or None,
                    "linked_load_id": linked_load_id,
                    "next_action": next_action or None,
                    "last_message_direction": _safe_str(direction).lower() or "inbound",
                },
            )
            created_case = _load_operations_case_by_conversation(conversation_key)
            if created_case:
                _record_operations_case_owner_change(
                    created_case.get("id"),
                    "",
                    _safe_str(created_case.get("owner", "")),
                    changed_by="system",
                )
                _log_operations_case_event(
                    created_case.get("id"),
                    "created",
                    "Case created",
                    _safe_str(created_case.get("source_subject", "")),
                    actor="system",
                    department=_safe_str(created_case.get("owner", "")),
                )
                _update_operations_case_sla(created_case.get("id"))
                return created_case
            return _load_operations_case_by_number(case_number)
        except Exception:
            continue

    return _load_operations_case_by_conversation(conversation_key)


def load_operations_case_by_number(case_number: str) -> dict:
    if not _safe_str(case_number):
        return {}
    try:
        case_df = read_df(
            """
            select *
            from operations_cases
            where case_number = :case_number
            limit 1
            """,
            {"case_number": case_number},
        )
    except Exception:
        return {}
    return case_df.iloc[0].to_dict() if not case_df.empty else {}


def sync_operations_case_summary(case_id) -> None:
    case_id = _int_or_none(case_id)
    if case_id is None:
        return
    try:
        execute(
            """
            update operations_cases oc
            set message_count = coalesce(summary.message_count, 0),
                last_message_at = summary.last_message_at,
                last_message_direction = summary.last_message_direction,
                linked_load_id = coalesce(oc.linked_load_id, summary.linked_load_id),
                updated_at = now()
            from (
                select
                    count(*) as message_count,
                    max(coalesce(source_received_at, created_at)) as last_message_at,
                    (array_agg(coalesce(email_direction, 'inbound') order by coalesce(source_received_at, created_at) desc, id desc))[1] as last_message_direction,
                    max(matched_load_id) as linked_load_id
                from order_intake
                where case_id = :case_id
            ) summary
            where oc.id = :case_id
            """,
            {"case_id": case_id},
        )
    except Exception:
        pass


def sync_operations_case_for_intake_record(record) -> dict:
    conversation_key = _row_conversation_join_key(record)
    request_type = _safe_str(record.get("request_type", "")) or "Customer Request"
    case = _get_or_create_operations_case(
        conversation_key=conversation_key,
        subject=_safe_str(record.get("source_subject", "")),
        sender=_safe_str(record.get("source_sender", "")),
        request_type=request_type,
        matched_load_id=record.get("matched_load_id"),
        direction=_safe_str(record.get("email_direction", "inbound")) or "inbound",
        next_action=_safe_str(record.get("action_required", "")),
        body=_safe_str(record.get("raw_text", "")),
    )
    case_id = _int_or_none(case.get("id"))
    if case_id is not None:
        execute(
            """
            update order_intake
            set case_id = :case_id
            where id = :intake_id
            """,
            {"case_id": case_id, "intake_id": int(record["id"])},
        )
        _sync_operations_case_summary(case_id)
    return case


def sync_operations_case_for_intake_id(intake_id: int) -> dict:
    try:
        record_df = _load_operations_inbox_record(int(intake_id))
    except Exception:
        record_df = pd.DataFrame()
    if record_df.empty:
        return {}
    return _sync_operations_case_for_intake_record(record_df.iloc[0])


def set_operations_case_status(case_id, status: str, next_action: str = "") -> None:
    case_id = _int_or_none(case_id)
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
            'status_change',
            'system'
        )
        """,
        {
            "case_id": case_id,
            "note_body": f"Case status changed to {status}. {next_action or ''}".strip(),
        },
    )
    _log_operations_case_event(
        case_id,
        "status_change",
        f"Status changed to {status}",
        next_action,
        actor="dispatcher",
    )
    if status == "Closed":
        _log_operations_case_event(case_id, "closed", "Case closed", next_action, actor="dispatcher")
    _update_operations_case_sla(case_id)


def update_operations_case(
    *,
    case_id,
    status: str,
    owner: str,
    priority: str,
    linked_load_id=None,
    next_action: str = "",
) -> None:
    """Update a case's status/owner/priority, record the change, and sync
    the linked load onto order_intake.

    Mandatory vs. best-effort, and why: the case UPDATE, the
    status-change note, and the order_intake sync are the actual state
    change this function promises the caller - they run inside one
    db_client.transaction() and either all commit or all roll back. The
    owner-change history row, the status-change event row, and the SLA
    recompute are audit/derived data whose failure must never block or
    partially-corrupt that mandatory change (this was already true before
    this function ran inside a shared transaction - see each function's
    own docstring for why a SAVEPOINT, not just a caught Python exception,
    is required to preserve that once they share a connection)."""
    case_id = _int_or_none(case_id)
    if case_id is None:
        return
    linked_load_id = _int_or_none(linked_load_id)

    update_sql = sa_text(
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
        """
    )
    note_sql = sa_text(
        """
        insert into operations_case_notes (case_id, note_body, note_type, created_by)
        values (:case_id, :note_body, 'status_change', 'dispatcher')
        """
    )
    order_intake_sync_sql = sa_text(
        "update order_intake set matched_load_id = coalesce(:linked_load_id, matched_load_id) where case_id = :case_id"
    )

    with transaction() as conn:
        old_case = _load_operations_case_by_id(case_id, conn=conn)

        conn.execute(
            update_sql,
            {
                "case_id": case_id,
                "status": status,
                "owner": owner,
                "priority": priority,
                "linked_load_id": linked_load_id,
                "next_action": next_action or None,
            },
        )

        _record_operations_case_owner_change(
            case_id, _safe_str(old_case.get("owner", "")), owner, changed_by="dispatcher", conn=conn
        )
        if _safe_str(old_case.get("status", "")) != status:
            _log_operations_case_event(
                case_id,
                "status_change",
                f"Status changed to {status}",
                next_action,
                actor="dispatcher",
                department=owner,
                conn=conn,
            )

        conn.execute(
            note_sql,
            {
                "case_id": case_id,
                "note_body": (
                    f"Case updated to {status}; owner {owner}; priority {priority}; "
                    f"linked load {_safe_str(linked_load_id) or '-'}."
                ),
            },
        )

        _update_operations_case_sla(case_id, conn=conn)

        conn.execute(order_intake_sync_sql, {"case_id": case_id, "linked_load_id": linked_load_id})


def add_operations_case_note(case_id, note_body: str, note_type: str = "internal", created_by: str = "dispatcher") -> None:
    """Add a case note and touch the case's updated_at. Both are mandatory
    (one transaction); the resulting audit event is best-effort (its own
    SAVEPOINT) - see update_operations_case's docstring for the same
    mandatory-vs-best-effort split applied here."""
    case_id = _int_or_none(case_id)
    note_body = _safe_str(note_body)
    if case_id is None or not note_body:
        return

    note_sql = sa_text(
        """
        insert into operations_case_notes (case_id, note_body, note_type, created_by)
        values (:case_id, :note_body, :note_type, :created_by)
        """
    )
    touch_sql = sa_text("update operations_cases set updated_at = now() where id = :case_id")

    with transaction() as conn:
        conn.execute(
            note_sql,
            {"case_id": case_id, "note_body": note_body, "note_type": note_type, "created_by": created_by},
        )
        conn.execute(touch_sql, {"case_id": case_id})
        _log_operations_case_event(
            case_id,
            "note",
            "Internal note added" if note_type == "internal" else "Case note added",
            note_body,
            actor=created_by,
            conn=conn,
        )


@ttl_cache(ttl_seconds=30)
def load_operations_case_timeline(case_id) -> pd.DataFrame:
    case_id = _int_or_none(case_id)
    if case_id is None:
        return pd.DataFrame()
    try:
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
    except Exception:
        return pd.DataFrame()


@ttl_cache(ttl_seconds=30)
def load_recent_operations_cases(current_case_id=None) -> pd.DataFrame:
    current_case_id = _int_or_none(current_case_id)
    try:
        return read_df(
            """
            select
                id,
                case_number,
                status,
                owner,
                priority,
                customer,
                source_subject,
                linked_load_id,
                updated_at
            from operations_cases
            where (:current_case_id is null or id <> :current_case_id)
            order by updated_at desc, id desc
            limit 250
            """,
            {"current_case_id": current_case_id},
        )
    except Exception:
        return pd.DataFrame()


@ttl_cache(ttl_seconds=30)
def load_operations_case_owner_history(case_id) -> pd.DataFrame:
    case_id = _int_or_none(case_id)
    if case_id is None:
        return pd.DataFrame()
    try:
        return read_df(
            """
            select
                changed_at,
                old_owner,
                new_owner,
                changed_by
            from operations_case_owner_history
            where case_id = :case_id
            order by changed_at desc, id desc
            limit 50
            """,
            {"case_id": case_id},
        )
    except Exception:
        return pd.DataFrame()


@ttl_cache(ttl_seconds=30)
def _operations_case_metrics() -> dict:
    metrics = {
        "open": 0,
        "waiting_dispatch": 0,
        "waiting_customer": 0,
        "closed": 0,
    }
    try:
        _refresh_operations_case_sla_statuses()
        case_df = read_df(
            """
            select coalesce(status, 'New') as status, count(*) as case_count
            from operations_cases
            group by coalesce(status, 'New')
            """
        )
    except Exception:
        return metrics
    for _, row in case_df.iterrows():
        status = _safe_str(row.get("status", "New"))
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


@ttl_cache(ttl_seconds=30)
def load_operations_case_dashboard_df() -> pd.DataFrame:
    try:
        return read_df(
            """
            select
                id,
                case_number,
                status,
                owner,
                priority,
                customer,
                request_type,
                linked_load_id,
                next_action,
                message_count,
                sla_status,
                created_at,
                updated_at,
                first_response_due_at,
                first_response_at,
                resolution_due_at,
                resolved_at,
                customer_wait_started_at,
                department_wait_started_at,
                closed_at,
                source_subject
            from operations_cases
            order by updated_at desc, id desc
            limit 1000
            """
        )
    except Exception:
        return pd.DataFrame()


def merge_operations_cases(source_case_id, target_case_id) -> bool:
    source_case_id = _int_or_none(source_case_id)
    target_case_id = _int_or_none(target_case_id)
    if source_case_id is None or target_case_id is None or source_case_id == target_case_id:
        return False

    source_case = _load_operations_case_by_id(source_case_id)
    target_case = _load_operations_case_by_id(target_case_id)
    if not source_case or not target_case:
        return False

    execute("update order_intake set case_id = :target_case_id where case_id = :source_case_id", {"target_case_id": target_case_id, "source_case_id": source_case_id})
    execute("update load_communications set case_id = :target_case_id where case_id = :source_case_id", {"target_case_id": target_case_id, "source_case_id": source_case_id})
    execute("update operations_email_replies set case_id = :target_case_id where case_id = :source_case_id", {"target_case_id": target_case_id, "source_case_id": source_case_id})
    execute("update operations_case_notes set case_id = :target_case_id where case_id = :source_case_id", {"target_case_id": target_case_id, "source_case_id": source_case_id})
    execute("update operations_case_events set case_id = :target_case_id where case_id = :source_case_id", {"target_case_id": target_case_id, "source_case_id": source_case_id})
    execute("update operations_case_owner_history set case_id = :target_case_id where case_id = :source_case_id", {"target_case_id": target_case_id, "source_case_id": source_case_id})
    _add_operations_case_note(
        target_case_id,
        f"Merged duplicate case {source_case.get('case_number')} into this case.",
    )
    execute(
        """
        update operations_cases
        set status = 'Closed',
            next_action = :next_action,
            closed_at = now(),
            resolved_at = coalesce(resolved_at, now()),
            updated_at = now()
        where id = :source_case_id
        """,
        {
            "source_case_id": source_case_id,
            "next_action": f"Merged into {target_case.get('case_number')}.",
        },
    )
    _sync_operations_case_summary(target_case_id)
    return True


@ttl_cache(ttl_seconds=30)
def load_operations_case_email_summary(case_id) -> dict:
    case_id = _int_or_none(case_id)
    if case_id is None:
        return {}
    try:
        summary_df = read_df(
            """
            select
                count(*) as total_messages,
                max(case
                    when coalesce(email_direction, 'inbound') = 'inbound'
                        then coalesce(source_received_at, created_at)
                    end) as last_customer_email_at,
                max(case
                    when coalesce(email_direction, 'inbound') = 'outbound'
                        then coalesce(source_received_at, created_at)
                    end) as last_reply_at,
                (array_agg(coalesce(source_sender, '') order by coalesce(source_received_at, created_at) desc, id desc)
                    filter (where coalesce(email_direction, 'inbound') = 'outbound'))[1] as last_reply_by,
                (array_agg(coalesce(email_mailbox, '') order by coalesce(source_received_at, created_at) desc, id desc)
                    filter (where coalesce(email_direction, 'inbound') = 'outbound'))[1] as last_reply_mailbox
            from order_intake
            where case_id = :case_id
            """,
            {"case_id": case_id},
        )
    except Exception:
        return {}
    return summary_df.iloc[0].to_dict() if not summary_df.empty else {}


def format_short_timestamp(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%m/%d %I:%M %p")


def format_relative_timestamp(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return "-"
    delta_minutes = int(max(0, (pd.Timestamp.now(tz="UTC") - parsed).total_seconds() // 60))
    if delta_minutes < 60:
        return f"{delta_minutes}m ago"
    hours = delta_minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def format_case_sla_label(operations_case: dict) -> str:
    status = _safe_str(operations_case.get("status", ""))
    sla_status = _safe_str(operations_case.get("sla_status", "On Track")) or "On Track"
    if status == "Closed":
        return sla_status

    first_response_at = _safe_str(operations_case.get("first_response_at", ""))
    due_value = operations_case.get("resolution_due_at") if first_response_at else operations_case.get("first_response_due_at")
    due_at = pd.to_datetime(due_value, errors="coerce", utc=True)
    if pd.isna(due_at):
        return sla_status

    delta_seconds = int((due_at - pd.Timestamp.now(tz="UTC")).total_seconds())
    label = "remaining" if delta_seconds >= 0 else "overdue"
    abs_seconds = abs(delta_seconds)
    hours = abs_seconds // 3600
    minutes = (abs_seconds % 3600) // 60
    if hours:
        return f"{hours}h {minutes}m {label}"
    return f"{minutes}m {label}"



# ============================================================
# Compatibility aliases for extracted Operations modules
# ============================================================
from email.utils import parseaddr as _parseaddr

_safe_str = safe_str
_int_or_none = int_or_none
_ensure_operations_case_schema = ensure_operations_case_schema
_load_operations_case_by_id = load_operations_case_by_id
_normalize_case_subject = normalize_case_subject
_case_identity_values = case_identity_values
_load_operations_case_match_context = load_operations_case_match_context
_score_operations_case_identity = score_operations_case_identity
_find_existing_operations_case_for_message = find_existing_operations_case_for_message
_log_operations_case_event = log_operations_case_event
_record_operations_case_owner_change = record_operations_case_owner_change
_update_operations_case_sla = update_operations_case_sla
_refresh_operations_case_sla_statuses = refresh_operations_case_sla_statuses
_get_or_create_operations_case = get_or_create_operations_case
_load_operations_case_by_number = load_operations_case_by_number
_sync_operations_case_summary = sync_operations_case_summary
_sync_operations_case_for_intake_record = sync_operations_case_for_intake_record
_sync_operations_case_for_intake_id = sync_operations_case_for_intake_id
_set_operations_case_status = set_operations_case_status
_update_operations_case = update_operations_case
_add_operations_case_note = add_operations_case_note
_load_operations_case_timeline = load_operations_case_timeline
_load_recent_operations_cases = load_recent_operations_cases
_load_operations_case_owner_history = load_operations_case_owner_history
operations_case_metrics = _operations_case_metrics
_load_operations_case_dashboard_df = load_operations_case_dashboard_df
_merge_operations_cases = merge_operations_cases
_load_operations_case_email_summary = load_operations_case_email_summary
_format_short_timestamp = format_short_timestamp
_format_relative_timestamp = format_relative_timestamp
_format_case_sla_label = format_case_sla_label


def extract_reference_tokens(text: str) -> dict:
    fields = extract_operational_fields(newest_message=str(text or ""))["fields"]
    mapping = {
        "booking_number": fields.get("Booking Number"),
        "container_number": fields.get("Container Number"),
        "reference_number": fields.get("Reference Number"),
    }
    return {
        key: str(value).upper()
        for key, value in mapping.items()
        if value
    }


def feedback_sender_domain(sender: str) -> str:
    _name, email = _parseaddr(str(sender or ""))
    email = _safe_str(email).lower()
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def load_operations_case_by_conversation(conversation_key: str) -> dict:
    conversation_key = _safe_str(conversation_key)
    if not conversation_key:
        return {}
    try:
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
    except Exception:
        return {}
    return case_df.iloc[0].to_dict() if not case_df.empty else {}


def load_operations_inbox_record(intake_id) -> dict:
    intake_id = _int_or_none(intake_id)
    if intake_id is None:
        return {}
    try:
        record_df = read_df(
            """
            select *
            from order_intake
            where id = :intake_id
            limit 1
            """,
            {"intake_id": intake_id},
        )
    except Exception:
        return {}
    return record_df.iloc[0].to_dict() if not record_df.empty else {}


_extract_reference_tokens = extract_reference_tokens
_feedback_sender_domain = feedback_sender_domain
_load_operations_case_by_conversation = load_operations_case_by_conversation
_load_operations_inbox_record = load_operations_inbox_record
_case_customer_from_sender = case_customer_from_sender
_default_operations_case_owner = default_operations_case_owner
_operations_case_priority_from_text = operations_case_priority_from_text
_operations_case_status_for_message = operations_case_status_for_message
_next_operations_case_number = next_operations_case_number

parseaddr = _parseaddr


def row_conversation_join_key(record: dict) -> str:
    if not hasattr(record, "get"):
        return ""
    for key in ["conversation_key", "email_thread_id", "email_normalized_subject", "source_message_id"]:
        value = _safe_str(record.get(key, ""))
        if value:
            return value
    return _safe_str(record.get("source_subject", ""))


_row_conversation_join_key = row_conversation_join_key
