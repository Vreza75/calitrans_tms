from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from services.dispatch_workflow_service import (
    ACTIVE_DRIVER_STATUSES,
    CLOSED_STATUSES,
    LOAD_TYPE_TABS,
    _load_exception_summary,
    _load_readiness_details,
    _normalize_load_type_value,
    _status_row_style,
)
from ui_components.communication_dashboard import render_communication_dashboard
from ui_components.flow_filters import apply_service_flow_filter, render_service_flow_filter


def render_dashboard(df: pd.DataFrame) -> None:
    st.subheader("Operations Dashboard")
    st.caption("Today KPI, Tomorrow KPI, driver utilization, LFD risk, and exceptions.")

    work_df = df.copy()
    work_df["TYPE"] = work_df.get("TYPE", pd.Series("", index=work_df.index)).apply(_normalize_load_type_value)
    selected_flow = render_service_flow_filter("dashboard_service_flow")
    work_df = apply_service_flow_filter(work_df, selected_flow)

    work_df["Delivery Date Parsed"] = pd.to_datetime(
        work_df["Delivery Need Date"].astype(str).str.strip(),
        errors="coerce",
    )

    work_df["LFD Parsed"] = pd.to_datetime(
        work_df["LFD"].astype(str).str.strip(),
        errors="coerce",
    )

    today = pd.Timestamp(date.today()).normalize()
    tomorrow = today + pd.Timedelta(days=1)

    today_df = work_df[
        work_df["Delivery Date Parsed"].dt.normalize().eq(today)
    ].copy()

    tomorrow_df = work_df[
        work_df["Delivery Date Parsed"].dt.normalize().eq(tomorrow)
    ].copy()

    open_df = work_df[~work_df["Status"].isin(CLOSED_STATUSES)].copy()

    lfd_risk_df = open_df[
        open_df["LFD Parsed"].notna()
        & (open_df["LFD Parsed"] <= today + pd.Timedelta(days=1))
        & (~open_df["Status"].isin(["Delivered", "Closed", "Cancelled", "Invoiced"]))
    ].copy()

    assigned_driver_df = open_df[
        open_df["Status"].isin(ACTIVE_DRIVER_STATUSES)
        & open_df["Driver Name"].astype(str).str.strip().ne("")
        & ~open_df["Driver Name"].astype(str).str.strip().str.lower().isin(["nan", "none", "unassigned"])
    ].copy()

    active_drivers = assigned_driver_df["Driver Name"].astype(str).str.strip().nunique()

    all_drivers = open_df[
        open_df["Driver Name"].astype(str).str.strip().ne("")
        & ~open_df["Driver Name"].astype(str).str.strip().str.lower().isin(["nan", "none", "unassigned"])
    ]["Driver Name"].astype(str).str.strip().nunique()

    total_drivers = max(all_drivers, active_drivers, 1)
    driver_utilization = int(round((active_drivers / total_drivers) * 100))

    ready_to_dispatch = int(open_df["Status"].isin(["PIN Received", "Ready to Dispatch"]).sum())
    on_driver = int(open_df["Status"].isin(ACTIVE_DRIVER_STATUSES).sum())
    delivered_today = int(today_df["Status"].isin(["Delivered", "POD Received", "Ready for ProfitTools"]).sum())
    ready_for_billing = int(open_df["Status"].isin(["POD Received", "Ready for ProfitTools"]).sum())

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Today's Loads", len(today_df))
    k2.metric("Tomorrow Loads", len(tomorrow_df))
    k3.metric("Driver Utilization", f"{driver_utilization}%")
    k4.metric("LFD Risk", len(lfd_risk_df))

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("Ready to Dispatch", ready_to_dispatch)
    k6.metric("On Driver", on_driver)
    k7.metric("Delivered Today", delivered_today)
    k8.metric("Ready for Billing", ready_for_billing)

    st.divider()

    left, right = st.columns(2)

    with left:
        st.markdown("### Today's Operations")
        today_summary = (
            today_df.groupby("TYPE")
            .size()
            .reindex(LOAD_TYPE_TABS, fill_value=0)
            .reset_index()
        )
        today_summary.columns = ["Type", "Loads"]
        st.dataframe(today_summary, use_container_width=True, hide_index=True)

    with right:
        st.markdown("### Tomorrow Planning")
        tomorrow_summary = (
            tomorrow_df.groupby("TYPE")
            .size()
            .reindex(LOAD_TYPE_TABS, fill_value=0)
            .reset_index()
        )
        tomorrow_summary.columns = ["Type", "Loads"]
        st.dataframe(tomorrow_summary, use_container_width=True, hide_index=True)

    st.divider()

    left2, right2 = st.columns(2)

    with left2:
        st.markdown("### Driver Utilization")

        driver_df = open_df[
            open_df["Driver Name"].astype(str).str.strip().ne("")
            & ~open_df["Driver Name"].astype(str).str.strip().str.lower().isin(["nan", "none", "unassigned"])
        ].copy()

        if driver_df.empty:
            st.info("No assigned drivers found.")
        else:
            driver_df["Delivery Date Sort"] = pd.to_datetime(
                driver_df["Delivery Need Date"],
                errors="coerce",
            )

            workload_df = (
                driver_df.groupby("Driver Name")
                .agg(
                    Assigned_Loads=("Booking Number", "count"),
                    Active_Loads=("Status", lambda x: x.isin(ACTIVE_DRIVER_STATUSES).sum()),
                )
                .reset_index()
            )

            current_load_df = (
                driver_df[
                    driver_df["Status"].isin(ACTIVE_DRIVER_STATUSES)
                ]
                .sort_values(["Driver Name", "Delivery Date Sort"])
                .groupby("Driver Name")
                .first()
                .reset_index()
            )

            current_load_df = current_load_df[
                [
                    "Driver Name",
                    "Container Number",
                    "Warehouse",
                    "Status",
                    "Delivery Need Date",
                ]
            ].rename(
                columns={
                    "Container Number": "Current Container",
                    "Warehouse": "Destination",
                    "Status": "Current Status",
                    "Delivery Need Date": "Need Date",
                }
            )

            driver_summary = workload_df.merge(
                current_load_df,
                on="Driver Name",
                how="left",
            )

            driver_summary = driver_summary.fillna("-")

            driver_summary = driver_summary[
                [
                    "Driver Name",
                    "Assigned_Loads",
                    "Active_Loads",
                    "Current Container",
                    "Destination",
                    "Current Status",
                    "Need Date",
                ]
            ].sort_values("Assigned_Loads", ascending=False)

            st.dataframe(
                driver_summary,
                use_container_width=True,
                hide_index=True,
            )

    with right2:
        st.markdown("### LFD Risk")

        if lfd_risk_df.empty:
            st.success("No urgent LFD risk loads.")
        else:
            lfd_display = lfd_risk_df.copy()
            lfd_display["Days Left"] = (
                lfd_display["LFD Parsed"].dt.normalize() - today
            ).dt.days

            lfd_columns = [
                "Container Number",
                "Booking Number",
                "Customer",
                "TYPE",
                "LFD",
                "Days Left",
                "Status",
                "Driver Name",
            ]
            lfd_columns = [c for c in lfd_columns if c in lfd_display.columns]
            st.dataframe(
                lfd_display.sort_values("Days Left")[lfd_columns],
                use_container_width=True,
                hide_index=True,
            )

    st.divider()

    st.markdown("### Exceptions / Needs Attention")

    exceptions = _load_exception_summary(open_df)
    exception_df = pd.DataFrame(
        [{"Issue": issue, "Count": count} for issue, count in exceptions.items()]
    )
    exception_total = int(exception_df["Count"].sum()) if not exception_df.empty else 0
    if exception_total:
        st.error(f"{exception_total} exception alert(s) need attention before the work is clean.")
    else:
        st.success("No major operational exceptions are currently flagged.")

    st.dataframe(exception_df, use_container_width=True, hide_index=True)

    exception_loads = open_df.copy()
    if not exception_loads.empty:
        readiness_rows = exception_loads.apply(lambda row: _load_readiness_details(row, include_documents=False), axis=1)
        exception_loads["Readiness %"] = readiness_rows.apply(lambda details: int(details.get("score", 0)))
        exception_loads["Next Action"] = readiness_rows.apply(lambda details: details.get("next_action", ""))
        exception_loads["Exceptions"] = readiness_rows.apply(lambda details: ", ".join(details.get("exceptions", [])))
        exception_loads = exception_loads[exception_loads["Exceptions"].astype(str).str.strip().ne("")].copy()
        if not exception_loads.empty:
            st.markdown("#### Exception Loads")
            exception_columns = [
                "_row_id",
                "TYPE",
                "Status",
                "Readiness %",
                "Next Action",
                "Exceptions",
                "Booking Number",
                "Container Number",
                "Customer",
                "Port",
                "Warehouse",
                "LFD",
                "Driver Name",
            ]
            exception_cols = [column for column in exception_columns if column in exception_loads.columns]
            styled_exceptions = (
                exception_loads.sort_values(["Status", "LFD", "_row_id"], ascending=[True, True, False])[exception_cols]
                .head(40)
                .style
                .apply(_status_row_style, axis=1)
            )
            st.dataframe(styled_exceptions, use_container_width=True, hide_index=True)

    st.divider()
    render_communication_dashboard()
