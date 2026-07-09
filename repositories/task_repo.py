# repositories/task_repo.py

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from db_client import execute, read_df


TASK_STATUSES = ["Open", "In Progress", "Waiting", "Completed", "Cancelled"]
TASK_PRIORITIES = ["Critical", "High", "Medium", "Low"]
TASK_OWNERS = ["Dispatch", "Accounting", "Manager", "Customer Service", "Safety", "Operations"]


def _safe_str(value: Any) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str


def _int_or_none(value: Any):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = _safe_str(value)
    if not text:
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def _json_dump(data: dict | None) -> str:
    return json.dumps(data or {}, default=str)


def ensure_task_schema() -> None:
    """Create Dispatcher Workspace task/action tables if they do not exist."""

    execute(
        """
        create table if not exists operations_tasks (
            id bigserial primary key,
            task_title text not null,
            task_description text,
            task_status text not null default 'Open',
            task_priority text not null default 'Medium',
            owner text not null default 'Dispatch',
            due_at timestamptz,
            completed_at timestamptz,
            completed_by text,
            source_type text,
            intake_id bigint references order_intake(id) on delete set null,
            case_id bigint references operations_cases(id) on delete set null,
            load_id bigint references loads(id) on delete set null,
            customer text,
            booking_number text,
            container_number text,
            created_by text not null default 'system',
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        )
        """
    )

    execute(
        """
        create table if not exists dispatcher_actions (
            id bigserial primary key,
            action_type text not null,
            action_status text not null default 'Recorded',
            action_summary text,
            intake_id bigint references order_intake(id) on delete set null,
            case_id bigint references operations_cases(id) on delete set null,
            load_id bigint references loads(id) on delete set null,
            task_id bigint references operations_tasks(id) on delete set null,
            actor text not null default 'dispatcher',
            metadata jsonb not null default '{}'::jsonb,
            created_at timestamptz not null default now()
        )
        """
    )

    execute(
        """
        create table if not exists ai_recommendation_decisions (
            id bigserial primary key,
            intake_id bigint references order_intake(id) on delete set null,
            case_id bigint references operations_cases(id) on delete set null,
            load_id bigint references loads(id) on delete set null,
            task_id bigint references operations_tasks(id) on delete set null,
            recommendation_type text not null,
            recommendation_summary text,
            ai_confidence integer,
            decision text not null,
            decision_notes text,
            decided_by text not null default 'dispatcher',
            ai_payload jsonb not null default '{}'::jsonb,
            final_payload jsonb not null default '{}'::jsonb,
            created_at timestamptz not null default now()
        )
        """
    )

    execute("create index if not exists idx_operations_tasks_status on operations_tasks(task_status)")
    execute("create index if not exists idx_operations_tasks_owner on operations_tasks(owner)")
    execute("create index if not exists idx_operations_tasks_due_at on operations_tasks(due_at)")
    execute("create index if not exists idx_operations_tasks_case_id on operations_tasks(case_id)")
    execute("create index if not exists idx_operations_tasks_load_id on operations_tasks(load_id)")
    execute("create index if not exists idx_operations_tasks_intake_id on operations_tasks(intake_id)")
    execute("create index if not exists idx_dispatcher_actions_created_at on dispatcher_actions(created_at desc)")
    execute("create index if not exists idx_ai_recommendation_decisions_created_at on ai_recommendation_decisions(created_at desc)")


