# services/dispatcher_workspace_service.py

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

import pandas as pd

from db_client import read_df
from utils.text_helpers import safe_str as _safe_str

try:
    from repositories import inbox_repo, case_repo, load_repo, task_repo
except Exception:  # keeps module importable during staged integration
    inbox_repo = None
    case_repo = None
    load_repo = None
    task_repo = None


def _int_or_zero(value: Any) -> int:
    try:
        if pd.isna(value):
            return 0
    except Exception:
        pass
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def load_dispatcher_workspace_metrics() -> Dict[str, int]:
    """Daily dispatcher metrics shown at the top of the workspace."""
    metrics = {
        "open_cases": 0,
        "waiting_customer": 0,
        "new_orders": 0,
        "existing_load_updates": 0,
        "documents": 0,
        "billing": 0,
        "lfd_today": 0,
        "lfd_48h": 0,
        "open_tasks": 0,
        "overdue_tasks": 0,
    }

    if inbox_repo:
        try:
            inbox_df = inbox_repo.load_operations_inbox_df(inbox_repo.inbox_review_where_clause())
            if not inbox_df.empty:
                request_type = inbox_df.get("request_type", pd.Series(dtype=str)).fillna("").astype(str)
                action_required = inbox_df.get("action_required", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
                metrics["new_orders"] = int(request_type.eq("New Booking").sum())
                metrics["existing_load_updates"] = int(request_type.isin(["Booking Update", "Appointment Update", "Customer Request", "Cancellation"]).sum())
                metrics["documents"] = int(request_type.isin(["POD Request"]).sum() + action_required.str.contains("document|pod|attachment", regex=True).sum())
                metrics["billing"] = int(request_type.eq("Billing").sum())
        except Exception:
            pass

    if case_repo:
        try:
            case_metrics = case_repo.operations_case_metrics()
            metrics["open_cases"] = _int_or_zero(case_metrics.get("open"))
            metrics["waiting_customer"] = _int_or_zero(case_metrics.get("waiting_customer"))
        except Exception:
            pass

    if task_repo:
        try:
            task_metrics = task_repo.task_metrics()
            metrics["open_tasks"] = _int_or_zero(task_metrics.get("open"))
            metrics["overdue_tasks"] = _int_or_zero(task_metrics.get("overdue"))
        except Exception:
            pass

    try:
        today = date.today()
        soon = today + timedelta(days=2)
        lfd_df = read_df(
            """
            select id, lfd, status
            from loads
            where lfd is not null
              and coalesce(status, '') not in ('Closed', 'Cancelled', 'Invoiced')
              and lfd::date <= :soon
            """,
            {"soon": soon.isoformat()},
        )
        if not lfd_df.empty:
            lfd_dates = pd.to_datetime(lfd_df["lfd"], errors="coerce").dt.date
            metrics["lfd_today"] = int((lfd_dates <= today).sum())
            metrics["lfd_48h"] = int((lfd_dates <= soon).sum())
    except Exception:
        pass

    return metrics


def load_dispatcher_work_queue(limit: int = 100) -> pd.DataFrame:
    """Unified work queue for dispatcher daily review."""
    if not inbox_repo:
        return pd.DataFrame()

    try:
        df = inbox_repo.load_operations_inbox_df(inbox_repo.inbox_review_where_clause())
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    df = df.copy()
    df["request_type"] = df.get("request_type", "").fillna("Needs Review").astype(str)
    df["priority_sort"] = df.apply(_priority_score, axis=1)
    df["received_sort"] = pd.to_datetime(df.get("source_received_at", df.get("created_at")), errors="coerce")
    df["work_item_title"] = df.apply(_work_item_title, axis=1)
    df["customer_hint"] = df.apply(_customer_hint, axis=1)
    df["reference_hint"] = df.apply(_reference_hint, axis=1)
    df["next_action"] = df.get("action_required", "").fillna("").astype(str)
    df["workspace_bucket"] = df.apply(_workspace_bucket, axis=1)

    return df.sort_values(["priority_sort", "received_sort"], ascending=[False, True]).head(int(limit))


def load_selected_work_item(intake_id: int) -> Dict[str, Any]:
    if not inbox_repo:
        return {}

    record_df = inbox_repo.load_operations_inbox_record(int(intake_id))
    if record_df.empty:
        return {}

    record = record_df.iloc[0].to_dict()
    case = {}
    load = {}
    tasks = []

    if case_repo and record.get("case_id"):
        case = case_repo.load_operations_case_by_id(record.get("case_id"))

    if load_repo and record.get("matched_load_id"):
        try:
            load = load_repo.load_by_id(record.get("matched_load_id"))
        except Exception:
            load = {}

    if task_repo:
        try:
            tasks = task_repo.list_tasks_for_context(
                intake_id=record.get("id"),
                case_id=record.get("case_id"),
                load_id=record.get("matched_load_id"),
            )
        except Exception:
            tasks = []

    return {
        "record": record,
        "case": case,
        "load": load,
        "tasks": tasks,
    }


def load_lfd_risk_loads(limit: int = 25) -> pd.DataFrame:
    try:
        return read_df(
            """
            select
                id,
                type,
                booking_number,
                container_number,
                customer,
                warehouse,
                lfd,
                status,
                driver_name
            from loads
            where lfd is not null
              and coalesce(status, '') not in ('Closed', 'Cancelled', 'Invoiced')
              and lfd::date <= current_date + interval '2 days'
            order by lfd asc, id desc
            limit :limit
            """,
            {"limit": int(limit)},
        )
    except Exception:
        return pd.DataFrame()


def _priority_score(row: pd.Series) -> int:
    text = " ".join([
        _safe_str(row.get("request_type")),
        _safe_str(row.get("action_required")),
        _safe_str(row.get("source_subject")),
    ]).lower()
    if any(term in text for term in ["urgent", "asap", "lfd today", "driver waiting", "cancel"]):
        return 100
    if any(term in text for term in ["lfd", "last free day", "appointment", "cutoff", "hold"]):
        return 80
    if _safe_str(row.get("request_type")) in {"New Booking", "Booking Update", "Appointment Update"}:
        return 70
    if _safe_str(row.get("request_type")) == "Billing":
        return 40
    return 50


def _work_item_title(row: pd.Series) -> str:
    subject = _safe_str(row.get("source_subject"))
    request_type = _safe_str(row.get("request_type")) or "Review"
    return f"{request_type}: {subject}" if subject else request_type


def _customer_hint(row: pd.Series) -> str:
    parsed = row.get("parsed_data") or {}
    if isinstance(parsed, dict):
        customer = _safe_str(parsed.get("Customer"))
        if customer:
            return customer
    sender = _safe_str(row.get("source_sender"))
    return sender.split("<")[0].strip() if sender else ""


def _reference_hint(row: pd.Series) -> str:
    parsed = row.get("parsed_data") or {}
    if isinstance(parsed, dict):
        for field in ["Booking Number", "Container Number", "Reference Number"]:
            value = _safe_str(parsed.get(field))
            if value:
                return value
    for field in ["conversation_key", "source_message_id"]:
        value = _safe_str(row.get(field))
        if value:
            return value
    return ""


def _workspace_bucket(row: pd.Series) -> str:
    request_type = _safe_str(row.get("request_type"))
    if request_type == "New Booking":
        return "New Orders"
    if request_type in {"Booking Update", "Appointment Update", "Cancellation", "Customer Request"}:
        return "Existing Loads"
    if request_type in {"POD Request"}:
        return "Documents"
    if request_type == "Billing":
        return "Billing"
    if request_type in {"Spam/Marketing", "No Action / FYI"}:
        return "Archive"
    return "Needs Review"
