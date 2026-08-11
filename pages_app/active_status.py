from __future__ import annotations

import re
from typing import Callable

import pandas as pd
import streamlit as st

from application.auth.models import AuthenticatedActor
from pages_app.dispatch_board import render_dispatch_workspace
from services.dispatch_workflow_service import (
    ACTIVE_DRIVER_STATUSES,
    CLOSED_STATUSES,
    LOAD_STATUS_FLOW,
    STATUS_MEANINGS,
    _load_department_queue,
    _load_readiness_details,
    _normalize_load_type_value,
    _safe_str,
    _status_row_style,
)
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter

def render_active_status_view(
    df: pd.DataFrame,
    principal: AuthenticatedActor,
    refresh_callback: Callable[[], None] | None = None,
    port_houston_panel_renderer: Callable | None = None,
) -> None:
    st.subheader("Active Status")
    st.caption("Quick dispatcher and manager list of current load statuses. Select a row to open dispatch details and update the load.")

    work_df = df.copy()
    if work_df.empty:
        st.info("No load data is available.")
        return

    if "Status" not in work_df.columns:
        st.warning("Status data is not available for the active status view.")
        return

    work_df["Status"] = work_df["Status"].astype(str).str.strip()
    work_df["TYPE"] = work_df.get("TYPE", pd.Series("", index=work_df.index)).apply(_normalize_load_type_value)
    readiness_details = work_df.apply(lambda row: _load_readiness_details(row, include_documents=False), axis=1)
    work_df["Readiness %"] = readiness_details.apply(lambda details: int(details.get("score", 0)))
    work_df["Next Action"] = readiness_details.apply(lambda details: details.get("next_action", ""))
    work_df["Exceptions"] = readiness_details.apply(lambda details: ", ".join(details.get("exceptions", [])))
    work_df["Department Queue"] = work_df.apply(_load_department_queue, axis=1)
    active_statuses = [status for status in LOAD_STATUS_FLOW if status not in CLOSED_STATUSES]

    filter_cols = st.columns([1, 1, 1, 1.2, 2])
    with filter_cols[0]:
        selected_flow = render_service_flow_filter("active_status_service_flow")
    with filter_cols[1]:
        include_closed = st.checkbox(
            "Include closed",
            value=False,
            key="active_status_include_closed",
        )
    with filter_cols[2]:
        queue_options = ["All Queues"] + sorted([queue for queue in work_df["Department Queue"].dropna().astype(str).unique() if queue])
        queue_filter = st.selectbox(
            "Department Queue",
            queue_options,
            index=0,
            key="active_status_queue_filter",
        )
    with filter_cols[3]:
        status_options = ["All Current Statuses"] + (LOAD_STATUS_FLOW if include_closed else active_statuses)
        status_filter = st.selectbox(
            "Status",
            status_options,
            index=0,
            key="active_status_status_filter",
        )
    with filter_cols[4]:
        search_filter = st.text_input(
            "Search",
            value="",
            placeholder="Booking, load, container, customer, driver, truck, port, warehouse",
            key="active_status_search_filter",
        )

    if not include_closed:
        work_df = work_df[~work_df["Status"].isin(CLOSED_STATUSES)].copy()
    work_df = apply_service_flow_filter(work_df, selected_flow)
    if queue_filter != "All Queues":
        work_df = work_df[work_df["Department Queue"].eq(queue_filter)].copy()
    if status_filter != "All Current Statuses":
        work_df = work_df[work_df["Status"].eq(status_filter)].copy()

    search_filter = _safe_str(search_filter).lower()
    if search_filter:
        searchable_columns = [
            "Booking Number",
            "Load ID",
            "Reference Number",
            "Container Number",
            "Customer",
            "Port",
            "Warehouse",
            "Address",
            "Driver Name",
            "Truck Assigned",
            "Chassis",
            "Status",
            "Next Action",
            "Department Queue",
            "Dispatcher Notes",
        ]
        available_columns = [column for column in searchable_columns if column in work_df.columns]
        search_blob = work_df[available_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        for term in [part for part in re.split(r"\s+", search_filter) if part]:
            search_blob_mask = search_blob.str.contains(re.escape(term), na=False)
            work_df = work_df[search_blob_mask].copy()
            search_blob = search_blob[search_blob_mask]

    status_scope = work_df.copy()
    metric_cols = st.columns(6)
    metric_cols[0].metric("Visible Loads", len(status_scope))
    metric_cols[1].metric("Ready for PIN", int(status_scope["Status"].isin(["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN"]).sum()))
    metric_cols[2].metric("Ready to Dispatch", int(status_scope["Status"].isin(["PIN Received", "Ready to Dispatch"]).sum()))
    metric_cols[3].metric("On Driver", int(status_scope["Status"].isin(ACTIVE_DRIVER_STATUSES).sum()))
    metric_cols[4].metric("Exceptions", int(status_scope["Exceptions"].astype(str).str.strip().ne("").sum()))
    metric_cols[5].metric("Billing Ready", int(status_scope["Status"].isin(["POD Received", "Ready for ProfitTools"]).sum()))

    with st.expander("Status Counts", expanded=False):
        status_counts = (
            status_scope["Status"]
            .value_counts()
            .rename_axis("Status")
            .reset_index(name="Loads")
        )
        status_counts["Meaning"] = status_counts["Status"].map(STATUS_MEANINGS).fillna("")
        if status_counts.empty:
            st.info("No statuses found for the current filters.")
        else:
            styled_counts = status_counts.style.apply(_status_row_style, axis=1)
            st.dataframe(styled_counts, use_container_width=True, hide_index=True)

    display_columns = [
        "_row_id",
        "TYPE",
        "Status",
        "Readiness %",
        "Next Action",
        "Department Queue",
        "Exceptions",
        "Booking Number",
        "Load ID",
        "Customer",
        "Container Number",
        "Port",
        "Warehouse",
        "Delivery Need Date",
        "LFD",
        "Driver Name",
        "Truck Assigned",
        "Chassis",
        "current_location",
        "eta",
    ]
    display_cols = [column for column in display_columns if column in work_df.columns]

    if work_df.empty:
        st.info("No loads match the current Active Status filters.")
        return

    sort_columns = [column for column in ["Status", "Delivery Need Date", "LFD", "_row_id"] if column in work_df.columns]
    sorted_df = work_df.sort_values(sort_columns, ascending=[True] * len(sort_columns)) if sort_columns else work_df.copy()
    styled_df = sorted_df[display_cols].style.apply(_status_row_style, axis=1)

    event = st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
        key="active_status_table",
    )
    selected_rows = event.selection.rows
    if selected_rows:
        selected_row_id = int(sorted_df.iloc[selected_rows[0]]["_row_id"])
        st.session_state["active_status_selected_row_id"] = selected_row_id

    selected_row_id = st.session_state.get("active_status_selected_row_id")
    if selected_row_id is None:
        st.caption("Select any row to open dispatch details and update status, driver, truck, chassis, notes, documents, or billing information.")
        return

    visible_ids = set(sorted_df["_row_id"].dropna().astype(int).tolist()) if "_row_id" in sorted_df.columns else set()
    if int(selected_row_id) not in visible_ids:
        st.info("Selected load is not visible with the current filters.")
        if st.button("Clear Active Status Selection", use_container_width=True):
            st.session_state.pop("active_status_selected_row_id", None)
            st.rerun()
        return

    selected_load = sorted_df[sorted_df["_row_id"].astype(int).eq(int(selected_row_id))].iloc[0]
    render_dispatch_workspace(
        selected_load, principal, refresh_callback=refresh_callback, port_houston_panel_renderer=port_houston_panel_renderer
    )
