# repositories/task_repo.py

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import text as sa_text

from db_client import execute, read_df, require_schema_ready, transaction
from utils.text_helpers import json_dump as _json_dump
from utils.text_helpers import safe_str as _safe_str


TASK_STATUSES = ["Open", "In Progress", "Waiting", "Completed", "Cancelled"]
TASK_PRIORITIES = ["Critical", "High", "Medium", "Low"]
TASK_OWNERS = ["Dispatch", "Accounting", "Manager", "Customer Service", "Safety", "Operations"]


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


def ensure_task_schema() -> None:
    """Verify the Dispatcher Workspace task/action tables are present -
    see database/dispatcher_workspace_migration.sql, which is the sole
    owner of this schema. Raises SchemaNotReadyError if that migration
    has not been applied; never runs DDL itself."""
    require_schema_ready(
        "operations_tasks", "task_status", migration_hint="database/dispatcher_workspace_migration.sql"
    )


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

    # Uses transaction() (engine.begin(), commits on clean exit) rather than
    # read_df() (engine.connect(), no auto-commit) - RETURNING id needs the
    # row back before the transaction closes, same pattern as
    # user_repo.create_user(). read_df() here silently discarded every
    # insert (implicit rollback on connection close after RETURNING was
    # already read) - fixed, no live callers were affected.
    with transaction() as conn:
        row = conn.execute(
            sa_text(
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
                """
            ),
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
        ).first()

    if row is None:
        raise RuntimeError("Task was not created.")

    return int(row[0])


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
