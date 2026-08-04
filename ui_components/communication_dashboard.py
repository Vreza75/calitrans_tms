# ui_components/communication_dashboard.py

from __future__ import annotations

import pandas as pd
import streamlit as st

import services.operations_case_service as case_service
import services.operations_inbox_service as ops
from db_client import check_schema_readiness


def _service_attr(module, public_name: str, private_name: str | None = None):
    if hasattr(module, public_name):
        return getattr(module, public_name)
    if private_name and hasattr(module, private_name):
        return getattr(module, private_name)
    return None


def _hours_between(start, end) -> float | None:
    start_ts = pd.to_datetime(start, errors="coerce", utc=True)
    end_ts = pd.to_datetime(end, errors="coerce", utc=True)

    if pd.isna(start_ts) or pd.isna(end_ts):
        return None

    return round((end_ts - start_ts).total_seconds() / 3600, 2)


def _refresh_case_sla_statuses() -> None:
    fn = _service_attr(
        case_service,
        "refresh_operations_case_sla_statuses",
        "_refresh_operations_case_sla_statuses",
    )

    if fn:
        fn()


def _load_case_dashboard_df() -> pd.DataFrame:
    fn = _service_attr(
        case_service,
        "load_operations_case_dashboard_df",
        "_load_operations_case_dashboard_df",
    )

    if fn:
        return fn()

    fn = _service_attr(
        ops,
        "load_operations_case_dashboard_df",
        "_load_operations_case_dashboard_df",
    )

    if fn:
        return fn()

    return pd.DataFrame()


def render_communication_dashboard() -> None:
    st.markdown("### Communication Dashboard")
    st.caption("Operations Case visibility across Dispatch, Management, Billing, and Customer Service.")

    # Phase 1 correction: read-only readiness check, no DDL from render
    # (see pages_app/operations_inbox.py for the same pattern/rationale).
    schema_readiness = check_schema_readiness("order_intake", "case_id")
    if schema_readiness.reason != "ready":
        st.info(
            "Communication dashboard will be available after the Operations Inbox "
            f"schema is ready ({schema_readiness.reason}). "
            "Ask an administrator to run scripts/run_migrations.py if this persists."
        )
        return

    _refresh_case_sla_statuses()

    load_fn = _service_attr(
        case_service,
        "load_operations_case_dashboard_df",
        "_load_operations_case_dashboard_df",
    )

    if load_fn and hasattr(load_fn, "clear"):
        load_fn.clear()

    case_df = _load_case_dashboard_df()

    if case_df.empty:
        st.info("No Operations Cases found yet. Sync the Operations Inbox to populate communication metrics.")
        return

    case_df = case_df.copy()

    case_df["created_at_dt"] = pd.to_datetime(case_df["created_at"], errors="coerce", utc=True)
    case_df["first_response_at_dt"] = pd.to_datetime(case_df["first_response_at"], errors="coerce", utc=True)
    case_df["closed_at_dt"] = pd.to_datetime(case_df["closed_at"], errors="coerce", utc=True)

    case_df["resolution_at_dt"] = pd.to_datetime(
        case_df["resolved_at"].fillna(case_df["closed_at"]),
        errors="coerce",
        utc=True,
    )

    case_df["first_response_hours"] = [
        _hours_between(created, responded)
        for created, responded in zip(case_df["created_at_dt"], case_df["first_response_at_dt"])
    ]

    case_df["resolution_hours"] = [
        _hours_between(created, resolved)
        for created, resolved in zip(case_df["created_at_dt"], case_df["resolution_at_dt"])
    ]

    open_cases = case_df[~case_df["status"].eq("Closed")].copy()
    closed_cases = case_df[case_df["status"].eq("Closed")].copy()
    responded = case_df[pd.notna(case_df["first_response_hours"])].copy()

    avg_response = responded["first_response_hours"].dropna().mean()
    sla_met = case_df["sla_status"].isin(["Met", "On Track"]).sum()
    sla_compliance = int(round((sla_met / max(len(case_df), 1)) * 100))

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Open Cases", len(open_cases))
    k2.metric("Waiting by Dept", int(open_cases["status"].astype(str).str.startswith("Waiting").sum()))
    k3.metric("Avg First Response", "-" if pd.isna(avg_response) else f"{avg_response:.1f}h")
    k4.metric("Cases Closed", len(closed_cases))
    k5.metric("SLA Compliance", f"{sla_compliance}%")

    owner_summary = (
        open_cases.groupby(["owner", "status"])
        .size()
        .reset_index(name="Cases")
        .sort_values(["owner", "Cases"], ascending=[True, False])
    )

    sla_risk = open_cases[
        open_cases["sla_status"].isin(["Warning", "First Response Overdue", "Resolution Overdue"])
    ].copy()

    owner_counts = (
        open_cases.groupby("owner")
        .size()
        .reset_index(name="Open Cases")
        .sort_values("Open Cases", ascending=False)
    )

    left, right = st.columns(2)

    with left:
        st.markdown("#### Owner Workload")
        st.dataframe(owner_counts, use_container_width=True, hide_index=True)

        st.markdown("#### Waiting by Department")
        st.dataframe(owner_summary, use_container_width=True, hide_index=True)

    with right:
        st.markdown("#### SLA Watch")

        if sla_risk.empty:
            st.success("No cases are currently near or past SLA.")
        else:
            watch_cols = [
                "case_number",
                "status",
                "owner",
                "priority",
                "sla_status",
                "first_response_due_at",
                "resolution_due_at",
                "customer",
                "source_subject",
            ]
            watch_cols = [column for column in watch_cols if column in sla_risk.columns]
            st.dataframe(sla_risk[watch_cols], use_container_width=True, hide_index=True)

    with st.expander("Shared Case View", expanded=False):
        display_cols = [
            "case_number",
            "status",
            "owner",
            "priority",
            "request_type",
            "linked_load_id",
            "message_count",
            "sla_status",
            "next_action",
            "customer",
            "source_subject",
        ]
        display_cols = [column for column in display_cols if column in case_df.columns]
        st.dataframe(case_df[display_cols], use_container_width=True, hide_index=True)