def create_task(
    *,
    title: str,
    description: str = "",
    owner: str = "Dispatch",
    priority: str = "Medium",
    due_at: str | None = None,
    source_type: str = "manual",
    intake_id=None,
    case_id=None,
    load_id=None,
    customer: str = "",
    booking_number: str = "",
    container_number: str = "",
    created_by: str = "dispatcher",
) -> int:
    ensure_task_schema()

    title = _safe_str(title)
    if not title:
        raise ValueError("Task title is required.")

    owner = owner if owner in TASK_OWNERS else "Dispatch"
    priority = priority if priority in TASK_PRIORITIES else "Medium"

    result = read_df(
        """
        insert into operations_tasks (
            task_title,
            task_description,
            task_status,
            task_priority,
            owner,
            due_at,
            source_type,
            intake_id,
            case_id,
            load_id,
            customer,
            booking_number,
            container_number,
            created_by
        )
        values (
            :task_title,
            :task_description,
            'Open',
            :task_priority,
            :owner,
            cast(:due_at as timestamptz),
            :source_type,
            :intake_id,
            :case_id,
            :load_id,
            :customer,
            :booking_number,
            :container_number,
            :created_by
        )
        returning id
        """,
        {
            "task_title": title,
            "task_description": description or None,
            "task_priority": priority,
            "owner": owner,
            "due_at": due_at,
            "source_type": source_type or "manual",
            "intake_id": _int_or_none(intake_id),
            "case_id": _int_or_none(case_id),
            "load_id": _int_or_none(load_id),
            "customer": customer or None,
            "booking_number": booking_number or None,
            "container_number": container_number or None,
            "created_by": created_by or "dispatcher",
        },
    )

    if result.empty:
        raise RuntimeError("Task was not created.")

    return int(result.iloc[0]["id"])


def update_task_status(task_id, status: str, completed_by: str = "dispatcher") -> None:
    ensure_task_schema()
    task_id = _int_or_none(task_id)
    if task_id is None:
        return

    status = status if status in TASK_STATUSES else "Open"

    execute(
        """
        update operations_tasks
        set task_status = :task_status,
            completed_at = case when :task_status = 'Completed' then now() else completed_at end,
            completed_by = case when :task_status = 'Completed' then :completed_by else completed_by end,
            updated_at = now()
        where id = :task_id
        """,
        {
            "task_id": task_id,
            "task_status": status,
            "completed_by": completed_by or "dispatcher",
        },
    )


def assign_task(task_id, owner: str, priority: str | None = None) -> None:
    ensure_task_schema()
    task_id = _int_or_none(task_id)
    if task_id is None:
        return

    owner = owner if owner in TASK_OWNERS else "Dispatch"
    priority = priority if priority in TASK_PRIORITIES else None

    execute(
        """
        update operations_tasks
        set owner = :owner,
            task_priority = coalesce(:task_priority, task_priority),
            updated_at = now()
        where id = :task_id
        """,
        {"task_id": task_id, "owner": owner, "task_priority": priority},
    )


def load_open_tasks(owner: str = "", limit: int = 200) -> pd.DataFrame:
    ensure_task_schema()

    where = "where task_status not in ('Completed', 'Cancelled')"
    params = {"limit": int(limit)}

    if _safe_str(owner):
        where += " and owner = :owner"
        params["owner"] = owner

    try:
        return read_df(
            f"""
            select *
            from operations_tasks
            {where}
            order by
                case task_priority
                    when 'Critical' then 1
                    when 'High' then 2
                    when 'Medium' then 3
                    else 4
                end,
                due_at nulls last,
                created_at desc
            limit :limit
            """,
            params,
        )
    except Exception:
        return pd.DataFrame()


def load_tasks_for_case(case_id) -> pd.DataFrame:
    ensure_task_schema()
    case_id = _int_or_none(case_id)
    if case_id is None:
        return pd.DataFrame()

    try:
        return read_df(
            """
            select *
            from operations_tasks
            where case_id = :case_id
            order by created_at desc
            """,
            {"case_id": case_id},
        )
    except Exception:
        return pd.DataFrame()


def load_tasks_for_load(load_id) -> pd.DataFrame:
    ensure_task_schema()
    load_id = _int_or_none(load_id)
    if load_id is None:
        return pd.DataFrame()

    try:
        return read_df(
            """
            select *
            from operations_tasks
            where load_id = :load_id
            order by created_at desc
            """,
            {"load_id": load_id},
        )
    except Exception:
        return pd.DataFrame()


def record_dispatcher_action(
    *,
    action_type: str,
    action_summary: str = "",
    action_status: str = "Recorded",
    intake_id=None,
    case_id=None,
    load_id=None,
    task_id=None,
    actor: str = "dispatcher",
    metadata: dict | None = None,
) -> None:
    ensure_task_schema()

    execute(
        """
        insert into dispatcher_actions (
            action_type,
            action_status,
            action_summary,
            intake_id,
            case_id,
            load_id,
            task_id,
            actor,
            metadata
        )
        values (
            :action_type,
            :action_status,
            :action_summary,
            :intake_id,
            :case_id,
            :load_id,
            :task_id,
            :actor,
            cast(:metadata as jsonb)
        )
        """,
        {
            "action_type": action_type,
            "action_status": action_status,
            "action_summary": action_summary or None,
            "intake_id": _int_or_none(intake_id),
            "case_id": _int_or_none(case_id),
            "load_id": _int_or_none(load_id),
            "task_id": _int_or_none(task_id),
            "actor": actor or "dispatcher",
            "metadata": _json_dump(metadata),
        },
    )


def record_ai_recommendation_decision(
    *,
    recommendation_type: str,
    decision: str,
    recommendation_summary: str = "",
    ai_confidence: int | None = None,
    decision_notes: str = "",
    intake_id=None,
    case_id=None,
    load_id=None,
    task_id=None,
    decided_by: str = "dispatcher",
    ai_payload: dict | None = None,
    final_payload: dict | None = None,
) -> None:
    ensure_task_schema()

    execute(
        """
        insert into ai_recommendation_decisions (
            intake_id,
            case_id,
            load_id,
            task_id,
            recommendation_type,
            recommendation_summary,
            ai_confidence,
            decision,
            decision_notes,
            decided_by,
            ai_payload,
            final_payload
        )
        values (
            :intake_id,
            :case_id,
            :load_id,
            :task_id,
            :recommendation_type,
            :recommendation_summary,
            :ai_confidence,
            :decision,
            :decision_notes,
            :decided_by,
            cast(:ai_payload as jsonb),
            cast(:final_payload as jsonb)
        )
        """,
        {
            "intake_id": _int_or_none(intake_id),
            "case_id": _int_or_none(case_id),
            "load_id": _int_or_none(load_id),
            "task_id": _int_or_none(task_id),
            "recommendation_type": recommendation_type,
            "recommendation_summary": recommendation_summary or None,
            "ai_confidence": ai_confidence,
            "decision": decision,
            "decision_notes": decision_notes or None,
            "decided_by": decided_by or "dispatcher",
            "ai_payload": _json_dump(ai_payload),
            "final_payload": _json_dump(final_payload),
        },
    )


def task_metrics() -> dict:
    ensure_task_schema()

    metrics = {
        "open": 0,
        "critical": 0,
        "overdue": 0,
        "due_today": 0,
        "completed_today": 0,
    }

    try:
        df = read_df(
            """
            select
                count(*) filter (where task_status not in ('Completed', 'Cancelled')) as open_count,
                count(*) filter (where task_status not in ('Completed', 'Cancelled') and task_priority = 'Critical') as critical_count,
                count(*) filter (where task_status not in ('Completed', 'Cancelled') and due_at < now()) as overdue_count,
                count(*) filter (where task_status not in ('Completed', 'Cancelled') and due_at::date = current_date) as due_today_count,
                count(*) filter (where task_status = 'Completed' and completed_at::date = current_date) as completed_today_count
            from operations_tasks
            """
        )
    except Exception:
        return metrics

    if df.empty:
        return metrics

    row = df.iloc[0]
    metrics["open"] = int(row.get("open_count", 0) or 0)
    metrics["critical"] = int(row.get("critical_count", 0) or 0)
    metrics["overdue"] = int(row.get("overdue_count", 0) or 0)
    metrics["due_today"] = int(row.get("due_today_count", 0) or 0)
    metrics["completed_today"] = int(row.get("completed_today_count", 0) or 0)

    return metrics
