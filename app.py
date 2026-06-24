from __future__ import annotations

import json
from datetime import date, datetime
from email.utils import parseaddr
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote
import base64
import os
import smtplib

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.message import EmailMessage

import pandas as pd
import streamlit as st

from admin_pages import render_master_data_admin
from config import ACTIVE_STATUSES, APP_NAME, DOCUMENT_STORAGE_DIR, EDITABLE_COLUMNS
from db_client import DispatchDatabaseClient, execute, read_df
from email_client import fetch_recent_operations_emails
from email_parser import parse_email_text
from operations_ai import (
    generate_operations_ai_suggestion,
    is_operations_ai_auto_classify_enabled,
    is_operations_ai_configured,
)
from order_parser import extract_text_from_pdf, parse_order_text
from port_houston_client import (
    BOOKING_FIELDS,
    PortHoustonClient,
    PortHoustonError,
    UNIT_FIELDS,
    VESSEL_FIELDS,
    content_records,
    flatten_record,
    get_nested,
    get_port_houston_settings,
    summarize_unit,
)
from profittools_export import export_ready_loads
from validators import validate_dispatch_rows
from order_intake import get_intake_queue, get_intake_record, create_load_from_intake, update_intake_status, render_order_upload_panel, render_email_intake_panel


st.set_page_config(
    page_title="CaliTrans TMS",
    page_icon="CT",
    layout="wide",
    initial_sidebar_state="expanded",
)


LOAD_STATUS_FLOW = [
    "New",
    "Hold/Need Info",
    "Awaiting Appointment",
    "Ready to Dispatch",
    "Assigned",
    "En Route to Pickup",
    "At Pickup",
    "Loaded",
    "En Route To Delivery",
    "Delivered",
    "Returning Empty",
    "POD Received",
    "Ready for ProfitTools",
    "Exported to ProfitTools",
    "Invoiced",
    "Closed",
    "Cancelled",
]

DISPATCH_BOARD_STATUSES = [
    "Ready to Dispatch",
    "Assigned",
    "En Route to Pickup",
    "At Pickup",
    "Loaded",
    "En Route To Delivery",
    "Delivered",
    "Returning Empty",
]

LOAD_TYPE_TABS = ["Import", "Export", "Export Local", "Import Local"]

ACTIVE_DRIVER_STATUSES = [
    "Assigned",
    "En Route to Pickup",
    "At Pickup",
    "Loaded",
    "En Route To Delivery",
    "Returning Empty",
]

CLOSED_STATUSES = ["Closed", "Cancelled", "Invoiced"]

SUMMARY_COLUMNS = [
    "_row_id",
    "TYPE",
    "Booking Number",
    "Load ID",
    "Customer",
    "Container Number",
    "Warehouse",
    "Delivery Need Date",
    "Status",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Port",
    "LFD",
    "Dispatcher Notes",
]

EXT_LOAD_COLUMNS = [
    "steamship_line",
    "vessel_name",
    "terminal",
    "pickup_appointment",
    "delivery_appointment",
    "empty_return_location",
    "empty_return_date",
    "chassis_provider",
    "pickup_reference",
    "delivery_reference",
    "invoice_status",
    "driver_pay_status",
    "customer_rate",
    "carrier_pay",
    "accessorials",
    "margin",
    "current_location",
    "eta",
    "live_load_status",
    "live_unload_status",
    "last_driver_update",
]

LOAD_SEARCH_COLUMNS = [
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
    "Dispatcher Notes",
]

NAVIGATION_SECTIONS = [
    "Operations Inbox",
    "Orders/Load Management",
    "Dispatch Board",
    "Calendar View",
    "Documents",
    "Billing / ProfitTools",
    "Port Houston Integration",
    "Dashboard",
    "Email Imports",
    "Validation",
    "Master Data",
]

LOAD_DATA_SECTIONS = {
    "Dashboard",
    "Orders/Load Management",
    "Dispatch Board",
    "Calendar View",
    "Documents",
    "Billing / ProfitTools",
    "Port Houston Integration",
    "Validation",
}


def load_css() -> None:
    theme = Path("theme.css")
    if theme.exists():
        st.markdown(theme.read_text(encoding="utf-8"), unsafe_allow_html=True)

    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.2rem;}
        .metric-card {
            border: 1px solid #e5e7eb;
            border-radius: 16px;
            padding: 16px;
            background: white;
            box-shadow: 0 4px 16px rgba(15, 23, 42, 0.06);
        }
        .load-card {
            border: 1px solid #dbeafe;
            border-radius: 14px;
            padding: 12px;
            margin-bottom: 10px;
            background: #ffffff;
            box-shadow: 0 2px 10px rgba(15, 23, 42, 0.05);
        }
        .load-card-title {
            font-weight: 700;
            font-size: 0.95rem;
            color: #0f172a;
        }
        .load-card-small {
            color: #475569;
            font-size: 0.82rem;
        }
        .status-pill {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 999px;
            font-size: 0.75rem;
            background: #e0f2fe;
            color: #075985;
            font-weight: 700;
        }
        .danger-pill {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 999px;
            font-size: 0.75rem;
            background: #fee2e2;
            color: #991b1b;
            font-weight: 700;
        }
        .success-pill {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 999px;
            font-size: 0.75rem;
            background: #dcfce7;
            color: #166534;
            font-weight: 700;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def image_to_base64(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return base64.b64encode(file_path.read_bytes()).decode("utf-8")


def normalize_date(value):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


@st.cache_data(ttl=45)
def load_dispatch_data() -> pd.DataFrame:
    return DispatchDatabaseClient().rows_to_dataframe()


@st.cache_data(show_spinner=False, ttl=45)
def load_tms_data() -> pd.DataFrame:
    return merge_ext(clean_df(load_dispatch_data()))


def refresh_data() -> None:
    st.cache_data.clear()


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()

    for col in SUMMARY_COLUMNS + [
        "Reference Number",
        "Address",
        "Billing Notes",
        "Ready for ProfitTools",
        "Rate",
        "Customer Email",
        "Contact Email",
        "Public Notes",
        "current_location",
        "eta",
        "live_load_status",
        "live_unload_status",
        "last_driver_update",
        "pickup_appointment",
        "delivery_appointment",
        "terminal",
        "empty_return_location",
    ]:
        if col not in df.columns:
            df[col] = ""

    df["TYPE"] = df["TYPE"].astype(str).str.strip()
    df["Status"] = df["Status"].astype(str).str.strip()
    df["Booking Number"] = df["Booking Number"].astype(str).str.strip()

    return df


@st.cache_data(show_spinner=False, ttl=45)
def get_ext_df() -> pd.DataFrame:
    """Read additional PortPro-style fields directly from loads."""
    try:
        return read_df(
            """
            select
                id as _row_id,
                steamship_line,
                vessel_name,
                terminal,
                pickup_appointment,
                delivery_appointment,
                empty_return_location,
                empty_return_date,
                chassis_provider,
                pickup_reference,
                delivery_reference,
                invoice_status,
                driver_pay_status,
                customer_rate,
                carrier_pay,
                accessorials,
                margin,
                current_location,
                eta,
                live_load_status,
                live_unload_status,
                last_driver_update
            from loads
            """
        )
    except Exception:
        return pd.DataFrame()


def merge_ext(df: pd.DataFrame) -> pd.DataFrame:
    ext = get_ext_df()
    if ext.empty or "_row_id" not in df.columns:
        df = df.copy()
        for column in EXT_LOAD_COLUMNS:
            if column not in df.columns:
                df[column] = ""
        return df

    ext_columns = [column for column in ext.columns if column != "_row_id"]
    base = df.drop(columns=[column for column in ext_columns if column in df.columns], errors="ignore")
    merged = base.merge(ext, on="_row_id", how="left")

    for column in EXT_LOAD_COLUMNS:
        if column not in merged.columns:
            merged[column] = ""
        else:
            merged[column] = merged[column].fillna("")

    return merged


def filter_loads(df: pd.DataFrame, search_text: str = "", status_filter: str = "All", type_filter: str = "All") -> pd.DataFrame:
    filtered = df.copy()

    if status_filter != "All":
        filtered = filtered[filtered["Status"].astype(str).eq(status_filter)]

    if type_filter != "All":
        filtered = filtered[filtered["TYPE"].astype(str).eq(type_filter)]

    if search_text:
        needle = search_text.lower()
        searchable_cols = [column for column in LOAD_SEARCH_COLUMNS if column in filtered.columns]
        if searchable_cols:
            searchable = filtered[searchable_cols].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
            filtered = filtered[searchable.str.contains(needle, regex=False, na=False)]

    return filtered


def show_header() -> None:
    banner_b64 = image_to_base64("assets/header_banner.png")
    if banner_b64:
        st.markdown(
            f"""
            <div class="banner-wrapper">
                <img class="header-banner" src="data:image/png;base64,{banner_b64}" />
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.title("CaliTrans TMS")
    st.caption("Drayage dispatch, container tracking, billing readiness, and operations dashboard")



STATUS_COLORS = {
    "New": "#f8fafc",
    "Hold/Need Info": "#fecaca",
    "Awaiting Appointment": "#fdba74",
    "Ready to Dispatch": "#bbf7d0",
    "Assigned": "#dcfce7",
    "En Route to Pickup": "#bef264",
    "At Pickup": "#fde047",
    "Loaded": "#a5b4fc",
    "En Route To Delivery": "#5eead4",
    "Delivered": "#93c5fd",
    "Returning Empty": "#e0f2fe",
    "POD Received": "#60a5fa",
    "Ready for ProfitTools": "#4ade80",
    "Exported to ProfitTools": "#c4b5fd",
    "Invoiced": "#f0abfc",
    "Closed": "#d1d5db",
    "Cancelled": "#f87171",
}

STATUS_MEANINGS = {
    "New": "New confirmed load, not dispatched yet",
    "Hold/Need Info": "Issue or missing information; dispatcher action required",
    "Awaiting Appointment": "Booking confirmed but waiting for pickup or delivery appointment",
    "Ready to Dispatch": "Ready/green light to assign driver and truck",
    "Assigned": "Driver and truck assigned",
    "En Route to Pickup": "Driver moving toward pickup or terminal",
    "At Pickup": "Driver checked in or waiting at pickup",
    "Loaded": "Container or freight loaded",
    "En Route To Delivery": "Driver moving toward delivery",
    "Delivered": "Delivered; POD or next billing step needed",
    "Returning Empty": "Driver returning empty container/chassis",
    "POD Received": "Proof of delivery received",
    "Ready for ProfitTools": "Ready for billing/export",
    "Exported to ProfitTools": "Sent to ProfitTools",
    "Invoiced": "Invoice sent",
    "Closed": "Load completed",
    "Cancelled": "Load cancelled",
}


STATUS_LEGEND_GROUPS = {
    "Ready / Active": ["Ready to Dispatch", "Assigned", "En Route to Pickup", "En Route To Delivery", "Ready for ProfitTools"],
    "Pickup / Loading": ["At Pickup", "Loaded"],
    "Delivered / Return": ["Delivered", "Returning Empty", "POD Received"],
    "Issues / Stops": ["Hold/Need Info", "Awaiting Appointment", "Cancelled"],
    "Billing / Closed": ["Exported to ProfitTools", "Invoiced", "Closed", "New"],
}



def _get_status_color(status: str) -> str:
    return STATUS_COLORS.get(str(status or "").strip(), "#f8fafc")


def _get_status_border_color(status: str) -> str:
    border_colors = {
        "New": "#94a3b8",
        "Hold/Need Info": "#dc2626",
        "Awaiting Appointment": "#ea580c",
        "Ready to Dispatch": "#16a34a",
        "Assigned": "#22c55e",
        "En Route to Pickup": "#65a30d",
        "At Pickup": "#ca8a04",
        "Loaded": "#4f46e5",
        "En Route To Delivery": "#0d9488",
        "Delivered": "#2563eb",
        "Returning Empty": "#0284c7",
        "POD Received": "#1d4ed8",
        "Ready for ProfitTools": "#15803d",
        "Exported to ProfitTools": "#7c3aed",
        "Invoiced": "#c026d3",
        "Closed": "#64748b",
        "Cancelled": "#b91c1c",
    }
    return border_colors.get(str(status or "").strip(), "#94a3b8")


def _status_row_style(row):
    status = str(row.get("Status", ""))
    color = STATUS_COLORS.get(status, "#ffffff")
    return [f"background-color: {color}"] * len(row)


def _render_status_legend() -> None:
    st.markdown("### Status Legend")
    st.caption("Dashboard row colors")

    for group_name, statuses in STATUS_LEGEND_GROUPS.items():
        st.markdown(f"**{group_name}**")
        for status in statuses:
            color = STATUS_COLORS.get(status, "#ffffff")
            meaning = STATUS_MEANINGS.get(status, "")
            st.markdown(
                f"""
                <div style="display:flex; align-items:flex-start; gap:8px; margin:6px 0 8px 0;">
                    <span style="min-width:18px; height:18px; border-radius:5px; background:{color}; border:1px solid #64748b; display:inline-block;"></span>
                    <span style="font-size:12px; line-height:1.2;">
                        <b>{status}</b><br>
                        <span style="color:#64748b;">{meaning}</span>
                    </span>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("<hr style='margin:8px 0;'>", unsafe_allow_html=True)

def show_kpis(df: pd.DataFrame) -> None:
    today = pd.Timestamp(date.today())

    delivery_dates = pd.to_datetime(df.get("Delivery Need Date", ""), errors="coerce")
    lfd_dates = pd.to_datetime(df.get("LFD", ""), errors="coerce")

    open_loads = df[~df["Status"].isin(["Closed", "Cancelled", "Invoiced"])]
    late = df[(delivery_dates.notna()) & (delivery_dates < today) & (~df["Status"].isin(["Delivered", "Closed", "Cancelled"]))]
    lfd_risk = df[(lfd_dates.notna()) & (lfd_dates <= today + pd.Timedelta(days=1)) & (~df["Status"].isin(["Delivered", "Closed", "Cancelled"]))]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Open Loads", len(open_loads))
    c2.metric("Ready to Dispatch", int(df["Status"].eq("Ready to Dispatch").sum()))
    c3.metric("On Driver", int(df["Status"].isin(["Assigned", "En Route to Pickup", "At Pickup", "Loaded", "En Route To Delivery"]).sum()))
    c4.metric("LFD Risk", len(lfd_risk))
    c5.metric("Late Deliveries", len(late))

def render_load_card(row) -> None:
    booking = str(row.get("Booking Number", "") or "")
    row_id = int(row.get("_row_id", 0))
    status = str(row.get("Status", "") or "")
    container = str(row.get("Container Number", "") or "-")
    customer = str(row.get("Customer", "") or "-")
    driver = _clean_display_value(row.get("Driver Name", ""), "Unassigned")
    need_date = str(row.get("Delivery Need Date", "") or "-")

    status_color = _get_status_color(status)
    border_color = _get_status_border_color(status)

    st.markdown(
        f"""
        <div style="
            background:{status_color};
            border-left:5px solid {border_color};
            border-radius:8px;
            padding:6px 7px;
            margin-bottom:5px;
            font-size:10px;
            line-height:1.15;
        ">
            <b>{booking}</b><br>
            {container}<br>
            <span>{customer}</span><br>
            <span>{driver}</span> · <span>{need_date}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("Open", key=f"open_load_{row_id}", use_container_width=True):
        st.session_state["selected_dispatch_load_id"] = row_id
        st.session_state["show_load_workspace_dialog"] = True
        st.rerun()

def render_dashboard(df: pd.DataFrame) -> None:
    st.subheader("Operations Dashboard")
    st.caption("Today KPI, Tomorrow KPI, driver utilization, LFD risk, and exceptions.")

    work_df = df.copy()

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

    ready_to_dispatch = int(open_df["Status"].eq("Ready to Dispatch").sum())
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

    exceptions = {
        "Unassigned Ready Loads": int(
            (
                open_df["Status"].eq("Ready to Dispatch")
                & open_df["Driver Name"].astype(str).str.strip().isin(["", "None", "nan", "Unassigned"])
            ).sum()
        ),
        "Missing Container Number": int(
            open_df["Container Number"].astype(str).str.strip().isin(["", "None", "nan"]).sum()
        ),
        "Missing Driver": int(
            open_df["Driver Name"].astype(str).str.strip().isin(["", "None", "nan", "Unassigned"]).sum()
        ),
        "Hold / Need Info": int(open_df["Status"].eq("Hold/Need Info").sum()),
        "Delivered / POD Needed": int(open_df["Status"].eq("Delivered").sum()),
        "Ready for Billing": ready_for_billing,
    }

    exception_df = pd.DataFrame(
        [{"Issue": issue, "Count": count} for issue, count in exceptions.items()]
    )

    st.dataframe(exception_df, use_container_width=True, hide_index=True)

BOOKING_VERIFICATION_REQUIRED_FIELDS = [
    "TYPE",
    "Booking Number",
    "Customer",
    "Container Number",
    "Port",
    "Warehouse",
    "Delivery Need Date",
    "LFD",
]


def _is_blank_value(value) -> bool:
    value_str = str(value or "").strip()
    return value_str == "" or value_str.lower() in {"nan", "none", "nat", "-", "null"}


def _booking_readiness(row) -> tuple[int, list[str]]:
    missing = []

    for field in BOOKING_VERIFICATION_REQUIRED_FIELDS:
        if field not in row.index or _is_blank_value(row.get(field, "")):
            missing.append(field)

    completed = len(BOOKING_VERIFICATION_REQUIRED_FIELDS) - len(missing)
    score = int(round((completed / len(BOOKING_VERIFICATION_REQUIRED_FIELDS)) * 100))

    return score, missing


def _add_booking_verification_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    scores = []
    missing_values = []
    readiness_labels = []

    for _, row in df.iterrows():
        score, missing = _booking_readiness(row)
        scores.append(score)
        missing_values.append(", ".join(missing) if missing else "")

        if score == 100:
            readiness_labels.append("Ready")
        elif score >= 75:
            readiness_labels.append("Needs Minor Info")
        elif score >= 50:
            readiness_labels.append("Needs Review")
        else:
            readiness_labels.append("Missing Critical Info")

    df["Readiness %"] = scores
    df["Missing Fields"] = missing_values
    df["Verification Result"] = readiness_labels

    return df


def _render_booking_verification_table(table_df: pd.DataFrame, title: str) -> None:
    st.markdown(f"#### {title}")
    st.caption(f"{len(table_df)} booking(s)")

    if table_df.empty:
        st.success("No bookings in this queue.")
        return

    columns = [
        "_row_id",
        "TYPE",
        "Booking Number",
        "Customer",
        "Container Number",
        "Port",
        "Warehouse",
        "Delivery Need Date",
        "LFD",
        "Status",
        "Readiness %",
        "Verification Result",
        "Missing Fields",
        "Dispatcher Notes",
    ]

    display_cols = [c for c in columns if c in table_df.columns]
    styled = (
        table_df.sort_values(["Readiness %", "_row_id"], ascending=[True, False])[display_cols]
        .style
        .apply(_status_row_style, axis=1)
    )

    st.dataframe(styled, use_container_width=True, hide_index=True)


def _render_booking_verification_actions(verification_df: pd.DataFrame) -> None:
    if verification_df.empty:
        return

    st.divider()
    st.markdown("### Booking Final Check / Move to Dispatch")
    st.caption("Use this section as the last office check before the booking becomes Ready to Dispatch.")

    labels = [
        f"{row['Booking Number']} | {row.get('Customer', '')} | {row.get('Readiness %', 0)}% ready | row {int(row['_row_id'])}"
        for _, row in verification_df.sort_values("_row_id", ascending=False).iterrows()
    ]

    selected = st.selectbox("Select booking to review", labels, key="booking_verification_selected")
    selected_row_id = int(selected.split("row ")[-1])
    selected_df = verification_df[verification_df["_row_id"].astype(int).eq(selected_row_id)]

    if selected_df.empty:
        st.warning("Selected booking was not found.")
        return

    selected_load = selected_df.iloc[0]
    readiness_score = int(selected_load.get("Readiness %", 0))
    missing_fields = str(selected_load.get("Missing Fields", "") or "")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Booking", str(selected_load.get("Booking Number", "") or "-"))
    c2.metric("Customer", str(selected_load.get("Customer", "") or "-"))
    c3.metric("Readiness", f"{readiness_score}%")
    c4.metric("Status", str(selected_load.get("Status", "") or "-"))

    if missing_fields:
        st.warning(f"Missing fields: {missing_fields}")
    else:
        st.success("This booking has all required dispatch-readiness fields.")

    with st.expander("Review selected booking details", expanded=True):
        details = {
            "Type": selected_load.get("TYPE", ""),
            "Booking Number": selected_load.get("Booking Number", ""),
            "Load ID": selected_load.get("Load ID", ""),
            "Customer": selected_load.get("Customer", ""),
            "Container Number": selected_load.get("Container Number", ""),
            "Port / Pickup": selected_load.get("Port", ""),
            "Warehouse / Delivery": selected_load.get("Warehouse", ""),
            "Delivery Need Date": selected_load.get("Delivery Need Date", ""),
            "LFD": selected_load.get("LFD", ""),
            "Status": selected_load.get("Status", ""),
            "Dispatcher Notes": selected_load.get("Dispatcher Notes", ""),
        }
        st.dataframe(
            pd.DataFrame([{"Field": k, "Value": v} for k, v in details.items()]),
            use_container_width=True,
            hide_index=True,
        )

    action_note = st.text_area(
        "Verification Note",
        value=str(selected_load.get("Dispatcher Notes", "") or ""),
        height=100,
        key=f"booking_verification_note_{selected_row_id}",
    )

    a1, a2, a3, a4 = st.columns(4)

    with a1:
        if st.button("Mark Missing Info", key=f"mark_missing_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Hold/Need Info",
                    "Dispatcher Notes": action_note or f"Missing fields: {missing_fields}",
                },
            )
            refresh_data()
            st.success("Booking marked Hold/Need Info.")
            st.rerun()

    with a2:
        if st.button("Awaiting Appointment", key=f"mark_appt_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Awaiting Appointment",
                    "Dispatcher Notes": action_note or "Waiting for appointment confirmation.",
                },
            )
            refresh_data()
            st.success("Booking marked Awaiting Appointment.")
            st.rerun()

    with a3:
        if st.button("Save Verification Note", key=f"save_verify_note_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Dispatcher Notes": action_note},
            )
            refresh_data()
            st.success("Verification note saved.")
            st.rerun()

    with a4:
        disabled = readiness_score < 100
        if st.button(
            "Move to Dispatch",
            key=f"move_to_dispatch_{selected_row_id}",
            use_container_width=True,
            disabled=disabled,
            help="Requires 100% readiness. Sets status to Ready to Dispatch.",
        ):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Ready to Dispatch",
                    "Dispatcher Notes": action_note or "Booking verified and moved to dispatch.",
                },
            )
            refresh_data()
            st.success("Booking moved to Dispatch Board as Ready to Dispatch.")
            st.rerun()

    if readiness_score < 100:
        st.info("Move to Dispatch is disabled until all required booking fields are complete.")

def _parse_date_or_none(value):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _safe_str(value) -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return value_str

import re


REQUEST_TYPES = [
    "New Booking",
    "Booking Update",
    "Appointment Update",
    "Quote Request",
    "Missing Information",
    "Cancellation",
    "Customer Request",
    "POD Request",
    "Other",
]

INBOX_TERMINAL_REVIEW_STATUSES = [
    "Order Created",
    "Attached",
    "Quote Created",
    "Order Cancelled",
    "Closed",
]

DEFAULT_OPERATIONS_QUEUE_ORDER = [
    "All",
    "Needs Details",
    "Customer Requests",
    "New Bookings",
    "Booking Updates",
    "Appointments",
    "Quote Requests",
    "Missing Info",
    "POD Requests",
    "Cancellations",
    "Waiting",
    "Needs Review",
]

def _normalize_reference_token(value: str) -> str:
    return re.sub(r"\s+", "-", str(value or "").strip(" :#-")).upper()


def _extract_reference_tokens(text: str) -> dict:
    text = str(text or "")

    booking_match = (
        re.search(r"\b(?:booking|bkg|bk)\s*(?:number|no\.?|#)?\s*[:#-]\s*([A-Z0-9][A-Z0-9-]{4,})\b", text, re.I)
        or re.search(r"\b(?:IMP|EXP|IML|EXL)[-\s]?[A-Z0-9-]{4,}\b", text, re.I)
        or re.search(r"\b(?:MAEU|ONEY|COSU|ZIMU|HLCU|MSCU|OOLU|CMDU|EGLV|YMLU|HMMU|SUDU)[A-Z0-9-]{4,}\b", text, re.I)
    )
    container_match = re.search(r"\b[A-Z]{4}\d{6,7}\b", text, re.I)
    ref_match = re.search(
        r"\b(?:ref|reference|po)\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{3,})\b",
        text,
        re.I,
    )

    booking_value = ""
    if booking_match:
        booking_value = booking_match.group(1) if booking_match.lastindex else booking_match.group(0)

    ref_value = ""
    if ref_match:
        ref_value = ref_match.group(1) if ref_match.lastindex else ref_match.group(0)

    return {
        "booking_number": _normalize_reference_token(booking_value) if booking_value else "",
        "container_number": container_match.group(0).upper() if container_match else "",
        "reference_number": _normalize_reference_token(ref_value) if ref_value else "",
    }


APPOINTMENT_INTENT_TERMS = [
    "can we load",
    "load at",
    "load on",
    "earlier",
    "later",
    "appointment",
    "appt",
    "scheduled",
    "confirmed time",
    "delivery appointment",
    "pickup appointment",
    "change appointment",
    "reschedule",
    "move appointment",
    "change time",
    "change date",
    "cita",
    "cita de entrega",
    "cita de recogida",
    "programar",
    "reprogramar",
    "cambiar hora",
    "cambiar fecha",
]

QUOTE_INTENT_TERMS = [
    "quote request",
    "rate request",
    "please quote",
    "need a quote",
    "need rate",
    "send rate",
    "pricing request",
    "price this load",
    "can you quote",
    "quote this",
    "rate this",
    "cotizacion",
    "cotización",
    "tarifa",
    "precio",
    "solicitud de tarifa",
    "pueden cotizar",
    "necesito tarifa",
]

UPDATE_INTENT_TERMS = [
    "any update",
    "status update",
    "please update",
    "can you update",
    "where are we",
    "where is",
    "eta",
    "update",
    "revision",
    "revised",
    "changed",
    "new address",
    "correction",
    "container released",
    "released",
    "last free day",
    "lfd",
    "actualizacion",
    "actualización",
    "estado",
    "estatus",
    "alguna novedad",
    "donde esta",
    "dónde está",
    "contenedor liberado",
    "liberado",
    "ultimo dia libre",
    "último día libre",
]

NEW_ORDER_INTENT_TERMS = [
    "new booking",
    "new load",
    "load order",
    "delivery order",
    "work order",
    "tender",
    "tendered",
    "bill of lading",
    "bol",
    "attached order",
    "attached load",
    "please book",
    "nuevo booking",
    "nueva carga",
    "orden de carga",
    "orden adjunta",
    "favor reservar",
]

MISSING_INFO_TERMS = [
    "missing info",
    "missing information",
    "need info",
    "need information",
    "incomplete",
    "please provide",
    "falta informacion",
    "falta información",
    "informacion faltante",
    "información faltante",
    "incompleto",
    "por favor envie",
    "por favor envíe",
]

CANCELLATION_TERMS = ["cancel", "cancelled", "canceled", "cancelar", "cancelado", "cancelacion", "cancelación"]
POD_TERMS = ["pod", "proof of delivery", "prueba de entrega", "comprobante de entrega"]

REPLY_LANGUAGE_OPTIONS = ["Auto", "English", "Spanish", "Bilingual"]

SPANISH_LANGUAGE_TERMS = [
    "actualizacion",
    "actualización",
    "carga",
    "contenedor",
    "cotizacion",
    "cotización",
    "entrega",
    "estado",
    "favor",
    "gracias",
    "hola",
    "informacion",
    "información",
    "necesito",
    "pod",
    "podria",
    "podría",
    "puede",
    "pueden",
    "recogida",
    "referencia",
    "reserva",
    "solicito",
]

ENGLISH_LANGUAGE_TERMS = [
    "appointment",
    "booking",
    "container",
    "delivery",
    "hello",
    "information",
    "please",
    "quote",
    "rate",
    "reference",
    "request",
    "status",
    "thank",
    "update",
]


def _contains_any(text: str, terms: list[str]) -> bool:
    lowered = str(text or "").lower()
    return any(term in lowered for term in terms)


def _detect_customer_language(subject: str, body: str) -> str:
    text = f"{subject or ''} {body or ''}".lower()
    spanish_score = sum(1 for term in SPANISH_LANGUAGE_TERMS if term in text)
    english_score = sum(1 for term in ENGLISH_LANGUAGE_TERMS if term in text)

    if spanish_score >= 2 and english_score >= 2:
        return "Bilingual"
    if spanish_score > english_score:
        return "Spanish"
    return "English"


def _resolve_reply_language(reply_language: str, subject: str, body: str) -> str:
    reply_language = str(reply_language or "Auto").strip()
    if reply_language == "Auto":
        return _detect_customer_language(subject, body)
    if reply_language in REPLY_LANGUAGE_OPTIONS:
        return reply_language
    return "English"


def _coerce_parsed_for_classification(subject: str, body: str, parsed: dict | None = None) -> dict:
    if isinstance(parsed, dict):
        return parsed
    try:
        return parse_email_text(subject, body)
    except Exception:
        return {}


def _has_reference_details(tokens: dict, parsed: dict) -> bool:
    parsed_reference_fields = [
        "Booking Number",
        "Container Number",
        "Reference Number",
    ]
    return any(_safe_str(tokens.get(key, "")) for key in ["booking_number", "container_number", "reference_number"]) or any(
        _safe_str(parsed.get(field, "")) for field in parsed_reference_fields
    )


def _has_quote_details(text: str, parsed: dict, tokens: dict) -> bool:
    detail_score = 0

    if _safe_str(parsed.get("Port", "")):
        detail_score += 1
    if _safe_str(parsed.get("Warehouse", "")) or _safe_str(parsed.get("Address", "")):
        detail_score += 1
    if _safe_str(parsed.get("Size", "")) or re.search(r"\b(?:20|40|45)\s*(?:ft|hc|hq|dv|std)?\b", text, re.I):
        detail_score += 1
    if _safe_str(parsed.get("Delivery Need Date", "")) or re.search(
        r"\b(?:today|tomorrow|asap|next week|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b",
        text,
        re.I,
    ):
        detail_score += 1
    if re.search(r"\bfrom\s+.{2,80}\s+\bto\s+.{2,80}", text, re.I):
        detail_score += 2
    if _has_reference_details(tokens, parsed):
        detail_score += 1

    return detail_score >= 2


def _has_new_order_details(text: str, parsed: dict, tokens: dict) -> bool:
    detail_score = 0
    for field in ["Booking Number", "Customer", "Container Number", "Port", "Warehouse", "Delivery Need Date"]:
        if _safe_str(parsed.get(field, "")):
            detail_score += 1
    if _has_reference_details(tokens, parsed):
        detail_score += 1
    if _contains_any(text, NEW_ORDER_INTENT_TERMS):
        detail_score += 1

    return detail_score >= 3


def classify_customer_request(subject: str, body: str, parsed: dict | None = None) -> str:
    text = f"{subject or ''} {body or ''}"
    parsed = _coerce_parsed_for_classification(subject, body, parsed)
    tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    has_reference = _has_reference_details(tokens, parsed)

    if _contains_any(text, MISSING_INFO_TERMS):
        return "Missing Information"

    if _contains_any(text, CANCELLATION_TERMS):
        return "Cancellation" if has_reference else "Customer Request"

    if _contains_any(text, POD_TERMS):
        return "POD Request" if has_reference else "Customer Request"

    if _contains_any(text, APPOINTMENT_INTENT_TERMS):
        return "Appointment Update" if has_reference else "Customer Request"

    if _contains_any(text, QUOTE_INTENT_TERMS):
        return "Quote Request" if _has_quote_details(text, parsed, tokens) else "Customer Request"

    if _contains_any(text, UPDATE_INTENT_TERMS):
        return "Booking Update" if has_reference else "Customer Request"

    if _contains_any(text, NEW_ORDER_INTENT_TERMS) and _has_new_order_details(text, parsed, tokens):
        return "New Booking"

    return "Customer Request"


def find_matching_load(tokens: dict) -> tuple[int | None, int]:
    booking = tokens.get("booking_number", "")
    container = tokens.get("container_number", "")
    reference = tokens.get("reference_number", "")

    conditions = []
    params = {}

    if booking:
        conditions.append("lower(booking_number) = lower(:booking)")
        params["booking"] = booking

    if container:
        conditions.append("lower(container_number) = lower(:container)")
        params["container"] = container

    if reference:
        conditions.append("lower(reference_number) = lower(:reference)")
        params["reference"] = reference

    if not conditions:
        return None, 0

    try:
        match_df = read_df(
            f"""
            select id
            from loads
            where {" or ".join(conditions)}
            order by updated_at desc
            limit 1
            """,
            params,
        )

        if match_df.empty:
            return None, 35

        if booking or container:
            return int(match_df.iloc[0]["id"]), 95

        return int(match_df.iloc[0]["id"]), 75

    except Exception:
        return None, 0


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


def save_load_communication(load_id, intake_id, conversation_key, request_type, subject, sender, body, direction: str = "inbound") -> None:
    execute(
        """
        insert into load_communications (
            load_id,
            intake_id,
            conversation_key,
            communication_type,
            direction,
            subject,
            sender,
            message_body
        )
        values (
            :load_id,
            :intake_id,
            :conversation_key,
            :communication_type,
            :direction,
            :subject,
            :sender,
            :message_body
        )
        """,
        {
            "load_id": load_id,
            "intake_id": intake_id,
            "conversation_key": conversation_key,
            "communication_type": request_type,
            "direction": direction,
            "subject": subject,
            "sender": sender,
            "message_body": body,
        },
    )


def create_quote_request_from_intake(intake_id: int, parsed: dict, notes: str = "") -> None:
    execute(
        """
        insert into quote_requests (
            intake_id,
            customer,
            origin,
            destination,
            container_type,
            requested_date,
            notes,
            quote_status
        )
        values (
            :intake_id,
            :customer,
            :origin,
            :destination,
            :container_type,
            :requested_date,
            :notes,
            'Requested'
        )
        """,
        {
            "intake_id": intake_id,
            "customer": parsed.get("Customer"),
            "origin": parsed.get("Port"),
            "destination": parsed.get("Warehouse"),
            "container_type": parsed.get("Size"),
            "requested_date": parsed.get("Delivery Need Date") or None,
            "notes": notes,
        },
    )


def _coerce_json_dict(value) -> dict:
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}

    return {}


def _json_dump(data: dict) -> str:
    return json.dumps(data or {}, default=str)


OPERATIONS_PDF_ATTACHMENTS_KEY = "_operations_pdf_attachments"
OPERATIONS_ORDER_FIELDS = [
    "TYPE",
    "Customer",
    "Booking Number",
    "Reference Number",
    "Container Number",
    "Size",
    "Port",
    "Warehouse",
    "Address",
    "Delivery Need Date",
    "Document Cutoff",
    "LFD",
    "Dispatcher Notes",
]


def _safe_storage_name(value: str, fallback: str = "file") -> str:
    name = Path(str(value or fallback)).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or fallback


def _operations_pdf_storage_dir() -> Path:
    storage_dir = Path(DOCUMENT_STORAGE_DIR) / "operations_inbox"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _parse_operations_pdf_bytes(content: bytes, filename: str) -> tuple[str, dict]:
    pdf_file = BytesIO(content or b"")
    pdf_file.name = filename or "attachment.pdf"
    pdf_text = extract_text_from_pdf(pdf_file)
    pdf_parsed = parse_order_text(pdf_text) if pdf_text else {}
    return pdf_text, pdf_parsed


def _field_count(parsed: dict) -> int:
    return sum(1 for field in OPERATIONS_ORDER_FIELDS if _safe_str(parsed.get(field, "")))


def _save_operations_pdf_attachment(
    *,
    content: bytes,
    filename: str,
    message_id: str,
    attachment_index: int,
) -> dict:
    safe_message = _safe_storage_name(message_id, "operations_email")[:90]
    safe_filename = _safe_storage_name(filename, f"attachment_{attachment_index}.pdf")
    stored_path = _operations_pdf_storage_dir() / f"{safe_message}_{attachment_index}_{safe_filename}"
    stored_path.write_bytes(content or b"")

    try:
        pdf_text, pdf_parsed = _parse_operations_pdf_bytes(content or b"", safe_filename)
        parse_error = ""
    except Exception as exc:
        pdf_text = ""
        pdf_parsed = {}
        parse_error = str(exc)

    return {
        "filename": safe_filename,
        "file_path": str(stored_path),
        "content_type": "application/pdf",
        "parsed_data": pdf_parsed,
        "fields_found": _field_count(pdf_parsed),
        "text_preview": pdf_text[:1800],
        "parse_error": parse_error,
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


def _extract_operations_pdf_attachments(parsed: dict, record: dict | pd.Series | None = None) -> list[dict]:
    attachments = parsed.get(OPERATIONS_PDF_ATTACHMENTS_KEY, [])
    if not isinstance(attachments, list):
        attachments = []

    normalized = [item for item in attachments if isinstance(item, dict)]

    if record is not None:
        filename = _safe_str(record.get("filename", "") if hasattr(record, "get") else "")
        file_path = _safe_str(record.get("file_path", "") if hasattr(record, "get") else "")
        if filename and file_path and not any(_safe_str(item.get("file_path", "")) == file_path for item in normalized):
            normalized.append(
                {
                    "filename": filename,
                    "file_path": file_path,
                    "content_type": "application/pdf",
                    "parsed_data": {},
                    "fields_found": 0,
                    "text_preview": "",
                    "parse_error": "",
                }
            )

    return normalized


def _merge_operations_order_fields(body_parsed: dict, pdf_parsed: dict) -> tuple[dict, list[dict], list[str]]:
    final_data = {}
    rows = []
    conflicts = []

    for field in OPERATIONS_ORDER_FIELDS:
        body_value = _safe_str(body_parsed.get(field, ""))
        pdf_value = _safe_str(pdf_parsed.get(field, ""))
        final_value = pdf_value or body_value
        final_data[field] = final_value

        if body_value and pdf_value and body_value.lower() != pdf_value.lower():
            status = "Review mismatch"
            conflicts.append(field)
        elif final_value:
            status = "Found"
        else:
            status = "Blank"

        rows.append(
            {
                "Field": field,
                "Email Body": body_value,
                "PDF": pdf_value,
                "Final Value": final_value,
                "Status": status,
            }
        )

    return final_data, rows, conflicts


def _store_operations_parsed_data(intake_id: int, parsed_data: dict, action_required: str | None = None) -> None:
    execute(
        """
        update order_intake
        set parsed_data = cast(:parsed_data as jsonb),
            action_required = coalesce(:action_required, action_required)
        where id = :intake_id
        """,
        {
            "intake_id": int(intake_id),
            "parsed_data": _json_dump(parsed_data),
            "action_required": action_required,
        },
    )


@st.cache_data(show_spinner=False, ttl=900)
def _read_operations_pdf_file(file_path: str, modified_ns: int) -> bytes:
    return Path(file_path).read_bytes()


def _read_operations_pdf_bytes(file_path: str) -> bytes:
    path = Path(file_path)
    return _read_operations_pdf_file(str(path), path.stat().st_mtime_ns)


@st.cache_data(show_spinner=False, ttl=900)
def _parse_operations_pdf_file(file_path: str, filename: str, modified_ns: int) -> tuple[str, dict]:
    content = Path(file_path).read_bytes()
    return _parse_operations_pdf_bytes(content, filename)


def _parse_saved_operations_pdf(file_path: str, filename: str) -> tuple[str, dict]:
    path = Path(file_path)
    return _parse_operations_pdf_file(str(path), filename, path.stat().st_mtime_ns)


def _extract_email_address(value: str) -> str:
    parsed = parseaddr(str(value or ""))
    return parsed[1] or str(value or "").strip()


def _email_received_lookup_key(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return _safe_str(value)
    return parsed.isoformat()


def _inbox_review_where_clause() -> str:
    terminal = ", ".join([f"'{status}'" for status in INBOX_TERMINAL_REVIEW_STATUSES])
    return f"where coalesce(review_status, 'Open') not in ({terminal})"


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_inbox_df(where_clause: str) -> pd.DataFrame:
    return read_df(
        f"""
        select
            id,
            created_at,
            source_received_at,
            source,
            source_subject,
            source_sender,
            filename,
            file_path,
            parsed_data,
            left(coalesce(raw_text, ''), 1200) as raw_text_preview,
            case
                when jsonb_typeof(parsed_data -> :pdf_attachments_key) = 'array'
                    then jsonb_array_length(parsed_data -> :pdf_attachments_key)
                when filename is not null and filename <> '' then 1
                else 0
            end as pdf_count,
            intake_status,
            request_type,
            conversation_key,
            matched_load_id,
            confidence_score,
            action_required,
            review_status
        from order_intake
        {where_clause}
        order by created_at desc
        """,
        {"pdf_attachments_key": OPERATIONS_PDF_ATTACHMENTS_KEY},
    )


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_inbox_record(intake_id: int) -> pd.DataFrame:
    return read_df(
        """
        select
            id,
            created_at,
            source_received_at,
            source,
            source_subject,
            source_sender,
            filename,
            file_path,
            parsed_data,
            raw_text,
            intake_status,
            request_type,
            conversation_key,
            matched_load_id,
            confidence_score,
            action_required,
            review_status
        from order_intake
        where id = :intake_id
        limit 1
        """,
        {"intake_id": int(intake_id)},
    )


def _load_operations_inbox_record_set(where_clause: str) -> pd.DataFrame:
    return read_df(
        f"""
        select
            id,
            source_subject,
            parsed_data,
            raw_text,
            request_type,
            conversation_key,
            matched_load_id,
            confidence_score,
            action_required
        from order_intake
        {where_clause}
        order by created_at desc
        """
    )


def _operations_items_needing_smart_group_update(inbox_df: pd.DataFrame) -> pd.Series:
    current_type = inbox_df["request_type"].fillna("").astype(str).str.strip()
    confidence = pd.to_numeric(inbox_df["confidence_score"], errors="coerce").fillna(0)
    has_match = inbox_df["matched_load_id"].notna() & ~inbox_df["matched_load_id"].astype(str).isin(["", "nan", "None"])

    action_type_needs_reference = current_type.isin([
        "New Booking",
        "Booking Update",
        "Appointment Update",
        "Quote Request",
        "Cancellation",
        "POD Request",
    ]) & ~has_match & confidence.lt(70)

    return current_type.isin(["", "Needs Classification", "Other"]) | action_type_needs_reference


def _find_existing_operations_email_record(
    message_id: str,
    subject: str,
    sender: str,
    received_at: str | None = None,
) -> dict | None:
    if message_id:
        existing = read_df(
            """
            select id, parsed_data, filename, file_path, raw_text, action_required
            from order_intake
            where source_message_id = :message_id
            limit 1
            """,
            {"message_id": message_id},
        )
        if not existing.empty:
            return existing.iloc[0].to_dict()

    if received_at:
        fallback = read_df(
            """
            select id, parsed_data, filename, file_path, raw_text, action_required
            from order_intake
            where source in ('operations_email', 'email_body', 'email_combined')
              and coalesce(source_subject, '') = :subject
              and coalesce(source_sender, '') = :sender
              and source_received_at = cast(:received_at as timestamptz)
            limit 1
            """,
            {"subject": subject or "", "sender": sender or "", "received_at": received_at},
        )
        if not fallback.empty:
            return fallback.iloc[0].to_dict()

    fallback = read_df(
        """
        select id, parsed_data, filename, file_path, raw_text, action_required
        from order_intake
        where source in ('operations_email', 'email_body', 'email_combined')
          and coalesce(source_subject, '') = :subject
          and coalesce(source_sender, '') = :sender
          and source_received_at is null
        limit 1
        """,
        {"subject": subject or "", "sender": sender or ""},
    )
    if not fallback.empty:
        return fallback.iloc[0].to_dict()

    return None


def _load_existing_operations_email_lookup(limit: int = 5000) -> dict:
    lookup = {
        "loaded": False,
        "by_message_id": {},
        "by_received": {},
        "by_subject_sender_no_received": {},
    }

    try:
        existing_df = read_df(
            """
            select
                id,
                parsed_data,
                filename,
                file_path,
                raw_text,
                action_required,
                source_message_id,
                coalesce(source_subject, '') as source_subject,
                coalesce(source_sender, '') as source_sender,
                source_received_at
            from order_intake
            where source in ('operations_email', 'email_body', 'email_combined')
               or source_message_id is not null
            order by created_at desc
            limit :limit
            """,
            {"limit": int(limit)},
        )
    except Exception:
        return lookup

    lookup["loaded"] = True
    for _, row in existing_df.iterrows():
        record = row.to_dict()
        message_id = _safe_str(record.get("source_message_id", ""))
        subject = _safe_str(record.get("source_subject", ""))
        sender = _safe_str(record.get("source_sender", ""))
        received_key = _email_received_lookup_key(record.get("source_received_at"))

        if message_id and message_id not in lookup["by_message_id"]:
            lookup["by_message_id"][message_id] = record
        if received_key:
            lookup["by_received"].setdefault((subject, sender, received_key), record)
        else:
            lookup["by_subject_sender_no_received"].setdefault((subject, sender), record)

    return lookup


def _find_existing_operations_email_record_from_lookup(
    lookup: dict,
    message_id: str,
    subject: str,
    sender: str,
    received_at: str | None = None,
) -> dict | None:
    message_id = _safe_str(message_id)
    subject = _safe_str(subject)
    sender = _safe_str(sender)

    if message_id:
        existing = lookup.get("by_message_id", {}).get(message_id)
        if existing:
            return existing

    received_key = _email_received_lookup_key(received_at)
    if received_key:
        existing = lookup.get("by_received", {}).get((subject, sender, received_key))
        if existing:
            return existing

    return lookup.get("by_subject_sender_no_received", {}).get((subject, sender))


def _operations_email_already_imported(message_id: str, subject: str, sender: str, received_at: str | None = None) -> bool:
    return _find_existing_operations_email_record(message_id, subject, sender, received_at) is not None


def _backfill_operations_pdf_attachments(
    *,
    existing_record: dict,
    email_item: dict,
    message_id: str,
) -> int:
    parsed = _coerce_json_dict(existing_record.get("parsed_data"))
    existing_attachments = _extract_operations_pdf_attachments(parsed, existing_record)
    existing_names = {_safe_str(item.get("filename", "")).lower() for item in existing_attachments}
    existing_paths = {_safe_str(item.get("file_path", "")) for item in existing_attachments}

    new_attachments = []
    for attachment_index, attachment in enumerate(email_item.get("attachments", []) or [], start=1):
        filename = _safe_str(attachment.get("filename", ""))
        content = attachment.get("content") or b""
        if not filename.lower().endswith(".pdf") or not content:
            continue
        if filename.lower() in existing_names:
            continue

        saved = _save_operations_pdf_attachment(
            content=content,
            filename=filename,
            message_id=message_id or f"intake-{existing_record.get('id')}",
            attachment_index=len(existing_attachments) + len(new_attachments) + attachment_index,
        )
        if _safe_str(saved.get("file_path", "")) in existing_paths:
            continue
        new_attachments.append(saved)

    if not new_attachments:
        return 0

    merged_attachments = existing_attachments + new_attachments
    updated_parsed = dict(parsed)
    for attachment in new_attachments:
        pdf_parsed = attachment.get("parsed_data") or {}
        for field in OPERATIONS_ORDER_FIELDS:
            if _safe_str(pdf_parsed.get(field, "")) and not _safe_str(updated_parsed.get(field, "")):
                updated_parsed[field] = _safe_str(pdf_parsed.get(field, ""))
    updated_parsed[OPERATIONS_PDF_ATTACHMENTS_KEY] = merged_attachments

    primary = merged_attachments[0]
    execute(
        """
        update order_intake
        set parsed_data = cast(:parsed_data as jsonb),
            filename = coalesce(filename, :filename),
            file_path = coalesce(file_path, :file_path),
            action_required = case
                when coalesce(action_required, '') = '' then :action_required
                else action_required
            end
        where id = :intake_id
        """,
        {
            "intake_id": int(existing_record["id"]),
            "parsed_data": _json_dump(updated_parsed),
            "filename": primary.get("filename"),
            "file_path": primary.get("file_path"),
            "action_required": _order_action_required_from_parsed(updated_parsed),
        },
    )

    return len(new_attachments)


def _operations_inbox_status_counts() -> pd.DataFrame:
    try:
        return read_df(
            """
            select
                coalesce(review_status, 'Open') as review_status,
                count(*) as email_count
            from order_intake
            where source in ('operations_email', 'email_body', 'email_combined')
            group by coalesce(review_status, 'Open')
            order by email_count desc, review_status
            """
        )
    except Exception:
        return pd.DataFrame()


def _render_no_open_inbox_explanation() -> None:
    result = st.session_state.get("operations_email_import_result") or {}
    fetched = int(result.get("fetched", 0) or 0)
    imported = int(result.get("imported", 0) or 0)
    skipped = int(result.get("skipped", 0) or 0)

    if fetched > 0 and imported == 0 and skipped > 0:
        st.info(
            "The recent Yahoo scan found emails that are already saved in Operations Inbox. "
            "Nothing new was added, so if no rows appear here, those saved requests are likely already closed, attached, "
            "converted to orders/quotes, or otherwise filtered out of the open inbox."
        )

    status_counts = _operations_inbox_status_counts()
    if not status_counts.empty:
        st.caption("Saved operations email status counts")
        st.dataframe(status_counts, use_container_width=True, hide_index=True)


def _action_required_for_request(
    request_type: str,
    parsed: dict,
    body: str,
    subject: str = "",
    tokens: dict | None = None,
    matched_load_id=None,
) -> str:
    text = f"{subject or ''} {body or ''}"
    tokens = tokens or _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    has_reference = _has_reference_details(tokens, parsed)

    if request_type == "Customer Request":
        if _contains_any(text, UPDATE_INTENT_TERMS) and not has_reference:
            return "Customer is asking for an update but did not include booking, container, or reference. Reply for identifying details."
        if _contains_any(text, QUOTE_INTENT_TERMS) and not _has_quote_details(text, parsed, tokens):
            return "Customer may need pricing but did not include enough lane details. Reply for pickup, delivery, equipment, and date."
        if _contains_any(text, NEW_ORDER_INTENT_TERMS) and not _has_new_order_details(text, parsed, tokens):
            return "Customer may be sending an order but key load details are missing. Reply for the load order or booking details."
        return "Review customer question and reply from the inbox."

    if request_type == "Quote Request":
        if not _has_quote_details(text, parsed, tokens):
            return "Quote intent found, but lane/equipment/date details are missing. Reply before creating a quote."
        return "Quote details found; review lane, timing, and equipment before sending rate."

    if request_type == "Missing Information":
        return "Customer needs missing information; reply or attach to the matching order."

    if request_type == "Cancellation":
        if matched_load_id is None:
            return "Cancellation requested without a matched order. Ask for booking, container, or reference before cancelling."
        return "Customer requested cancellation; verify matching order before cancelling."

    if request_type == "Appointment Update":
        if matched_load_id is None:
            return "Appointment request needs booking, container, or reference before updating the schedule."
        return "Appointment update received; attach to matching order and update schedule."

    if request_type == "Booking Update":
        if matched_load_id is None:
            return "Update request needs booking, container, or reference before attaching to an order."
        return "Update matched to an order; attach it and update order notes or schedule."

    if request_type == "POD Request":
        if matched_load_id is None:
            return "POD request needs booking, container, or reference before sending documents."
        return "POD request matched to an order; verify POD status and reply."

    if request_type == "New Booking":
        missing = []
        for field in ["Booking Number", "Customer", "Warehouse"]:
            if not _safe_str(parsed.get(field, "")):
                missing.append(field)
        if missing:
            return "Missing order details: " + ", ".join(missing)
        return "New booking details found; review parsed fields before creating order."

    if body:
        return "Review message and choose the next action."
    return "Review imported email."


def _classification_confidence(
    request_type: str,
    subject: str,
    body: str,
    parsed: dict,
    tokens: dict,
    matched_load_id,
    match_confidence: int,
) -> int:
    text = f"{subject or ''} {body or ''}"

    if matched_load_id is not None:
        return max(match_confidence, 90)

    if request_type == "Customer Request":
        if (
            (_contains_any(text, UPDATE_INTENT_TERMS) and not _has_reference_details(tokens, parsed))
            or (_contains_any(text, QUOTE_INTENT_TERMS) and not _has_quote_details(text, parsed, tokens))
            or (_contains_any(text, NEW_ORDER_INTENT_TERMS) and not _has_new_order_details(text, parsed, tokens))
        ):
            return 60
        return 70

    if request_type == "Quote Request":
        return 80 if _has_quote_details(text, parsed, tokens) else 55

    if request_type == "New Booking":
        return 80 if _has_new_order_details(text, parsed, tokens) else 55

    if request_type in ["Appointment Update", "Booking Update", "Cancellation", "POD Request"]:
        return 75 if _has_reference_details(tokens, parsed) else 55

    if request_type == "Missing Information":
        return 75

    return max(match_confidence, 50)


def _build_operations_email_classification(
    subject: str,
    body: str,
    parsed: dict | None = None,
    fallback_key: str = "",
) -> dict:
    parsed = _coerce_parsed_for_classification(subject, body, parsed)
    detected_type = classify_customer_request(subject, body, parsed)
    tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    matched_load_id, match_confidence = find_matching_load(tokens)
    confidence = _classification_confidence(
        detected_type,
        subject,
        body,
        parsed,
        tokens,
        matched_load_id,
        match_confidence,
    )
    conversation_key = (
        tokens.get("booking_number")
        or tokens.get("container_number")
        or tokens.get("reference_number")
        or fallback_key
        or "customer-request"
    )
    action_required = _action_required_for_request(
        detected_type,
        parsed,
        body,
        subject=subject,
        tokens=tokens,
        matched_load_id=matched_load_id,
    )

    return {
        "request_type": detected_type,
        "tokens": tokens,
        "matched_load_id": matched_load_id,
        "confidence_score": confidence,
        "conversation_key": conversation_key,
        "action_required": action_required,
    }


def _operations_classification_for_review(record, parsed: dict, subject: str, body: str, fallback_key: str) -> dict:
    saved_type = _safe_str(record.get("request_type", ""))
    saved_confidence = pd.to_numeric(record.get("confidence_score", 0), errors="coerce")
    if pd.isna(saved_confidence):
        saved_confidence = 0

    saved_match = record.get("matched_load_id")
    saved_matched_load_id = None
    if pd.notna(saved_match) and _safe_str(saved_match):
        try:
            saved_matched_load_id = int(saved_match)
        except Exception:
            saved_matched_load_id = None

    saved_is_usable = (
        saved_type in REQUEST_TYPES
        and saved_type not in ["Needs Classification", "Other"]
        and (int(saved_confidence) >= 70 or saved_matched_load_id is not None)
    )

    if not saved_is_usable:
        return _build_operations_email_classification(
            subject,
            body,
            parsed,
            fallback_key=fallback_key,
        )

    tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    conversation_key = (
        _safe_str(record.get("conversation_key", ""))
        or tokens.get("booking_number")
        or tokens.get("container_number")
        or tokens.get("reference_number")
        or fallback_key
        or "customer-request"
    )
    action_required = _safe_str(record.get("action_required", "")) or _action_required_for_request(
        saved_type,
        parsed,
        body,
        subject=subject,
        tokens=tokens,
        matched_load_id=saved_matched_load_id,
    )

    return {
        "request_type": saved_type,
        "tokens": tokens,
        "matched_load_id": saved_matched_load_id,
        "confidence_score": int(saved_confidence),
        "conversation_key": conversation_key,
        "action_required": action_required,
    }


AI_LOAD_CONTEXT_COLUMNS = [
    "id",
    "load_id",
    "type",
    "booking_number",
    "reference_number",
    "container_number",
    "customer",
    "port",
    "warehouse",
    "address",
    "delivery_need_date",
    "lfd",
    "status",
    "driver_name",
    "truck_assigned",
    "chassis",
    "size",
    "steamship_line",
    "vessel_name",
    "terminal",
    "pickup_appointment",
    "delivery_appointment",
    "empty_return_location",
    "empty_return_date",
    "current_location",
    "eta",
    "live_load_status",
    "live_unload_status",
    "last_driver_update",
]

AI_LOAD_CONTEXT_LABELS = {
    "id": "Load ID",
    "load_id": "External Load ID",
    "type": "Move Type",
    "booking_number": "Booking Number",
    "reference_number": "Reference Number",
    "container_number": "Container Number",
    "customer": "Customer",
    "port": "Pickup / Port",
    "warehouse": "Delivery / Warehouse",
    "address": "Delivery Address",
    "delivery_need_date": "Delivery Need Date",
    "lfd": "LFD",
    "status": "Status",
    "driver_name": "Driver",
    "truck_assigned": "Truck",
    "chassis": "Chassis",
    "size": "Container Size",
    "steamship_line": "Steamship Line",
    "vessel_name": "Vessel",
    "terminal": "Terminal",
    "pickup_appointment": "Pickup Appointment",
    "delivery_appointment": "Delivery Appointment",
    "empty_return_location": "Empty Return Location",
    "empty_return_date": "Empty Return Date",
    "current_location": "Current Location",
    "eta": "ETA",
    "live_load_status": "Live Load Status",
    "live_unload_status": "Live Unload Status",
    "last_driver_update": "Last Driver Update",
}


def _clean_ai_context_value(value) -> str:
    value_str = _safe_str(value)
    if value_str.lower() in {"nan", "nat"}:
        return ""
    return value_str


def _existing_load_columns() -> set[str]:
    try:
        df = read_df(
            """
            select column_name
            from information_schema.columns
            where table_name = 'loads'
            """
        )
        return set(df["column_name"].astype(str).tolist())
    except Exception:
        return {
            "id",
            "load_id",
            "type",
            "booking_number",
            "reference_number",
            "container_number",
            "customer",
            "port",
            "warehouse",
            "address",
            "delivery_need_date",
            "lfd",
            "status",
            "driver_name",
            "truck_assigned",
            "chassis",
            "size",
        }


def _load_context_select_columns() -> list[str]:
    existing = _existing_load_columns()
    return [column for column in AI_LOAD_CONTEXT_COLUMNS if column in existing]


def _load_row_to_ai_context(row: dict) -> dict:
    context = {}
    for key, label in AI_LOAD_CONTEXT_LABELS.items():
        if key in row:
            value = _clean_ai_context_value(row.get(key))
            if value:
                context[label] = value
    return context


def _load_document_context(load_id) -> dict:
    if load_id is None:
        return {}

    try:
        docs_df = read_df(
            """
            select document_type, filename, created_at
            from documents
            where load_id = :load_id
            order by created_at desc
            limit 12
            """,
            {"load_id": int(load_id)},
        )
    except Exception:
        return {}

    if docs_df.empty:
        return {
            "Document Count": "0",
            "POD Available": "No document found",
        }

    doc_types = [
        _clean_ai_context_value(value)
        for value in docs_df.get("document_type", pd.Series(dtype=str)).tolist()
    ]
    doc_names = [
        _clean_ai_context_value(value)
        for value in docs_df.get("filename", pd.Series(dtype=str)).tolist()
    ]
    haystack = " ".join(doc_types + doc_names).lower()
    pod_available = "Yes" if ("pod" in haystack or "proof" in haystack or "delivery" in haystack) else "No document found"

    return {
        "Document Count": str(len(docs_df)),
        "Document Types": ", ".join([value for value in doc_types if value][:6]),
        "POD Available": pod_available,
    }


def _fetch_ai_load_context(load_id) -> dict:
    if load_id is None:
        return {}

    try:
        columns = _load_context_select_columns()
        if not columns:
            return {}
        load_df = read_df(
            f"""
            select {", ".join(columns)}
            from loads
            where id = :load_id
            limit 1
            """,
            {"load_id": int(load_id)},
        )
    except Exception:
        return {}

    if load_df.empty:
        return {}

    context = _load_row_to_ai_context(load_df.iloc[0].to_dict())
    context.update(_load_document_context(load_id))
    return context


def _candidate_summary_from_context(context: dict) -> dict:
    keep = [
        "Load ID",
        "External Load ID",
        "Booking Number",
        "Reference Number",
        "Container Number",
        "Customer",
        "Status",
        "Pickup / Port",
        "Delivery / Warehouse",
        "Delivery Need Date",
        "LFD",
        "ETA",
        "Current Location",
        "Pickup Appointment",
        "Delivery Appointment",
        "POD Available",
    ]
    return {key: context.get(key, "") for key in keep if context.get(key)}


def _find_ai_load_candidates(tokens: dict, parsed: dict, matched_load_id=None, limit: int = 5) -> list[dict]:
    candidate_ids = []
    if matched_load_id is not None:
        candidate_ids.append(int(matched_load_id))

    conditions = []
    params = {"limit": int(limit)}

    booking = _safe_str(tokens.get("booking_number") or parsed.get("Booking Number", ""))
    container = _safe_str(tokens.get("container_number") or parsed.get("Container Number", ""))
    reference = _safe_str(tokens.get("reference_number") or parsed.get("Reference Number", ""))
    customer = _safe_str(parsed.get("Customer", ""))

    if booking:
        conditions.append("lower(coalesce(booking_number, '')) like lower(:booking_like)")
        params["booking_like"] = f"%{booking}%"
    if container:
        conditions.append("lower(coalesce(container_number, '')) like lower(:container_like)")
        params["container_like"] = f"%{container}%"
    if reference:
        conditions.append("lower(coalesce(reference_number, '')) like lower(:reference_like)")
        params["reference_like"] = f"%{reference}%"
    if customer and len(customer) >= 4:
        conditions.append("lower(coalesce(customer, '')) like lower(:customer_like)")
        params["customer_like"] = f"%{customer}%"

    if conditions:
        try:
            ids_df = read_df(
                f"""
                select id
                from loads
                where {" or ".join(conditions)}
                order by updated_at desc
                limit :limit
                """,
                params,
            )
            for value in ids_df.get("id", pd.Series(dtype=int)).tolist():
                if pd.notna(value):
                    candidate_ids.append(int(value))
        except Exception:
            pass

    candidates = []
    seen = set()
    for load_id in candidate_ids:
        if load_id in seen:
            continue
        seen.add(load_id)
        context = _fetch_ai_load_context(load_id)
        if context:
            candidates.append(_candidate_summary_from_context(context))
        if len(candidates) >= limit:
            break

    return candidates


def _build_ai_load_context(classification: dict, parsed: dict) -> tuple[dict, list[dict]]:
    matched_load_id = classification.get("matched_load_id")
    tokens = classification.get("tokens") or {}
    load_context = _fetch_ai_load_context(matched_load_id) if matched_load_id is not None else {}
    load_candidates = _find_ai_load_candidates(tokens, parsed, matched_load_id=matched_load_id)
    return load_context, load_candidates


def _valid_ai_suggested_load_id(ai_suggestion: dict, load_candidates: list[dict]) -> int | None:
    if not ai_suggestion or not ai_suggestion.get("success"):
        return None

    suggested = _safe_str(ai_suggestion.get("suggested_load_id", ""))
    if not suggested:
        return None

    valid_ids = {
        _safe_str(candidate.get("Load ID", ""))
        for candidate in load_candidates
        if _safe_str(candidate.get("Load ID", ""))
    }
    if suggested not in valid_ids:
        return None

    try:
        return int(suggested)
    except Exception:
        return None


def _ensure_operations_ai_feedback_table() -> None:
    execute(
        """
        create table if not exists operations_ai_feedback (
            id bigserial primary key,
            intake_id bigint references order_intake(id) on delete cascade,
            load_id bigint references loads(id) on delete set null,
            source_subject text,
            source_sender text,
            ai_request_type text,
            final_request_type text,
            ai_confidence_score integer,
            ai_priority text,
            ai_action_required text,
            final_action_required text,
            ai_reply_body text,
            final_reply_body text,
            correction_type text not null,
            feedback_notes text,
            created_by text not null default 'dispatcher',
            created_at timestamptz not null default now()
        )
        """
    )
    execute(
        """
        create index if not exists idx_operations_ai_feedback_created_at
        on operations_ai_feedback(created_at)
        """
    )
    execute(
        """
        create index if not exists idx_operations_ai_feedback_intake_id
        on operations_ai_feedback(intake_id)
        """
    )


def _truncate_feedback_text(value, limit: int = 700) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].strip() + "..."


def _recent_operations_ai_feedback_examples(limit: int = 6) -> list[dict]:
    try:
        _ensure_operations_ai_feedback_table()
        feedback_df = read_df(
            """
            select
                correction_type,
                source_subject,
                ai_request_type,
                final_request_type,
                ai_action_required,
                final_action_required,
                ai_reply_body,
                final_reply_body,
                feedback_notes,
                created_at
            from operations_ai_feedback
            order by created_at desc
            limit :limit
            """,
            {"limit": int(limit)},
        )
    except Exception:
        return []

    examples = []
    for _, row in feedback_df.iterrows():
        examples.append(
            {
                "correction_type": _safe_str(row.get("correction_type", "")),
                "subject_hint": _truncate_feedback_text(row.get("source_subject", ""), 160),
                "ai_request_type": _safe_str(row.get("ai_request_type", "")),
                "final_request_type": _safe_str(row.get("final_request_type", "")),
                "ai_action_required": _truncate_feedback_text(row.get("ai_action_required", ""), 220),
                "final_action_required": _truncate_feedback_text(row.get("final_action_required", ""), 220),
                "ai_reply_body": _truncate_feedback_text(row.get("ai_reply_body", ""), 450),
                "final_reply_body": _truncate_feedback_text(row.get("final_reply_body", ""), 450),
                "feedback_notes": _truncate_feedback_text(row.get("feedback_notes", ""), 220),
            }
        )
    return examples


def _save_operations_ai_feedback(
    *,
    intake_id: int,
    load_id,
    source_subject: str,
    source_sender: str,
    ai_suggestion: dict | None,
    final_request_type: str,
    final_action_required: str = "",
    final_reply_body: str = "",
    correction_type: str,
    feedback_notes: str = "",
) -> None:
    if not ai_suggestion or not ai_suggestion.get("success"):
        return

    try:
        _ensure_operations_ai_feedback_table()
        execute(
            """
            insert into operations_ai_feedback (
                intake_id,
                load_id,
                source_subject,
                source_sender,
                ai_request_type,
                final_request_type,
                ai_confidence_score,
                ai_priority,
                ai_action_required,
                final_action_required,
                ai_reply_body,
                final_reply_body,
                correction_type,
                feedback_notes
            )
            values (
                :intake_id,
                :load_id,
                :source_subject,
                :source_sender,
                :ai_request_type,
                :final_request_type,
                :ai_confidence_score,
                :ai_priority,
                :ai_action_required,
                :final_action_required,
                :ai_reply_body,
                :final_reply_body,
                :correction_type,
                :feedback_notes
            )
            """,
            {
                "intake_id": int(intake_id),
                "load_id": load_id,
                "source_subject": source_subject,
                "source_sender": source_sender,
                "ai_request_type": ai_suggestion.get("request_type", ""),
                "final_request_type": final_request_type,
                "ai_confidence_score": int(ai_suggestion.get("confidence_score", 0) or 0),
                "ai_priority": ai_suggestion.get("priority", ""),
                "ai_action_required": ai_suggestion.get("action_required", ""),
                "final_action_required": final_action_required,
                "ai_reply_body": ai_suggestion.get("reply_body", ""),
                "final_reply_body": final_reply_body,
                "correction_type": correction_type,
                "feedback_notes": feedback_notes,
            },
        )
    except Exception:
        pass


def _operations_ai_rule_context(classification: dict, parsed: dict, subject: str, body: str) -> dict:
    tokens = classification.get("tokens") or _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    return {
        "request_type": classification.get("request_type", "Customer Request"),
        "confidence_score": classification.get("confidence_score", 0),
        "action_required": classification.get("action_required", ""),
        "conversation_key": classification.get("conversation_key", ""),
        "matched_load_id": classification.get("matched_load_id"),
        "tokens": tokens,
    }


def _conversation_key_from_candidate(candidate: dict, fallback: str) -> str:
    for key in ["Booking Number", "Container Number", "Reference Number", "External Load ID", "Load ID"]:
        value = _safe_str(candidate.get(key, ""))
        if value:
            return value
    return fallback


def _apply_ai_suggestion_to_classification(
    classification: dict,
    ai_suggestion: dict,
    load_candidates: list[dict] | None = None,
) -> dict:
    if not ai_suggestion or not ai_suggestion.get("success"):
        return classification

    request_type = ai_suggestion.get("request_type")
    if request_type not in REQUEST_TYPES:
        return classification

    updated = dict(classification)
    updated["request_type"] = request_type
    updated["confidence_score"] = int(ai_suggestion.get("confidence_score", classification.get("confidence_score", 0)) or 0)
    updated["action_required"] = ai_suggestion.get("action_required") or classification.get("action_required", "")
    suggested_load_id = _valid_ai_suggested_load_id(ai_suggestion, load_candidates or [])
    if suggested_load_id is not None:
        updated["matched_load_id"] = suggested_load_id
        for candidate in load_candidates or []:
            if _safe_str(candidate.get("Load ID", "")) == str(suggested_load_id):
                updated["conversation_key"] = _conversation_key_from_candidate(
                    candidate,
                    updated.get("conversation_key", ""),
                )
                break
    return updated


def import_recent_operations_emails(limit: int = 50) -> tuple[int, int, int, int]:
    emails = fetch_recent_operations_emails(limit=limit)
    existing_lookup = _load_existing_operations_email_lookup()
    imported = 0
    skipped = 0
    pdf_updated = 0
    fetched = len(emails)

    for item in emails:
        subject = str(item.get("subject", "") or "")
        sender = str(item.get("from", "") or "")
        body = str(item.get("body", "") or "")
        message_id = str(item.get("message_id", "") or item.get("id", "") or "")
        received_at = item.get("received_at")

        if existing_lookup.get("loaded"):
            existing_record = _find_existing_operations_email_record_from_lookup(
                existing_lookup,
                message_id,
                subject,
                sender,
                received_at,
            )
        else:
            existing_record = _find_existing_operations_email_record(message_id, subject, sender, received_at)
        if existing_record:
            pdf_updated += _backfill_operations_pdf_attachments(
                existing_record=existing_record,
                email_item=item,
                message_id=message_id or f"email-{skipped + 1}",
            )
            skipped += 1
            continue

        try:
            body_parsed = parse_email_text(subject, body)
        except Exception:
            body_parsed = {}

        pdf_attachments = []
        for attachment_index, attachment in enumerate(item.get("attachments", []) or [], start=1):
            filename = _safe_str(attachment.get("filename", ""))
            content = attachment.get("content") or b""
            if not filename.lower().endswith(".pdf") or not content:
                continue
            pdf_attachments.append(
                _save_operations_pdf_attachment(
                    content=content,
                    filename=filename,
                    message_id=message_id or f"operations-{imported + 1}",
                    attachment_index=attachment_index,
                )
            )

        parsed = dict(body_parsed)
        if pdf_attachments:
            for attachment in pdf_attachments:
                pdf_parsed = attachment.get("parsed_data") or {}
                for field in OPERATIONS_ORDER_FIELDS:
                    if _safe_str(pdf_parsed.get(field, "")) and not _safe_str(parsed.get(field, "")):
                        parsed[field] = _safe_str(pdf_parsed.get(field, ""))
            parsed[OPERATIONS_PDF_ATTACHMENTS_KEY] = pdf_attachments

        classification = _build_operations_email_classification(
            subject,
            body,
            parsed,
            fallback_key=message_id or f"email-{imported + 1}",
        )
        if is_operations_ai_auto_classify_enabled():
            load_context, load_candidates = _build_ai_load_context(classification, parsed)
            ai_suggestion = generate_operations_ai_suggestion(
                subject=subject,
                sender=sender,
                body=body,
                parsed=parsed,
                rule_classification=_operations_ai_rule_context(classification, parsed, subject, body),
                load_context=load_context,
                load_candidates=load_candidates,
                feedback_examples=_recent_operations_ai_feedback_examples(),
                response_language=_resolve_reply_language("Auto", subject, body),
                company_name=_get_app_setting("COMPANY_NAME", "CaliTrans"),
            )
            classification = _apply_ai_suggestion_to_classification(classification, ai_suggestion, load_candidates)

        execute(
            """
            insert into order_intake (
                source,
                source_subject,
                source_sender,
                source_received_at,
                source_message_id,
                filename,
                file_path,
                parsed_data,
                raw_text,
                intake_status,
                review_status,
                request_type,
                conversation_key,
                matched_load_id,
                confidence_score,
                action_required
            )
            values (
                'operations_email',
                :source_subject,
                :source_sender,
                :source_received_at,
                :source_message_id,
                :filename,
                :file_path,
                cast(:parsed_data as jsonb),
                :raw_text,
                'Needs Review',
                'Open',
                :request_type,
                :conversation_key,
                :matched_load_id,
                :confidence_score,
                :action_required
            )
            """,
            {
                "source_subject": subject,
                "source_sender": sender,
                "source_received_at": received_at,
                "source_message_id": message_id or None,
                "filename": pdf_attachments[0].get("filename") if pdf_attachments else None,
                "file_path": pdf_attachments[0].get("file_path") if pdf_attachments else None,
                "parsed_data": _json_dump(parsed),
                "raw_text": body,
                "request_type": classification["request_type"],
                "conversation_key": classification["conversation_key"],
                "matched_load_id": classification["matched_load_id"],
                "confidence_score": classification["confidence_score"],
                "action_required": classification["action_required"],
            },
        )
        imported += 1

    return imported, skipped, fetched, pdf_updated


def _default_operations_reply_subject(subject: str, request_type: str) -> str:
    clean_subject = str(subject or "").strip()
    if clean_subject.lower().startswith("re:"):
        return clean_subject
    if clean_subject:
        return f"Re: {clean_subject}"
    return f"Re: {request_type}"


def _default_operations_reply_body(
    request_type: str,
    parsed: dict,
    matched_load_id,
    subject: str = "",
    body: str = "",
    reply_language: str = "Auto",
) -> str:
    company_name = _get_app_setting("COMPANY_NAME", "CaliTrans")
    booking = _safe_str(parsed.get("Booking Number", ""))
    container = _safe_str(parsed.get("Container Number", ""))
    tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    reference_number = _safe_str(parsed.get("Reference Number", "")) or tokens.get("reference_number", "")
    token_booking = tokens.get("booking_number", "")
    token_container = tokens.get("container_number", "")
    reference = (
        booking
        or container
        or reference_number
        or token_booking
        or token_container
        or (f"load {matched_load_id}" if matched_load_id else "your request")
    )
    text = f"{subject or ''} {body or ''}"
    resolved_language = _resolve_reply_language(reply_language, subject, body)

    english_reply = _default_operations_reply_body_english(
        request_type,
        parsed,
        matched_load_id,
        subject,
        body,
        company_name,
        reference,
        tokens,
        text,
    )
    if resolved_language == "English":
        return english_reply

    spanish_reply = _default_operations_reply_body_spanish(
        request_type,
        parsed,
        matched_load_id,
        subject,
        body,
        company_name,
        reference,
        tokens,
        text,
    )
    if resolved_language == "Spanish":
        return spanish_reply

    return f"{english_reply}\n\n---\n\n{spanish_reply}"


def _default_operations_reply_body_english(
    request_type: str,
    parsed: dict,
    matched_load_id,
    subject: str,
    body: str,
    company_name: str,
    reference: str,
    tokens: dict,
    text: str,
) -> str:
    if request_type == "Customer Request":
        if _contains_any(text, UPDATE_INTENT_TERMS) and not _has_reference_details(tokens, parsed):
            return (
                "Hello,\n\n"
                "We can check on this for you. Please send the booking number, container number, "
                "or reference number so we can pull up the correct load and send a current status update.\n\n"
                f"Thank you,\n{company_name} Dispatch"
            )
        if _contains_any(text, QUOTE_INTENT_TERMS) and not _has_quote_details(text, parsed, tokens):
            return (
                "Hello,\n\n"
                "We can prepare pricing for you. Please send the pickup location, delivery location, "
                "container size/type, requested date, and any special handling notes so we can quote it accurately.\n\n"
                f"Thank you,\n{company_name} Dispatch"
            )
        if _contains_any(text, NEW_ORDER_INTENT_TERMS) and not _has_new_order_details(text, parsed, tokens):
            return (
                "Hello,\n\n"
                "We can get this moving. Please send the load order or the booking number, customer name, "
                "container number, pickup terminal, delivery location, and requested delivery date.\n\n"
                f"Thank you,\n{company_name} Dispatch"
            )
        return (
            "Hello,\n\n"
            "We received your message and our dispatch team is reviewing it. "
            "We will follow up with the next update or any details needed.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "Appointment Update":
        return (
            "Hello,\n\n"
            f"We received your schedule request for {reference}. "
            "We are checking terminal and delivery availability now and will confirm the appointment window shortly.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "Quote Request":
        return (
            "Hello,\n\n"
            "Thank you for the rate request. We are reviewing the lane, timing, equipment, "
            "and any accessorials now and will follow up with pricing shortly.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "Booking Update":
        return (
            "Hello,\n\n"
            f"We received your update for {reference}. "
            "We are reviewing it against the order now and will confirm once the schedule or order notes are updated.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "New Booking":
        return (
            "Hello,\n\n"
            f"We received the booking details for {reference}. "
            "Our dispatch team is reviewing the order setup, appointment needs, and delivery requirements now. "
            "We will follow up if anything is missing before dispatch.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "Missing Information":
        return (
            "Hello,\n\n"
            f"We received your message for {reference}. "
            "We are checking the missing information now and will send the update as soon as it is confirmed.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "Cancellation":
        return (
            "Hello,\n\n"
            f"We received your cancellation request for {reference}. "
            "We are verifying the order details and will confirm once the change is complete.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "POD Request":
        return (
            "Hello,\n\n"
            f"We received your POD request for {reference}. "
            "We are checking document status now and will send it over as soon as it is available.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    return (
        "Hello,\n\n"
        f"We received your message for {reference}. "
        "Our dispatch team is reviewing it now and will follow up shortly.\n\n"
        f"Thank you,\n{company_name} Dispatch"
    )


def _default_operations_reply_body_spanish(
    request_type: str,
    parsed: dict,
    matched_load_id,
    subject: str,
    body: str,
    company_name: str,
    reference: str,
    tokens: dict,
    text: str,
) -> str:
    if request_type == "Customer Request":
        if _contains_any(text, UPDATE_INTENT_TERMS) and not _has_reference_details(tokens, parsed):
            return (
                "Hola,\n\n"
                "Con gusto podemos revisar esto. Por favor envíe el número de booking, número de contenedor "
                "o número de referencia para ubicar la carga correcta y enviarle una actualización de estado.\n\n"
                f"Gracias,\n{company_name} Dispatch"
            )
        if _contains_any(text, QUOTE_INTENT_TERMS) and not _has_quote_details(text, parsed, tokens):
            return (
                "Hola,\n\n"
                "Con gusto podemos preparar una cotización. Por favor envíe el lugar de recogida, lugar de entrega, "
                "tamaño/tipo de contenedor, fecha solicitada y cualquier instrucción especial para cotizar correctamente.\n\n"
                f"Gracias,\n{company_name} Dispatch"
            )
        if _contains_any(text, NEW_ORDER_INTENT_TERMS) and not _has_new_order_details(text, parsed, tokens):
            return (
                "Hola,\n\n"
                "Con gusto podemos avanzar con esto. Por favor envíe la orden de carga o el número de booking, "
                "nombre del cliente, número de contenedor, terminal de recogida, lugar de entrega y fecha solicitada de entrega.\n\n"
                f"Gracias,\n{company_name} Dispatch"
            )
        return (
            "Hola,\n\n"
            "Recibimos su mensaje y nuestro equipo de despacho lo está revisando. "
            "Le daremos seguimiento con la próxima actualización o con cualquier información necesaria.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "Appointment Update":
        return (
            "Hola,\n\n"
            f"Recibimos su solicitud de cita para {reference}. "
            "Estamos revisando la disponibilidad de la terminal y la entrega, y confirmaremos la ventana de cita en breve.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "Quote Request":
        return (
            "Hola,\n\n"
            "Gracias por la solicitud de tarifa. Estamos revisando la ruta, el tiempo, el equipo "
            "y cualquier cargo adicional aplicable. Le enviaremos la cotización en breve.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "Booking Update":
        return (
            "Hola,\n\n"
            f"Recibimos su actualización para {reference}. "
            "La estamos revisando contra la orden y confirmaremos cuando el horario o las notas de la orden estén actualizados.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "New Booking":
        return (
            "Hola,\n\n"
            f"Recibimos los detalles del booking para {reference}. "
            "Nuestro equipo de despacho está revisando la configuración de la orden, las citas necesarias y los requisitos de entrega. "
            "Le contactaremos si falta alguna información antes de despachar.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "Missing Information":
        return (
            "Hola,\n\n"
            f"Recibimos su mensaje para {reference}. "
            "Estamos revisando la información faltante y le enviaremos la actualización tan pronto sea confirmada.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "Cancellation":
        return (
            "Hola,\n\n"
            f"Recibimos su solicitud de cancelación para {reference}. "
            "Estamos verificando los detalles de la orden y confirmaremos cuando el cambio esté completo.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "POD Request":
        return (
            "Hola,\n\n"
            f"Recibimos su solicitud de POD para {reference}. "
            "Estamos revisando el estado del documento y se lo enviaremos tan pronto esté disponible.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    return (
        "Hola,\n\n"
        f"Recibimos su mensaje para {reference}. "
        "Nuestro equipo de despacho lo está revisando y le dará seguimiento en breve.\n\n"
        f"Gracias,\n{company_name} Dispatch"
    )

def save_operations_email_reply(
    *,
    intake_id: int,
    load_id,
    recipient: str,
    subject: str,
    body: str,
    status: str,
    error_message: str = "",
) -> None:
    execute(
        """
        insert into operations_email_replies (
            intake_id,
            load_id,
            recipient,
            subject,
            body,
            status,
            error_message,
            sent_at,
            sent_by
        )
        values (
            :intake_id,
            :load_id,
            :recipient,
            :subject,
            :body,
            :status,
            :error_message,
            case when :status = 'sent' then now() else null end,
            'dispatcher'
        )
        """,
        {
            "intake_id": intake_id,
            "load_id": load_id,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "status": status,
            "error_message": error_message or None,
        },
    )

def auto_classify_open_inbox_items(inbox_df: pd.DataFrame) -> int:
    updated_count = 0

    for _, row in inbox_df.iterrows():
        current_type = str(row.get("request_type", "") or "").strip()
        existing_match = row.get("matched_load_id")
        existing_has_match = pd.notna(existing_match) and _safe_str(existing_match) != ""
        existing_confidence = pd.to_numeric(row.get("confidence_score", 0), errors="coerce")
        if pd.isna(existing_confidence):
            existing_confidence = 0

        current_is_action_type = current_type in [
            "New Booking",
            "Booking Update",
            "Appointment Update",
            "Quote Request",
            "Cancellation",
            "POD Request",
        ]
        needs_classification = current_type in ["", "Needs Classification", "Other"]
        needs_correction_check = current_is_action_type and not existing_has_match and existing_confidence < 70

        if not needs_classification and not needs_correction_check:
            continue

        intake_id = int(row["id"])
        subject = str(row.get("source_subject", "") or "")
        body = str(row.get("raw_text", "") or "")
        parsed = _coerce_json_dict(row.get("parsed_data"))

        classification = _build_operations_email_classification(
            subject,
            body,
            parsed,
            fallback_key=f"intake-{intake_id}",
        )
        detected_type = classification["request_type"]
        matched_load_id = classification["matched_load_id"]
        confidence = classification["confidence_score"]

        should_update = needs_classification or (needs_correction_check and detected_type == "Customer Request")
        if not should_update:
            continue

        update_intake_classification(
            intake_id,
            detected_type,
            classification["conversation_key"],
            matched_load_id,
            confidence,
            classification["action_required"],
        )
        updated_count += 1

    return updated_count


OPERATIONS_LOAD_UPDATE_FIELD_TO_DB = {
    "TYPE": "type",
    "Booking Number": "booking_number",
    "Reference Number": "reference_number",
    "Container Number": "container_number",
    "Customer": "customer",
    "Size": "size",
    "Port": "port",
    "Warehouse": "warehouse",
    "Address": "address",
    "Delivery Need Date": "delivery_need_date",
    "Document Cutoff": "document_cutoff",
    "LFD": "lfd",
    "Dispatcher Notes": "dispatcher_notes",
}


def _order_action_required_from_parsed(parsed: dict) -> str:
    missing = [
        field
        for field in ["Booking Number", "Customer", "Warehouse"]
        if not _safe_str(parsed.get(field, ""))
    ]
    if missing:
        return "Missing order details: " + ", ".join(missing)
    return "PDF and email data ready for order review."


def _save_pdf_data_to_operations_request(
    *,
    intake_id: int,
    subject: str,
    body: str,
    parsed_data: dict,
    filename: str,
    file_path: str,
    pdf_text: str,
) -> None:
    action_required = _order_action_required_from_parsed(parsed_data)
    classification = _build_operations_email_classification(
        subject,
        f"{body}\n\nPDF TEXT:\n{pdf_text[:5000]}",
        parsed_data,
        fallback_key=f"intake-{intake_id}",
    )

    execute(
        """
        update order_intake
        set parsed_data = cast(:parsed_data as jsonb),
            filename = :filename,
            file_path = :file_path,
            request_type = :request_type,
            conversation_key = :conversation_key,
            matched_load_id = :matched_load_id,
            confidence_score = :confidence_score,
            action_required = :action_required
        where id = :intake_id
        """,
        {
            "intake_id": int(intake_id),
            "parsed_data": _json_dump(parsed_data),
            "filename": filename,
            "file_path": file_path,
            "request_type": classification["request_type"],
            "conversation_key": classification["conversation_key"],
            "matched_load_id": classification["matched_load_id"],
            "confidence_score": classification["confidence_score"],
            "action_required": action_required,
        },
    )


def _attach_saved_pdf_to_load(load_id: int, filename: str, file_path: str, source: str = "operations_inbox_pdf") -> None:
    execute(
        """
        insert into documents (load_id, document_type, filename, file_path, source)
        select :load_id, 'load_order', :filename, :file_path, :source
        where not exists (
            select 1
            from documents
            where load_id = :load_id
              and file_path = :file_path
        )
        """,
        {
            "load_id": int(load_id),
            "filename": filename,
            "file_path": file_path,
            "source": source,
        },
    )


def _update_load_from_operations_pdf(load_id: int, parsed: dict, fill_blank_only: bool = True) -> dict:
    db_columns = list(OPERATIONS_LOAD_UPDATE_FIELD_TO_DB.values())
    current_df = read_df(
        f"""
        select {", ".join(db_columns)}
        from loads
        where id = :load_id
        limit 1
        """,
        {"load_id": int(load_id)},
    )
    current = current_df.iloc[0].to_dict() if not current_df.empty else {}

    updates = {}
    for field, db_column in OPERATIONS_LOAD_UPDATE_FIELD_TO_DB.items():
        value = _safe_str(parsed.get(field, ""))
        if not value:
            continue
        if fill_blank_only and _safe_str(current.get(db_column, "")):
            continue
        updates[field] = value

    if updates:
        DispatchDatabaseClient().update_row_fields(int(load_id), updates)

    return updates


def _import_uploaded_pdf_to_operations_request(intake_id: int, parsed: dict, uploaded_file) -> dict:
    content = uploaded_file.getvalue()
    attachment = _save_operations_pdf_attachment(
        content=content,
        filename=uploaded_file.name,
        message_id=f"intake-{intake_id}",
        attachment_index=len(_extract_operations_pdf_attachments(parsed)) + 1,
    )

    updated_parsed = dict(parsed)
    attachments = _extract_operations_pdf_attachments(updated_parsed)
    attachments.append(attachment)

    pdf_parsed = attachment.get("parsed_data") or {}
    for field in OPERATIONS_ORDER_FIELDS:
        if _safe_str(pdf_parsed.get(field, "")) and not _safe_str(updated_parsed.get(field, "")):
            updated_parsed[field] = _safe_str(pdf_parsed.get(field, ""))
    updated_parsed[OPERATIONS_PDF_ATTACHMENTS_KEY] = attachments

    execute(
        """
        update order_intake
        set parsed_data = cast(:parsed_data as jsonb),
            filename = coalesce(filename, :filename),
            file_path = coalesce(file_path, :file_path),
            action_required = :action_required
        where id = :intake_id
        """,
        {
            "intake_id": int(intake_id),
            "parsed_data": _json_dump(updated_parsed),
            "filename": attachment.get("filename"),
            "file_path": attachment.get("file_path"),
            "action_required": _order_action_required_from_parsed(updated_parsed),
        },
    )

    return attachment


def _render_pdf_preview(content: bytes, filename: str) -> None:
    encoded = base64.b64encode(content).decode("ascii")
    st.markdown(
        f"""
        <iframe
            title="{filename}"
            src="data:application/pdf;base64,{encoded}"
            width="100%"
            height="620"
            style="border: 1px solid #d1d5db; border-radius: 8px;"
        ></iframe>
        """,
        unsafe_allow_html=True,
    )


def _render_operations_pdf_panel(
    *,
    selected_id: int,
    record,
    parsed: dict,
    subject: str,
    sender: str,
    body: str,
    matched_load_id,
    conversation_key: str,
) -> None:
    attachments = _extract_operations_pdf_attachments(parsed, record)

    with st.expander("PDF Attachments / Order Documents", expanded=bool(attachments)):
        uploaded_pdf = st.file_uploader(
            "Add PDF to this request",
            type=["pdf"],
            key=f"operations_pdf_upload_{selected_id}",
        )
        if uploaded_pdf is not None:
            if st.button(
                "Import Uploaded PDF",
                key=f"operations_pdf_import_upload_{selected_id}",
                use_container_width=True,
            ):
                attachment = _import_uploaded_pdf_to_operations_request(int(selected_id), parsed, uploaded_pdf)
                st.success(f"Imported PDF: {attachment.get('filename', uploaded_pdf.name)}")
                refresh_data()
                st.rerun()

        if not attachments:
            st.info("No PDF attachments were saved with this inbox request.")
            return

        labels = []
        for idx, attachment in enumerate(attachments):
            filename = _safe_str(attachment.get("filename", f"attachment_{idx + 1}.pdf"))
            fields_found = int(attachment.get("fields_found", 0) or 0)
            labels.append(f"{idx + 1}. {filename} ({fields_found} field(s) found)")

        selected_label = st.selectbox(
            "Select PDF",
            labels,
            key=f"operations_pdf_select_{selected_id}",
        )
        selected_index = labels.index(selected_label)
        attachment = attachments[selected_index]
        filename = _safe_str(attachment.get("filename", f"attachment_{selected_index + 1}.pdf"))
        file_path = _safe_str(attachment.get("file_path", ""))

        if not file_path or not Path(file_path).exists():
            st.warning("The saved PDF file could not be found on disk.")
            return

        try:
            content = _read_operations_pdf_bytes(file_path)
        except Exception as exc:
            st.error(f"Could not read saved PDF: {exc}")
            return

        d1, d2, d3 = st.columns([1, 1, 2])
        with d1:
            st.download_button(
                "Download PDF",
                data=content,
                file_name=filename,
                mime="application/pdf",
                key=f"operations_pdf_download_{selected_id}_{selected_index}",
                use_container_width=True,
            )
        with d2:
            show_preview = st.checkbox(
                "View PDF",
                value=False,
                key=f"operations_pdf_preview_{selected_id}_{selected_index}",
            )
        with d3:
            st.caption(f"Saved file: {filename}")

        if show_preview:
            _render_pdf_preview(content, filename)

        try:
            pdf_text, pdf_parsed = _parse_saved_operations_pdf(file_path, filename)
            parse_error = ""
        except Exception as exc:
            pdf_text = _safe_str(attachment.get("text_preview", ""))
            pdf_parsed = attachment.get("parsed_data") or {}
            parse_error = str(exc)

        if parse_error:
            st.warning(f"PDF text parse needs review: {parse_error}")

        try:
            body_parsed = parse_email_text(subject, body)
        except Exception:
            body_parsed = {}

        base_parsed = {}
        for field in OPERATIONS_ORDER_FIELDS:
            base_parsed[field] = _safe_str(body_parsed.get(field, "")) or _safe_str(parsed.get(field, ""))

        final_data, comparison_rows, conflicts = _merge_operations_order_fields(base_parsed, pdf_parsed)
        final_data[OPERATIONS_PDF_ATTACHMENTS_KEY] = attachments

        c1, c2, c3 = st.columns(3)
        c1.metric("Email Fields", _field_count(body_parsed))
        c2.metric("PDF Fields", _field_count(pdf_parsed))
        c3.metric("Mismatches", len(conflicts))

        if conflicts:
            st.warning("Review mismatched fields before creating or updating a load: " + ", ".join(conflicts))

        st.dataframe(pd.DataFrame(comparison_rows), use_container_width=True, hide_index=True)

        with st.expander("Extracted PDF Text", expanded=False):
            st.text_area(
                "PDF Text",
                value=pdf_text or "No text was extracted from this PDF.",
                height=220,
                disabled=True,
                key=f"operations_pdf_text_{selected_id}_{selected_index}",
            )

        fill_blank_only = st.checkbox(
            "Only fill blank fields when updating an existing load",
            value=True,
            key=f"operations_pdf_fill_blank_only_{selected_id}_{selected_index}",
        )

        b1, b2, b3, b4 = st.columns(4)
        with b1:
            if st.button("Use PDF Data", key=f"use_pdf_data_{selected_id}_{selected_index}", use_container_width=True):
                _save_pdf_data_to_operations_request(
                    intake_id=int(selected_id),
                    subject=subject,
                    body=body,
                    parsed_data=final_data,
                    filename=filename,
                    file_path=file_path,
                    pdf_text=pdf_text,
                )
                st.success("PDF data saved to this Operations request.")
                refresh_data()
                st.rerun()

        can_create_from_pdf = bool(_safe_str(final_data.get("Booking Number", "")) and _safe_str(final_data.get("Customer", "")))
        with b2:
            if st.button(
                "Create Load",
                key=f"create_load_from_pdf_{selected_id}_{selected_index}",
                use_container_width=True,
                disabled=not can_create_from_pdf,
            ):
                _save_pdf_data_to_operations_request(
                    intake_id=int(selected_id),
                    subject=subject,
                    body=body,
                    parsed_data=final_data,
                    filename=filename,
                    file_path=file_path,
                    pdf_text=pdf_text,
                )
                load_id = create_load_from_intake(int(selected_id), final_data)
                execute(
                    """
                    update order_intake
                    set review_status = 'Order Created',
                        matched_load_id = :load_id,
                        request_type = 'New Booking',
                        conversation_key = :conversation_key
                    where id = :intake_id
                    """,
                    {
                        "intake_id": int(selected_id),
                        "load_id": load_id,
                        "conversation_key": conversation_key or final_data.get("Booking Number"),
                    },
                )
                refresh_data()
                st.success(f"Created load ID {load_id} from selected PDF.")
                st.rerun()

        with b3:
            if st.button(
                "Attach PDF",
                key=f"attach_pdf_to_load_{selected_id}_{selected_index}",
                use_container_width=True,
                disabled=matched_load_id is None,
            ):
                _attach_saved_pdf_to_load(int(matched_load_id), filename, file_path)
                save_load_communication(
                    matched_load_id,
                    int(selected_id),
                    conversation_key,
                    "PDF Attachment",
                    subject,
                    sender,
                    f"Attached PDF document from Operations Inbox: {filename}",
                )
                st.success("PDF attached to the matched load.")
                st.rerun()

        with b4:
            if st.button(
                "Update Load",
                key=f"update_load_from_pdf_{selected_id}_{selected_index}",
                use_container_width=True,
                disabled=matched_load_id is None,
            ):
                updates = _update_load_from_operations_pdf(
                    int(matched_load_id),
                    final_data,
                    fill_blank_only=fill_blank_only,
                )
                if updates:
                    _attach_saved_pdf_to_load(int(matched_load_id), filename, file_path)
                    save_load_communication(
                        matched_load_id,
                        int(selected_id),
                        conversation_key,
                        "PDF Update",
                        subject,
                        sender,
                        "Updated load fields from Operations Inbox PDF: " + ", ".join(updates.keys()),
                    )
                    refresh_data()
                    st.success("Updated matched load fields: " + ", ".join(updates.keys()))
                    st.rerun()
                else:
                    st.info("No load fields needed updating from the selected PDF.")
        
def render_operations_inbox() -> None:
    st.subheader("Operations Inbox")
    st.caption("Classify customer emails, match updates to existing loads, create bookings, create quote requests, or send replies.")
    c1, c2, c3 = st.columns([1, 1, 3])

    with c1:
        if st.button("Refresh Inbox", use_container_width=True):
            refresh_data()
            st.rerun()

    with c2:
        if st.button("Check Client Email", use_container_width=True):
            try:
                imported, skipped, fetched, pdf_updated = import_recent_operations_emails(limit=50)
                st.session_state["operations_email_import_result"] = {
                    "fetched": fetched,
                    "imported": imported,
                    "skipped": skipped,
                    "pdf_updated": pdf_updated,
                }
                refresh_data()
                st.rerun()
            except Exception as exc:
                st.error(f"Could not import client emails: {exc}")

    with c3:
        result = st.session_state.get("operations_email_import_result")
        if result:
            fetched = int(result.get("fetched", 0))
            imported = int(result.get("imported", 0))
            skipped = int(result.get("skipped", 0))
            pdf_updated = int(result.get("pdf_updated", 0))
            if fetched == 0:
                st.warning("Yahoo inbox connected, but no messages were returned from the recent inbox scan.")
            else:
                pdf_note = f", updated {pdf_updated} PDF attachment(s)" if pdf_updated else ""
                st.success(f"Yahoo inbox fetched {fetched} email(s), imported {imported}, skipped {skipped} already in Operations Inbox{pdf_note}.")
        else:
            st.info("Open items are classified automatically. Replies can be sent from the request review panel.")
            
    try:
        where_clause = _inbox_review_where_clause()
        inbox_df = _load_operations_inbox_df(where_clause)
    except Exception as exc:
        st.error(f"Could not load Operations Inbox: {exc}")
        st.info("If this is the first time using Operations Inbox email, run database/operations_email_workflow_migration.sql in Supabase.")
        return

    if inbox_df.empty:
        st.success("No open customer requests.")
        _render_no_open_inbox_explanation()
        return

    inbox_df["request_type_clean"] = (
        inbox_df["request_type"]
                .fillna("Needs Classification")
                .astype(str)
                .str.strip()
        )
    inbox_df["confidence_score"] = pd.to_numeric(
        inbox_df["confidence_score"],
        errors="coerce",
    ).fillna(0).astype(int)

    def extract_reference_from_text(text: str) -> str:
        tokens = _extract_reference_tokens(text)
        return (
            tokens.get("booking_number")
            or tokens.get("container_number")
            or tokens.get("reference_number")
            or ""
        )

    def extract_requested_time(text: str) -> str:
        text = str(text)
        match = re.search(r"\b(?:at\s*)?(\d{3,4})\s*(?:on\s*)?(\d{1,2}/\d{1,2})?\b", text, re.I)
        if match:
            time_part = match.group(1)
            date_part = match.group(2) or ""
            return f"{time_part} {date_part}".strip()
        return ""

    inbox_df["client_name"] = (
        inbox_df["source_sender"]
        .fillna("")
        .astype(str)
        .str.extract(r"^([^<]+)")[0]
        .fillna("")
        .str.strip()
    )

    inbox_df["email_received"] = pd.to_datetime(
        inbox_df["source_received_at"],
        errors="coerce",
    ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")

    inbox_df["reference_hint"] = inbox_df["conversation_key"].fillna("").astype(str)
    generic_reference = inbox_df["reference_hint"].str.lower().isin(["", "customer-request"]) | inbox_df["reference_hint"].str.lower().str.startswith(("email-", "intake-"))
    inbox_df.loc[generic_reference, "reference_hint"] = (
        inbox_df.loc[generic_reference, "source_subject"].fillna("").apply(extract_reference_from_text)
    )
    inbox_df["requested_time"] = ""
    inbox_df["review_status_clean"] = (
        inbox_df["review_status"]
                .fillna("Open")
                .astype(str)
                .str.strip()
        )
    no_matched_load = inbox_df["matched_load_id"].isna() | inbox_df["matched_load_id"].astype(str).isin(["", "nan", "None"])
    needs_details_mask = (
        no_matched_load
        & inbox_df["confidence_score"].lt(70)
        & inbox_df["request_type_clean"].isin([
            "Customer Request",
            "New Booking",
            "Booking Update",
            "Appointment Update",
            "Quote Request",
            "Cancellation",
            "POD Request",
        ])
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Open Requests", len(inbox_df))
    m2.metric("Needs Details", int(needs_details_mask.sum()))
    m3.metric("Customer Requests", int(inbox_df["request_type_clean"].eq("Customer Request").sum()))
    m4.metric("Quotes", int(inbox_df["request_type_clean"].eq("Quote Request").sum()))
    m5.metric("Waiting", int(inbox_df["review_status_clean"].eq("Waiting on Customer").sum()))

    smart_group_result = st.session_state.pop("operations_smart_group_update_result", None)
    if smart_group_result is not None:
        st.success(f"Smart groups updated {int(smart_group_result)} item(s).")

    c_update, c_note = st.columns([1, 4])
    with c_update:
        if st.button("Recheck Groups", key="operations_recheck_smart_groups", use_container_width=True):
            with st.spinner("Updating smart groups..."):
                full_inbox_df = _load_operations_inbox_record_set(where_clause)
                update_mask = _operations_items_needing_smart_group_update(full_inbox_df)
                classified_count = auto_classify_open_inbox_items(full_inbox_df[update_mask].copy())
                st.session_state["operations_smart_group_update_result"] = classified_count
                refresh_data()
                st.rerun()
    with c_note:
        st.caption("Routine inbox clicks stay fast. Use Recheck Groups when older messages need to be regrouped.")

    with st.expander("Operations Inbox Process Feedback", expanded=False):
        st.markdown(
            """
- Treat `Needs Details` as the first-response queue. Ask for booking, container, or reference before creating orders, quotes, cancellations, or POD tasks.
- Ask frequent customers to include one identifier in the email subject line: booking number, container number, or reference number.
- Keep `Waiting` clean by moving items there only after a reply is sent, then close or attach the request when the customer responds.
- Use `Customer Requests` for general status questions and unclear requests so they do not inflate the new order or quote queues.
- Review repeat vague requests weekly and turn the top missing details into a customer intake template.
            """.strip()
        )

    tab_labels = list(DEFAULT_OPERATIONS_QUEUE_ORDER)

    queue_map = {
        "All": inbox_df,
        "Needs Details": inbox_df[needs_details_mask],
        "Customer Requests": inbox_df[inbox_df["request_type_clean"].eq("Customer Request")],
        "New Bookings": inbox_df[inbox_df["request_type_clean"].eq("New Booking")],
        "Booking Updates": inbox_df[inbox_df["request_type_clean"].eq("Booking Update")],
        "Appointments": inbox_df[inbox_df["request_type_clean"].eq("Appointment Update")],
        "Quote Requests": inbox_df[inbox_df["request_type_clean"].eq("Quote Request")],
        "Missing Info": inbox_df[inbox_df["request_type_clean"].eq("Missing Information")],
        "POD Requests": inbox_df[inbox_df["request_type_clean"].eq("POD Request")],
        "Cancellations": inbox_df[inbox_df["request_type_clean"].eq("Cancellation")],
        "Waiting": inbox_df[inbox_df["review_status_clean"].eq("Waiting on Customer")],
        "Needs Review": inbox_df[
            inbox_df["request_type_clean"].isin(["Needs Classification", "Other"])
            | inbox_df["confidence_score"].fillna(0).lt(70)
        ],
        }

    tab_display_cols = {
        "All": [
            "id",
            "email_received",
            "client_name",
            "request_type",
            "review_status",
            "source_subject",
            "pdf_count",
            "matched_load_id",
            "confidence_score",
            "action_required",
        ],

        "Needs Details": [
            "id",
            "email_received",
            "client_name",
            "request_type",
            "source_subject",
            "pdf_count",
            "reference_hint",
            "confidence_score",
            "action_required",
        ],

        "Customer Requests": [
            "id",
            "email_received",
            "client_name",
            "source_subject",
            "pdf_count",
            "reference_hint",
            "matched_load_id",
            "action_required",
        ],

        "New Bookings": [
            "id",
            "email_received",
            "client_name",
            "source_subject",
            "pdf_count",
            "reference_hint",
            "confidence_score",
            "action_required",
        ],

        "Booking Updates": [
            "id",
            "email_received",
            "client_name",
            "source_subject",
            "pdf_count",
            "reference_hint",
            "matched_load_id",
            "confidence_score",
            "action_required",
        ],

        "Appointments": [
            "id",
            "email_received",
            "client_name",
            "reference_hint",
            "requested_time",
            "source_subject",
            "pdf_count",
            "matched_load_id",
            "action_required",
        ],

        "Quote Requests": [
            "id",
            "email_received",
            "client_name",
            "reference_hint",
            "source_subject",
            "pdf_count",
            "confidence_score",
            "action_required",
        ],

        "Missing Info": [
            "id",
            "email_received",
            "client_name",
            "source_subject",
            "pdf_count",
            "matched_load_id",
            "action_required",
        ],

        "POD Requests": [
            "id",
            "email_received",
            "client_name",
            "source_subject",
            "pdf_count",
            "reference_hint",
            "matched_load_id",
            "action_required",
        ],

        "Cancellations": [
            "id",
            "email_received",
            "client_name",
            "source_subject",
            "pdf_count",
            "matched_load_id",
            "confidence_score",
            "action_required",
        ],

        "Waiting": [
            "id",
            "email_received",
            "client_name",
            "source_subject",
            "pdf_count",
            "matched_load_id",
            "review_status",
            "action_required",
        ],

        "Needs Review": [
            "id",
            "email_received",
            "client_name",
            "request_type",
            "source_sender",
            "source_subject",
            "pdf_count",
            "confidence_score",
            "action_required",
        ],
    }


    tab_titles = [
        f"{label} ({len(queue_map.get(label, pd.DataFrame()))})"
        for label in tab_labels
    ]
    queue_tabs = st.tabs(tab_titles)

    for selected_queue, queue_tab in zip(tab_labels, queue_tabs):
        with queue_tab:
            tab_df = queue_map[selected_queue].copy()
            active_display_cols = [
                c for c in tab_display_cols.get(selected_queue, tab_display_cols["All"])
                if c in tab_df.columns
            ]
            if "reference_hint" in active_display_cols:
                blank_reference = tab_df["reference_hint"].fillna("").astype(str).str.strip().eq("")
                if blank_reference.any():
                    preview = tab_df["raw_text_preview"] if "raw_text_preview" in tab_df.columns else ""
                    tab_df.loc[blank_reference, "reference_hint"] = (
                        tab_df.loc[blank_reference, "source_subject"].fillna("")
                        + " "
                        + pd.Series(preview, index=tab_df.index).fillna("")
                    ).apply(extract_reference_from_text)
            if "requested_time" in active_display_cols:
                if "raw_text_preview" in tab_df.columns:
                    tab_df["requested_time"] = tab_df["raw_text_preview"].fillna("").apply(extract_requested_time)
                else:
                    tab_df["requested_time"] = tab_df["source_subject"].fillna("").apply(extract_requested_time)

            st.caption(f"{len(tab_df)} item(s)")

            if tab_df.empty:
                st.info(f"No {selected_queue.lower()} items.")
            else:
                event = st.dataframe(
                    tab_df[active_display_cols],
                    use_container_width=True,
                    hide_index=True,
                    selection_mode="single-row",
                    on_select="rerun",
                    key=f"operations_inbox_table_{selected_queue}",
                )

                selected_rows = event.selection.rows
                if selected_rows:
                    row_id = int(tab_df.iloc[selected_rows[0]]["id"])

                    if st.button(
                        f"Open Request #{row_id}",
                        key=f"open_request_{selected_queue}_{row_id}",
                        use_container_width=True,
                    ):
                        st.session_state["selected_operations_request_id"] = row_id
                        st.session_state["selected_operations_tab"] = selected_queue
                        st.rerun()

    st.divider()

    selected_id = st.session_state.get("selected_operations_request_id")
    selected_tab_name = st.session_state.get("selected_operations_tab")

    if selected_id is None:
        st.info("Select a row, then click Open Request.")
        return

    record_df = _load_operations_inbox_record(int(selected_id))

    if record_df.empty:
        st.warning("Selected request was not found.")
        return

    st.markdown(f"### Review Customer Request - {selected_tab_name} - Request #{selected_id}")
    st.divider()

    record = record_df.iloc[0]
    parsed = _coerce_json_dict(record.get("parsed_data"))

    subject = str(record.get("source_subject", "") or "")
    sender = str(record.get("source_sender", "") or "")
    body = str(record.get("raw_text", "") or "")

    classification = _operations_classification_for_review(
        record,
        parsed,
        subject,
        body,
        fallback_key=f"intake-{selected_id}",
    )
    detected_type = classification["request_type"]
    tokens = classification["tokens"]
    matched_load_id = classification["matched_load_id"]
    confidence = classification["confidence_score"]
    conversation_key = classification["conversation_key"]

    saved_matched_load_id = record.get("matched_load_id")
    if matched_load_id is None and pd.notna(saved_matched_load_id) and _safe_str(saved_matched_load_id):
        try:
            matched_load_id = int(saved_matched_load_id)
            classification["matched_load_id"] = matched_load_id
        except Exception:
            pass

    load_context_key = f"operations_load_context_{selected_id}_{matched_load_id or 'none'}"
    cached_load_context = st.session_state.get(load_context_key) or {}
    load_context = cached_load_context.get("load_context", {})
    load_candidates = cached_load_context.get("load_candidates", [])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Detected Type", detected_type)
    c2.metric("Confidence", f"{confidence}%")
    c3.metric("Matched Load", matched_load_id or "-")
    c4.metric("Conversation", conversation_key)

    saved_request_type = str(record.get("request_type", "") or "").strip()
    default_request_type = saved_request_type if saved_request_type in REQUEST_TYPES else detected_type

    request_type = st.selectbox(
        "Request Type",
        REQUEST_TYPES,
        index=REQUEST_TYPES.index(default_request_type) if default_request_type in REQUEST_TYPES else 0,
    )
    detected_reply_language = _detect_customer_language(subject, body)
    language_default = "Auto"
    reply_language = st.selectbox(
        "Reply Language",
        REPLY_LANGUAGE_OPTIONS,
        index=REPLY_LANGUAGE_OPTIONS.index(language_default),
        help=f"Auto detected: {detected_reply_language}. Choose Spanish or Bilingual for customer-facing replies when needed.",
        key=f"operations_reply_language_{selected_id}",
    )
    resolved_reply_language = _resolve_reply_language(reply_language, subject, body)
    st.caption(f"Reply draft language: {resolved_reply_language}")
    selected_action_required = _action_required_for_request(
        request_type,
        parsed,
        body,
        subject=subject,
        tokens=tokens,
        matched_load_id=matched_load_id,
    )

    with st.expander("Email / Request Body", expanded=True):
        st.write(f"**From:** {sender}")
        st.write(f"**Subject:** {subject}")
        st.text_area("Message", value=body, height=220, disabled=True)

    with st.expander("Parsed Fields", expanded=False):
        st.json(parsed)

    _render_operations_pdf_panel(
        selected_id=int(selected_id),
        record=record,
        parsed=parsed,
        subject=subject,
        sender=sender,
        body=body,
        matched_load_id=matched_load_id,
        conversation_key=conversation_key,
    )

    ai_suggestion_key = f"operations_ai_suggestion_{selected_id}"
    ai_version_key = f"operations_ai_suggestion_version_{selected_id}"
    ai_suggestion = st.session_state.get(ai_suggestion_key)

    with st.expander("AI Assist", expanded=False):
        st.caption("AI suggestions are drafts for dispatcher review. They do not send email or create orders.")
        context_checked = bool(cached_load_context.get("checked"))
        if st.button(
            "Load Match Context",
            key=f"load_ai_context_{selected_id}",
            use_container_width=True,
        ):
            with st.spinner("Checking matching loads and documents..."):
                load_context, load_candidates = _build_ai_load_context(classification, parsed)
                st.session_state[load_context_key] = {
                    "load_context": load_context,
                    "load_candidates": load_candidates,
                    "checked": True,
                }
                context_checked = True

        if load_context:
            st.write("**Matched load context:**")
            st.json(_candidate_summary_from_context(load_context))
        elif load_candidates:
            st.write("**Possible load matches:**")
            st.dataframe(pd.DataFrame(load_candidates), use_container_width=True, hide_index=True)
        elif context_checked:
            st.info("No matching load context found yet. AI will ask for booking, container, or reference details when needed.")
        else:
            st.info("Load matching context is available on demand to keep email opening fast.")

        if not is_operations_ai_configured():
            st.info("Add OPENAI_API_KEY to enable AI classification and reply drafts.")
        else:
            feedback_examples = _recent_operations_ai_feedback_examples()
            if feedback_examples:
                st.caption(f"Learning from {len(feedback_examples)} recent dispatcher feedback example(s).")

            if st.button(
                "Generate AI Suggestion",
                key=f"generate_ai_suggestion_{selected_id}",
                use_container_width=True,
            ):
                with st.spinner("Generating AI classification and reply draft..."):
                    if not context_checked:
                        load_context, load_candidates = _build_ai_load_context(classification, parsed)
                        st.session_state[load_context_key] = {
                            "load_context": load_context,
                            "load_candidates": load_candidates,
                            "checked": True,
                        }
                        context_checked = True
                    ai_suggestion = generate_operations_ai_suggestion(
                        subject=subject,
                        sender=sender,
                        body=body,
                        parsed=parsed,
                        rule_classification=_operations_ai_rule_context(classification, parsed, subject, body),
                        load_context=load_context,
                        load_candidates=load_candidates,
                        feedback_examples=feedback_examples,
                        response_language=resolved_reply_language,
                        company_name=_get_app_setting("COMPANY_NAME", "CaliTrans"),
                    )
                    st.session_state[ai_suggestion_key] = ai_suggestion
                    st.session_state[ai_version_key] = str(datetime.now().timestamp()).replace(".", "_")

            ai_suggestion = st.session_state.get(ai_suggestion_key)
            if ai_suggestion:
                if not ai_suggestion.get("success"):
                    st.warning(f"AI suggestion failed: {ai_suggestion.get('error', 'Unknown error')}")
                else:
                    a1, a2, a3, a4 = st.columns(4)
                    a1.metric("AI Type", ai_suggestion.get("request_type", "-"))
                    a2.metric("AI Confidence", f"{int(ai_suggestion.get('confidence_score', 0) or 0)}%")
                    a3.metric("Priority", ai_suggestion.get("priority", "Normal"))
                    a4.metric("Needs Details", "Yes" if ai_suggestion.get("needs_details") else "No")

                    if ai_suggestion.get("response_language"):
                        st.write(f"**AI reply language:** {ai_suggestion.get('response_language')}")
                    if ai_suggestion.get("suggested_load_id"):
                        st.write(
                            f"**AI matched load:** {ai_suggestion.get('suggested_load_id')} "
                            f"({int(ai_suggestion.get('load_match_confidence', 0) or 0)}% match confidence)"
                        )
                    if ai_suggestion.get("status_summary"):
                        st.write(f"**Status summary:** {ai_suggestion.get('status_summary')}")
                    if ai_suggestion.get("reason"):
                        st.write(f"**Why:** {ai_suggestion.get('reason')}")
                    if ai_suggestion.get("action_required"):
                        st.write(f"**Suggested action:** {ai_suggestion.get('action_required')}")
                    required_details = ai_suggestion.get("required_details") or []
                    if required_details:
                        st.write("**Details to request:** " + ", ".join(required_details))
                    feedback_notes = st.text_area(
                        "Learning Notes",
                        value="",
                        placeholder="Optional: tell AI what was right or what dispatch changed.",
                        height=80,
                        key=f"operations_ai_feedback_notes_{selected_id}",
                    )

                    if st.button(
                        "Apply AI Classification",
                        key=f"apply_ai_classification_{selected_id}",
                        use_container_width=True,
                    ):
                        ai_matched_load_id = _valid_ai_suggested_load_id(ai_suggestion, load_candidates)
                        applied_matched_load_id = ai_matched_load_id if ai_matched_load_id is not None else matched_load_id
                        applied_conversation_key = conversation_key
                        if ai_matched_load_id is not None:
                            for candidate in load_candidates:
                                if _safe_str(candidate.get("Load ID", "")) == str(ai_matched_load_id):
                                    applied_conversation_key = _conversation_key_from_candidate(candidate, conversation_key)
                                    break
                        update_intake_classification(
                            int(selected_id),
                            ai_suggestion.get("request_type", request_type),
                            applied_conversation_key,
                            applied_matched_load_id,
                            int(ai_suggestion.get("confidence_score", confidence) or confidence),
                            ai_suggestion.get("action_required", selected_action_required),
                        )
                        _save_operations_ai_feedback(
                            intake_id=int(selected_id),
                            load_id=applied_matched_load_id,
                            source_subject=subject,
                            source_sender=sender,
                            ai_suggestion=ai_suggestion,
                            final_request_type=ai_suggestion.get("request_type", request_type),
                            final_action_required=ai_suggestion.get("action_required", selected_action_required),
                            correction_type="classification_accepted",
                            feedback_notes=feedback_notes,
                        )
                        st.success("AI classification applied.")
                        refresh_data()
                        st.rerun()

    if st.button("Save Classification", use_container_width=True):
        update_intake_classification(
            int(selected_id),
            request_type,
            conversation_key,
            matched_load_id,
            confidence,
            selected_action_required,
        )
        if ai_suggestion and ai_suggestion.get("success"):
            ai_feedback_notes = _safe_str(st.session_state.get(f"operations_ai_feedback_notes_{selected_id}", ""))
            ai_request_type = _safe_str(ai_suggestion.get("request_type", ""))
            ai_action_required = _safe_str(ai_suggestion.get("action_required", ""))
            if request_type != ai_request_type:
                correction_type = "classification_corrected"
            elif selected_action_required != ai_action_required:
                correction_type = "action_corrected"
            else:
                correction_type = "classification_confirmed"

            _save_operations_ai_feedback(
                intake_id=int(selected_id),
                load_id=matched_load_id,
                source_subject=subject,
                source_sender=sender,
                ai_suggestion=ai_suggestion,
                final_request_type=request_type,
                final_action_required=selected_action_required,
                correction_type=correction_type,
                feedback_notes=ai_feedback_notes,
            )
        st.success("Classification saved.")
        refresh_data()
        st.rerun()

    st.markdown("### Customer Email Reply")
    ai_reply_body = ""
    if ai_suggestion and ai_suggestion.get("success"):
        ai_reply_language = _safe_str(ai_suggestion.get("response_language", ""))
        if not ai_reply_language or ai_reply_language == resolved_reply_language:
            ai_reply_body = _safe_str(ai_suggestion.get("reply_body", ""))
    reply_body_default = ai_reply_body or _default_operations_reply_body(
        request_type,
        parsed,
        matched_load_id,
        subject,
        body,
        reply_language=reply_language,
    )
    reply_key_seed = f"{request_type}_{resolved_reply_language}_{st.session_state.get(ai_version_key, 'rule')}"
    reply_key_suffix = re.sub(r"[^a-z0-9]+", "_", reply_key_seed.lower()).strip("_")
    with st.form(f"operations_email_reply_{selected_id}"):
        reply_to = st.text_input(
            "To",
            value=_extract_email_address(sender),
            key=f"operations_reply_to_{selected_id}",
        )
        reply_subject = st.text_input(
            "Subject",
            value=_default_operations_reply_subject(subject, request_type),
            key=f"operations_reply_subject_{selected_id}",
        )
        reply_body = st.text_area(
            "Message",
            value=reply_body_default,
            height=220,
            key=f"operations_reply_body_{selected_id}_{reply_key_suffix}",
        )
        mark_waiting = st.checkbox(
            "Mark waiting on customer after sending",
            value=True,
            key=f"operations_reply_waiting_{selected_id}",
        )
        send_reply = st.form_submit_button("Send Email Reply")

    if send_reply:
        if not reply_to.strip():
            st.error("Reply recipient is required.")
        elif not reply_subject.strip() or not reply_body.strip():
            st.error("Subject and message are required.")
        else:
            try:
                _send_smtp_email(reply_to.strip(), reply_subject.strip(), reply_body.strip())
                save_operations_email_reply(
                    intake_id=int(selected_id),
                    load_id=matched_load_id,
                    recipient=reply_to.strip(),
                    subject=reply_subject.strip(),
                    body=reply_body.strip(),
                    status="sent",
                )

                if ai_suggestion and ai_suggestion.get("success") and ai_reply_body:
                    normalized_ai_reply = " ".join(ai_reply_body.split())
                    normalized_final_reply = " ".join(reply_body.strip().split())
                    _save_operations_ai_feedback(
                        intake_id=int(selected_id),
                        load_id=matched_load_id,
                        source_subject=subject,
                        source_sender=sender,
                        ai_suggestion=ai_suggestion,
                        final_request_type=request_type,
                        final_action_required=selected_action_required,
                        final_reply_body=reply_body.strip(),
                        correction_type="reply_edited" if normalized_ai_reply != normalized_final_reply else "reply_accepted",
                        feedback_notes=_safe_str(st.session_state.get(f"operations_ai_feedback_notes_{selected_id}", "")),
                    )

                if matched_load_id is not None:
                    save_load_communication(
                        matched_load_id,
                        int(selected_id),
                        conversation_key,
                        request_type,
                        reply_subject.strip(),
                        reply_to.strip(),
                        reply_body.strip(),
                        direction="outbound",
                    )

                if mark_waiting:
                    execute(
                        """
                        update order_intake
                        set review_status = 'Waiting on Customer',
                            action_required = 'Reply sent; waiting on customer response.'
                        where id = :intake_id
                        """,
                        {"intake_id": int(selected_id)},
                    )

                st.success(f"Email sent to {reply_to.strip()}.")
                refresh_data()
                st.rerun()
            except Exception as exc:
                try:
                    save_operations_email_reply(
                        intake_id=int(selected_id),
                        load_id=matched_load_id,
                        recipient=reply_to.strip(),
                        subject=reply_subject.strip(),
                        body=reply_body.strip(),
                        status="failed",
                        error_message=str(exc),
                    )
                except Exception:
                    pass
                st.error(f"Email was not sent: {exc}")

    st.markdown("### Operations Actions")

    message_text = f"{subject or ''} {body or ''}"
    can_create_order = request_type == "New Booking" and _has_new_order_details(message_text, parsed, tokens)
    can_create_quote = request_type == "Quote Request" and _has_quote_details(message_text, parsed, tokens)

    a1, a2, a3, a4, a5 = st.columns(5)

    with a1:
        if st.button("Create New Order", use_container_width=True, disabled=not can_create_order):
            booking = parsed.get("Booking Number") or tokens.get("booking_number")
            customer = parsed.get("Customer") or ""

            if not booking or not customer:
                st.error("Booking Number and Customer are required.")
            else:
                load_id = create_load_from_intake(
                    int(selected_id),
                    {
                        "TYPE": parsed.get("TYPE", "Import") or "Import",
                        "Booking Number": booking,
                        "Reference Number": parsed.get("Reference Number") or tokens.get("reference_number"),
                        "Customer": customer,
                        "Container Number": parsed.get("Container Number") or tokens.get("container_number"),
                        "Port": parsed.get("Port", ""),
                        "Warehouse": parsed.get("Warehouse", ""),
                        "Address": parsed.get("Address", ""),
                        "Document Cutoff": parsed.get("Document Cutoff", ""),
                        "Delivery Need Date": parsed.get("Delivery Need Date", ""),
                        "LFD": parsed.get("LFD", ""),
                        "Size": parsed.get("Size", ""),
                        "Status": "New",
                        "Dispatcher Notes": f"Created from Operations Inbox request #{selected_id}",
                    },
                )

                execute(
                    """
                    update order_intake
                    set review_status = 'Order Created',
                        matched_load_id = :load_id,
                        request_type = 'New Booking',
                        conversation_key = :conversation_key
                    where id = :intake_id
                    """,
                    {
                        "intake_id": int(selected_id),
                        "load_id": load_id,
                        "conversation_key": conversation_key,
                    },
                )

                refresh_data()
                st.success(f"Created new order/load ID {load_id}.")
                st.rerun()

    with a2:
        if st.button("Update Existing Order", use_container_width=True, disabled=matched_load_id is None):
            save_load_communication(
                matched_load_id,
                int(selected_id),
                conversation_key,
                request_type,
                subject,
                sender,
                body,
            )

            execute(
                """
                update order_intake
                set review_status = 'Attached',
                    matched_load_id = :matched_load_id,
                    request_type = :request_type,
                    conversation_key = :conversation_key
                where id = :intake_id
                """,
                {
                    "intake_id": int(selected_id),
                    "matched_load_id": matched_load_id,
                    "request_type": request_type,
                    "conversation_key": conversation_key,
                },
            )

            st.success("Request attached to existing order communication history.")
            st.rerun()

    with a3:
        if st.button("Create Quote", use_container_width=True, disabled=not can_create_quote):
            create_quote_request_from_intake(int(selected_id), parsed, body[:1000])

            execute(
                """
                update order_intake
                set review_status = 'Quote Created',
                    request_type = 'Quote Request',
                    conversation_key = :conversation_key
                where id = :intake_id
                """,
                {
                    "intake_id": int(selected_id),
                    "conversation_key": conversation_key,
                },
            )

            st.success("Quote request created.")
            st.rerun()

    with a4:
        if st.button("Cancel Order", use_container_width=True, disabled=matched_load_id is None):
            execute(
                """
                update loads
                set status = 'Cancelled'
                where id = :load_id
                """,
                {"load_id": matched_load_id},
            )

            execute(
                """
                update order_intake
                set review_status = 'Order Cancelled',
                    matched_load_id = :matched_load_id,
                    request_type = 'Cancellation',
                    conversation_key = :conversation_key
                where id = :intake_id
                """,
                {
                    "intake_id": int(selected_id),
                    "matched_load_id": matched_load_id,
                    "conversation_key": conversation_key,
                },
            )

            refresh_data()
            st.warning("Matched order was cancelled.")
            st.rerun()

    with a5:
        if st.button("Close / No Action", use_container_width=True):
            execute(
                """
                update order_intake
                set review_status = 'Closed'
                where id = :intake_id
                """,
                {"intake_id": int(selected_id)},
            )

            st.info("Request closed.")
            st.rerun()  
def render_booking_review(df: pd.DataFrame) -> None:
    st.markdown("### Booking Review")
    st.caption("Complete missing booking information here. Ready bookings move to Dispatch Board.")

    review_statuses = ["New", "Hold/Need Info", "Awaiting Appointment"]
    review_df = df[df["Status"].isin(review_statuses)].copy()
    review_df = _add_booking_verification_columns(review_df)

    if review_df.empty:
        st.success("No bookings require review.")
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Needs Review", int(review_df["Status"].eq("New").sum()))
    k2.metric("Missing Info", int(review_df["Status"].eq("Hold/Need Info").sum()))
    k3.metric("Awaiting Appointment", int(review_df["Status"].eq("Awaiting Appointment").sum()))
    k4.metric("Ready", int(review_df["Readiness %"].eq(100).sum()))

    q1, q2, q3, q4 = st.tabs(
        ["Needs Review", "Missing Information", "Awaiting Appointment", "All Review"]
    )

    with q1:
        _render_booking_verification_table(
            review_df[review_df["Status"].eq("New")].copy(),
            "Needs Review",
        )

    with q2:
        missing_df = review_df[review_df["Readiness %"].lt(100)].copy()
        _render_booking_verification_table(missing_df, "Missing Information")

    with q3:
        _render_booking_verification_table(
            review_df[review_df["Status"].eq("Awaiting Appointment")].copy(),
            "Awaiting Appointment",
        )

    with q4:
        _render_booking_verification_table(review_df, "All Bookings in Review")

    st.divider()
    st.markdown("### Edit Selected Booking")

    labels = [
        f"{row['Booking Number']} | {row.get('Customer', '')} | {row.get('Readiness %', 0)}% ready | row {int(row['_row_id'])}"
        for _, row in review_df.sort_values("_row_id", ascending=False).iterrows()
    ]

    selected = st.selectbox("Select booking to edit", labels, key="booking_review_selected")
    selected_row_id = int(selected.split("row ")[-1])
    selected_df = review_df[review_df["_row_id"].astype(int).eq(selected_row_id)]

    if selected_df.empty:
        st.warning("Selected booking was not found.")
        return

    selected_load = selected_df.iloc[0]
    readiness_score = int(selected_load.get("Readiness %", 0))
    missing_fields = str(selected_load.get("Missing Fields", "") or "")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Booking", _safe_str(selected_load.get("Booking Number", "")) or "-")
    m2.metric("Customer", _safe_str(selected_load.get("Customer", "")) or "-")
    m3.metric("Readiness", f"{readiness_score}%")
    m4.metric("Status", _safe_str(selected_load.get("Status", "")) or "-")

    if missing_fields:
        st.warning(f"Missing fields: {missing_fields}")
    else:
        st.success("This booking is complete and ready to dispatch.")

    with st.form(f"booking_review_form_{selected_row_id}"):
        c1, c2, c3 = st.columns(3)

        with c1:
            type_val = st.selectbox(
                "TYPE",
                LOAD_TYPE_TABS,
                index=LOAD_TYPE_TABS.index(_safe_str(selected_load.get("TYPE", "")))
                if _safe_str(selected_load.get("TYPE", "")) in LOAD_TYPE_TABS
                else 0,
            )
            booking = st.text_input("Booking Number *", value=_safe_str(selected_load.get("Booking Number", "")))
            load_id = st.text_input("Load ID", value=_safe_str(selected_load.get("Load ID", "")))
            reference = st.text_input("Reference Number", value=_safe_str(selected_load.get("Reference Number", "")))
            customer = st.text_input("Customer *", value=_safe_str(selected_load.get("Customer", "")))
            container = st.text_input("Container Number *", value=_safe_str(selected_load.get("Container Number", "")))

        with c2:
            port = st.text_input("Port / Pickup *", value=_safe_str(selected_load.get("Port", "")))
            warehouse = st.text_input("Warehouse / Delivery *", value=_safe_str(selected_load.get("Warehouse", "")))
            address = st.text_input("Address", value=_safe_str(selected_load.get("Address", "")))
            delivery_need = st.date_input(
                "Delivery Need Date *",
                value=_parse_date_or_none(selected_load.get("Delivery Need Date", "")),
            )
            lfd = st.date_input(
                "LFD",
                value=_parse_date_or_none(selected_load.get("LFD", "")),
            )
            size = st.selectbox(
                "Size",
                ["", "20", "40", "40HC", "40ST", "20FR", "40FR", "20 STRF", "40STRF"],
                index=0,
            )

        with c3:
            status = st.selectbox(
                "Review Status",
                ["New", "Hold/Need Info", "Awaiting Appointment", "Ready to Dispatch"],
                index=["New", "Hold/Need Info", "Awaiting Appointment", "Ready to Dispatch"].index(
                    _safe_str(selected_load.get("Status", "New"))
                )
                if _safe_str(selected_load.get("Status", "New")) in ["New", "Hold/Need Info", "Awaiting Appointment", "Ready to Dispatch"]
                else 0,
            )
            driver = st.text_input("Driver Name", value=_safe_str(selected_load.get("Driver Name", "")))
            truck = st.text_input("Truck Assigned", value=_safe_str(selected_load.get("Truck Assigned", "")))
            chassis = st.text_input("Chassis", value=_safe_str(selected_load.get("Chassis", "")))
            notes = st.text_area(
                "Dispatcher Notes",
                value=_safe_str(selected_load.get("Dispatcher Notes", "")),
                height=165,
            )

        submitted = st.form_submit_button("Save Booking Review")

    if submitted:
        updates = {
            "type": type_val,
            "booking_number": booking.strip(),
            "load_id": load_id.strip(),
            "reference_number": reference.strip(),
            "customer": customer.strip(),
            "container_number": container.strip(),
            "port": port.strip(),
            "warehouse": warehouse.strip(),
            "address": address.strip(),
            "delivery_need_date": delivery_need,
            "lfd": lfd,
            "status": status,
            "driver_name": driver.strip(),
            "truck_assigned": truck.strip(),
            "chassis": chassis.strip(),
            "dispatcher_notes": notes.strip(),
    }
        

        DispatchDatabaseClient().update_row_fields(selected_row_id, updates)
        refresh_data()
        st.success("Booking review saved.")
        st.rerun()

    st.markdown("### Booking Actions")

    a1, a2, a3, a4 = st.columns(4)

    with a1:
        if st.button("Mark Missing Info", key=f"review_missing_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Hold/Need Info",
                    "Dispatcher Notes": missing_fields or "Missing booking information.",
                },
            )
            refresh_data()
            st.warning("Booking marked Hold/Need Info.")
            st.rerun()

    with a2:
        if st.button("Awaiting Appointment", key=f"review_appt_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Awaiting Appointment",
                    "Dispatcher Notes": "Waiting for pickup/delivery appointment confirmation.",
                },
            )
            refresh_data()
            st.warning("Booking marked Awaiting Appointment.")
            st.rerun()

    with a3:
        ready_disabled = readiness_score < 100
        if st.button(
            "Move To Dispatch",
            key=f"review_ready_dispatch_{selected_row_id}",
            use_container_width=True,
            disabled=ready_disabled,
        ):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Ready to Dispatch",
                    "Dispatcher Notes": "Booking completed and moved to dispatch.",
                },
            )
            refresh_data()
            st.success("Booking moved to Dispatch Board.")
            st.rerun()

    with a4:
        if st.button("Cancel Booking", key=f"review_cancel_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Status": "Cancelled"},
            )
            refresh_data()
            st.error("Booking cancelled.")
            st.rerun()

    if readiness_score < 100:
        st.info("Move To Dispatch is disabled until all required fields are complete.")

def _get_selected_dispatch_load(df: pd.DataFrame):
    selected_id = st.session_state.get("selected_dispatch_load_id")

    if selected_id is None and not df.empty:
        selected_id = int(df.iloc[0]["_row_id"])
        st.session_state["selected_dispatch_load_id"] = selected_id

    if selected_id is None:
        return None

    selected_df = df[df["_row_id"].astype(int).eq(int(selected_id))]
    if selected_df.empty:
        return None

    return selected_df.iloc[0]


def _read_status_timeline(load_id: int) -> pd.DataFrame:
    try:
        return read_df(
            """
            select old_status, new_status, notes, created_by, created_at
            from status_events
            where load_id = :load_id
            order by created_at desc
            """,
            {"load_id": load_id},
        )
    except Exception:
        return pd.DataFrame()


def _read_dispatch_messages(load_id: int) -> pd.DataFrame:
    try:
        return read_df(
            """
            select message_type, direction, recipient, message_body, sent_by, created_at
            from dispatch_messages
            where load_id = :load_id
            order by created_at desc
            """,
            {"load_id": load_id},
        )
    except Exception:
        return pd.DataFrame()


def _read_documents_for_load(load_id: int) -> pd.DataFrame:
    try:
        return read_df(
            """
            select document_type, filename, file_path, source, created_at
            from documents
            where load_id = :load_id
            order by created_at desc
            """,
            {"load_id": load_id},
        )
    except Exception:
        return pd.DataFrame()


def _update_load_extra_fields(load_id: int, current_location: str, eta_value, live_load_status: str, live_unload_status: str) -> None:
    execute(
        """
        update loads
        set current_location = :current_location,
            eta = :eta,
            live_load_status = :live_load_status,
            live_unload_status = :live_unload_status,
            last_driver_update = now()
        where id = :load_id
        """,
        {
            "load_id": load_id,
            "current_location": current_location or None,
            "eta": eta_value or None,
            "live_load_status": live_load_status or None,
            "live_unload_status": live_unload_status or None,
        },
    )


def _insert_dispatch_message(load_id: int, message_type: str, direction: str, recipient: str, message_body: str) -> None:
    execute(
        """
        insert into dispatch_messages (load_id, message_type, direction, recipient, message_body, sent_by)
        values (:load_id, :message_type, :direction, :recipient, :message_body, 'dispatcher')
        """,
        {
            "load_id": load_id,
            "message_type": message_type,
            "direction": direction,
            "recipient": recipient or None,
            "message_body": message_body,
        },
    )



def _get_app_setting(name: str, default=None):
    """Read from Streamlit secrets first, then environment variables."""
    try:
        value = st.secrets.get(name)
        if value not in [None, ""]:
            return value
    except Exception:
        pass
    return os.getenv(name, default)


def _get_first_app_setting(names: list[str], default=None):
    for name in names:
        value = _get_app_setting(name)
        if value not in [None, ""]:
            return value
    return default


def _first_present(load, keys: list[str], fallback: str = "") -> str:
    for key in keys:
        try:
            value = load.get(key, "")
        except Exception:
            value = ""
        value_str = str(value or "").strip()
        if value_str and value_str.lower() not in {"nan", "none", "nat", "null", "-"}:
            return value_str
    return fallback


def _customer_email_for_load(load) -> str:
    return _first_present(
        load,
        ["Customer Email", "Contact Email", "customer_email", "contact_email", "Email", "email"],
        "",
    )


def _next_status_goal(new_status: str) -> str:
    flow = LOAD_STATUS_FLOW
    if new_status in flow:
        idx = flow.index(new_status)
        if idx + 1 < len(flow):
            return flow[idx + 1]
    return "Next dispatch milestone"


def _eta_to_next_goal(load, new_status: str) -> str:
    eta_value = _first_present(load, ["eta", "ETA"], "")
    if eta_value:
        return eta_value

    if new_status in ["Assigned", "En Route to Pickup", "At Pickup"]:
        eta_value = _first_present(load, ["pickup_appointment", "Pickup Appointment", "Delivery Need Date"], "")
        if eta_value:
            return eta_value

    if new_status in ["Loaded", "En Route To Delivery", "Delivered"]:
        eta_value = _first_present(load, ["delivery_appointment", "Delivery Appointment", "Delivery Need Date"], "")
        if eta_value:
            return eta_value

    if new_status == "Returning Empty":
        eta_value = _first_present(load, ["empty_return_date", "Empty Return Date", "LFD"], "")
        if eta_value:
            return eta_value

    return "ETA pending dispatch update"


def _build_customer_status_email(load, old_status: str, new_status: str, note: str = "") -> tuple[str, str]:
    company_name = _get_app_setting("COMPANY_NAME", "CaliTrans")
    booking = _first_present(load, ["Booking Number", "booking_number"], "-")
    load_ref = _first_present(load, ["Load ID", "id", "_row_id"], "-")
    customer = _first_present(load, ["Customer", "customer"], "Customer")
    container = _first_present(load, ["Container Number", "container_number", "Reference Number"], "-")
    move_type = _first_present(load, ["TYPE", "type"], "-")
    pickup = _first_present(load, ["Port", "terminal", "pickup_location"], "-")
    delivery = _first_present(load, ["Warehouse", "Address", "delivery_location"], "-")
    driver = _first_present(load, ["Driver Name", "driver_name"], "Pending")
    truck = _first_present(load, ["Truck Assigned", "truck_assigned"], "Pending")
    chassis = _first_present(load, ["Chassis", "chassis"], "-")
    lfd = _first_present(load, ["LFD", "lfd"], "-")
    current_location = _first_present(load, ["current_location", "Current Location"], "-")
    public_notes = _first_present(load, ["Public Notes", "public_notes"], "")
    dispatcher_notes = note or public_notes
    next_goal = _next_status_goal(new_status)
    eta = _eta_to_next_goal(load, new_status)

    subject = f"{company_name} Load Update | {booking} | {new_status}"

    body = f"""
Hello {customer},

Your load status has been updated.

STATUS UPDATE
Previous Status: {old_status or '-'}
Current Status: {new_status}
Next Goal: {next_goal}
ETA to Next Goal: {eta}

LOAD DETAILS
Load ID: {load_ref}
Move Type: {move_type}
Booking Number: {booking}
Container / Reference: {container}
LFD: {lfd}

ROUTE
Pickup / Port: {pickup}
Delivery / Warehouse: {delivery}
Current Location: {current_location}

DISPATCH DETAILS
Driver: {driver}
Truck: {truck}
Chassis: {chassis}

NOTES
{dispatcher_notes if dispatcher_notes else 'No additional notes at this time.'}

Thank you,
{company_name} Dispatch
""".strip()
    return subject, body


def _log_customer_email_notification(load_id: int, old_status: str, new_status: str, recipient: str, subject: str, body: str, status: str, error_message: str = "") -> None:
    """Log customer status emails. Safe no-op if the table has not been created yet."""
    try:
        execute(
            """
            insert into email_notifications
                (load_id, old_status, new_status, sent_to, subject, body, status, error_message, sent_at)
            values
                (:load_id, :old_status, :new_status, :sent_to, :subject, :body, :status, :error_message,
                 case when :status = 'sent' then now() else null end)
            """,
            {
                "load_id": load_id,
                "old_status": old_status,
                "new_status": new_status,
                "sent_to": recipient or None,
                "subject": subject,
                "body": body,
                "status": status,
                "error_message": error_message or None,
            },
        )
    except Exception:
        pass


def _send_smtp_email(to_email: str, subject: str, body: str) -> None:
    smtp_host = _get_app_setting("SMTP_HOST", "smtp.mail.yahoo.com")
    smtp_port = int(_get_app_setting("SMTP_PORT", 465))
    smtp_user = _get_first_app_setting(["SMTP_USER", "YAHOO_EMAIL", "EMAIL_ADDRESS"])
    smtp_password = _get_first_app_setting(["SMTP_PASSWORD", "YAHOO_APP_PASSWORD", "EMAIL_APP_PASSWORD"])
    dispatch_email = _get_first_app_setting(["DISPATCH_EMAIL", "YAHOO_EMAIL", "EMAIL_ADDRESS"], smtp_user)

    if not to_email:
        raise ValueError("Missing customer email address on this load.")
    if not smtp_host or not smtp_user or not smtp_password:
        raise ValueError("Missing email settings. Add YAHOO_EMAIL and YAHOO_APP_PASSWORD, or SMTP_HOST, SMTP_USER, and SMTP_PASSWORD.")

    msg = MIMEMultipart()
    msg["From"] = dispatch_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

def _clean_display_value(value, fallback: str = "-") -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", ""}:
        return fallback
    return value_str


def _generate_driver_dispatch_message(selected_load) -> str:
    booking = _clean_display_value(selected_load.get("Booking Number", ""))
    container = _clean_display_value(selected_load.get("Container Number", ""))
    customer = _clean_display_value(selected_load.get("Customer", ""))
    pickup = _clean_display_value(selected_load.get("Port", ""))
    delivery = _clean_display_value(selected_load.get("Warehouse", ""))
    address = _clean_display_value(selected_load.get("Address", ""))
    delivery_need = _clean_display_value(selected_load.get("Delivery Need Date", ""))
    lfd = _clean_display_value(selected_load.get("LFD", ""))
    chassis = _clean_display_value(selected_load.get("Chassis", ""))
    size = _clean_display_value(selected_load.get("Size", ""))
    notes = _clean_display_value(selected_load.get("Dispatcher Notes", ""), "")

    message = f"""LOAD ASSIGNMENT

Booking: {booking}
Container: {container}
Customer: {customer}
Size: {size}

Pickup / Terminal:
{pickup}

Delivery:
{delivery}
{address}

Delivery Need Date: {delivery_need}
LFD: {lfd}
Chassis: {chassis}

Instructions:
{notes if notes else "Please confirm when en route, at pickup, loaded, delivered, and empty returned."}
"""
    return message.strip()


def _save_status_quick_update(load_id: int, selected_load, new_status: str, note: str) -> tuple[bool, str]:
    old_status = str(selected_load.get("Status", "") or "")

    DispatchDatabaseClient().update_row_fields(
        load_id,
        {
            "Status": new_status,
            "Dispatcher Notes": note,
        },
    )
    _insert_dispatch_message(
        load_id,
        "driver_status_quick_update",
        "internal",
        "dispatcher",
        f"Quick status update: {new_status}. {note}",
    )

    return _send_customer_status_update_email(load_id, selected_load, old_status, new_status, note)

def render_dispatch_workspace(selected_load) -> None:
    load_id = int(selected_load["_row_id"])
    booking = str(selected_load.get("Booking Number", "") or "")
    container = str(selected_load.get("Container Number", "") or "-")
    customer = str(selected_load.get("Customer", "") or "-")

    st.markdown("---")
    st.markdown(f"## Load Workspace: {booking}")
    st.caption(f"{customer} · Container {container}")

    top = st.columns(5)
    top[0].metric("Status", str(selected_load.get("Status", "") or "-"))
    top[1].metric("Driver", str(selected_load.get("Driver Name", "") or "Unassigned"))
    top[2].metric("Truck", str(selected_load.get("Truck Assigned", "") or "-"))
    top[3].metric("Delivery Need", str(selected_load.get("Delivery Need Date", "") or "-"))
    top[4].metric("LFD", str(selected_load.get("LFD", "") or "-"))

    dispatch_tab, status_tab, timeline_tab, driver_tab, customer_tab, docs_tab, billing_tab = st.tabs(
        ["Dispatch Details", "Status Update", "Timeline", "Driver Notes/Text", "Customer Notes", "Documents", "Billing"]
    )

    with dispatch_tab:
        st.markdown("### Dispatch Progress Details")
        c1, c2 = st.columns(2)

        with c1:
            st.write("**Start / Pickup Point**")
            st.info(str(selected_load.get("Port", "") or "Not set"))
            st.write("**Delivery / Final Point**")
            st.info(str(selected_load.get("Warehouse", "") or "Not set"))
            st.write("**Address**")
            st.info(str(selected_load.get("Address", "") or "Not set"))

        with c2:
            current_location = st.text_input(
                "Current Location",
                value=str(selected_load.get("current_location", "") or ""),
                placeholder="Example: Bayport Terminal, I-10 East, Baytown DC...",
                key=f"current_location_{load_id}",
            )

            eta_date = st.date_input("ETA Date", value=None, key=f"eta_date_{load_id}")
            eta_time = st.time_input("ETA Time", value=None, key=f"eta_time_{load_id}")

            live_load_status = st.selectbox(
                "Live Load Status",
                ["", "Not Started", "Waiting", "In Progress", "Completed", "Issue / Delay"],
                index=0,
                key=f"live_load_{load_id}",
            )

            live_unload_status = st.selectbox(
                "Live Unload Status",
                ["", "Not Started", "Waiting", "In Progress", "Completed", "Issue / Delay"],
                index=0,
                key=f"live_unload_{load_id}",
            )

        eta_value = None
        if eta_date and eta_time:
            eta_value = pd.Timestamp.combine(eta_date, eta_time).to_pydatetime()

        if st.button("Save Dispatch Progress", key=f"save_dispatch_progress_{load_id}"):
            _update_load_extra_fields(load_id, current_location, eta_value, live_load_status, live_unload_status)
            st.success("Dispatch progress saved.")
            refresh_data()
            st.rerun()

    with status_tab:
        st.markdown("### Status Update")
        c1, c2, c3, c4 = st.columns(4)
        current_status = str(selected_load.get("Status", "") or "New")
        status_index = LOAD_STATUS_FLOW.index(current_status) if current_status in LOAD_STATUS_FLOW else 0

        new_status = c1.selectbox("New Status", LOAD_STATUS_FLOW, index=status_index)
        driver = c2.text_input("Driver Name", value=str(selected_load.get("Driver Name", "") or ""))
        truck = c3.text_input("Truck Assigned", value=str(selected_load.get("Truck Assigned", "") or ""))
        chassis = c4.text_input("Chassis", value=str(selected_load.get("Chassis", "") or ""))
        customer_email = st.text_input(
            "Customer Email",
            value=str(selected_load.get("Customer Email", "") or ""),
            key=f"customer_email_{load_id}",
    )

        note = st.text_area("Status / Dispatch Note", value=str(selected_load.get("Dispatcher Notes", "") or ""), height=120)

        if st.button("Save Status Update", key=f"save_status_{load_id}"):
            updates = {}
            if new_status != current_status:
                updates["Status"] = new_status
            if driver.strip() != str(selected_load.get("Driver Name", "") or "").strip():
                updates["Driver Name"] = driver.strip()
            if truck.strip() != str(selected_load.get("Truck Assigned", "") or "").strip():
                updates["Truck Assigned"] = truck.strip()
            if chassis.strip() != str(selected_load.get("Chassis", "") or "").strip():
                updates["Chassis"] = chassis.strip()
            if note.strip() != str(selected_load.get("Dispatcher Notes", "") or "").strip():
                updates["Dispatcher Notes"] = note.strip()
           

            if updates:
                DispatchDatabaseClient().update_row_fields(load_id, updates)

                if "Status" in updates:
                    email_sent, email_msg = _send_customer_status_update_email(
                        load_id,
                        selected_load,
                        current_status,
                        new_status,
                        note.strip(),
                        customer_email.strip(),
                    )

                    if email_sent:
                        st.success(f"Status updated. {email_msg}")
                    else:
                        st.warning(f"Status updated, but customer email was not sent: {email_msg}")
                else:
                    st.success("Load details updated.")

                refresh_data()
                st.rerun()
            else:
                st.info("No changes detected.")

    with timeline_tab:
        st.markdown("### Load Timeline")
        timeline = _read_status_timeline(load_id)
        if timeline.empty:
            st.info("No timeline records yet.")
        else:
            st.dataframe(timeline, use_container_width=True, hide_index=True)

    with driver_tab:
        st.markdown("### Driver Communication Center")
        st.caption(
            "Generate dispatch instructions, save driver messages, and record quick driver status updates. "
            "SMS/Motive sending can be connected later through FastAPI."
        )

        load_id = int(selected_load["_row_id"])
        current_status = _clean_display_value(selected_load.get("Status", ""), "New")
        driver_name = _clean_display_value(selected_load.get("Driver Name", ""), "Unassigned")
        truck = _clean_display_value(selected_load.get("Truck Assigned", ""), "-")
        chassis = _clean_display_value(selected_load.get("Chassis", ""), "-")
        booking = _clean_display_value(selected_load.get("Booking Number", ""), "-")
        container = _clean_display_value(selected_load.get("Container Number", ""), "-")

        st.markdown("#### Driver Assignment")
        info_cols = st.columns(5)
        info_cols[0].metric("Driver", driver_name)
        info_cols[1].metric("Truck", truck)
        info_cols[2].metric("Chassis", chassis)
        info_cols[3].metric("Status", current_status)
        info_cols[4].metric("Container", container)

        st.markdown("#### Generated Dispatch Message")

        generated_message = _generate_driver_dispatch_message(selected_load)

        edited_message = st.text_area(
            "Dispatch Message",
            value=generated_message,
            height=260,
            key=f"generated_dispatch_msg_{load_id}",
        )

        action_cols = st.columns(4)

        with action_cols[0]:
            if st.button("Save Message", key=f"save_generated_driver_msg_{load_id}", use_container_width=True):
                _insert_dispatch_message(
                    load_id,
                    "driver_dispatch_message",
                    "outbound",
                    driver_name,
                    edited_message.strip(),
                )
                st.success("Driver dispatch message saved to history.")
                st.rerun()

        with action_cols[1]:
            st.download_button(
                "Download Message",
                data=edited_message,
                file_name=f"dispatch_message_{booking}.txt",
                mime="text/plain",
                key=f"download_dispatch_msg_{load_id}",
                use_container_width=True,
            )

        with action_cols[2]:
            if st.button("Copy/Paste Ready", key=f"copy_ready_{load_id}", use_container_width=True):
                _insert_dispatch_message(
                    load_id,
                    "driver_dispatch_message_copy_ready",
                    "outbound",
                    driver_name,
                    edited_message.strip(),
                )
                st.info("Message saved. Copy the text above and paste into Motive.")

        with action_cols[3]:
            st.button(
                "Send via Motive",
                key=f"send_motive_placeholder_{load_id}",
                disabled=True,
                use_container_width=True,
                help="Future FastAPI + Motive integration",
            )

        st.markdown("#### Quick Driver Status Updates")
        st.caption("These buttons update load status and create a communication log entry.")

        quick_statuses = [
            ("En Route to Pickup", "Driver en route to pickup/terminal."),
            ("At Pickup", "Driver arrived at pickup/terminal."),
            ("Loaded", "Container/load picked up and loaded."),
            ("En Route To Delivery", "Driver en route to delivery."),
            ("Delivered", "Delivery completed. Awaiting POD if not received."),
            ("Returning Empty", "Driver returning empty container/chassis."),
            ("POD Received", "POD received and saved for billing."),
        ]

        quick_cols = st.columns(4)
        for idx, (status_label, default_note) in enumerate(quick_statuses):
            with quick_cols[idx % 4]:
                if st.button(status_label, key=f"quick_status_{load_id}_{status_label}", use_container_width=True):
                    email_sent, email_msg = _save_status_quick_update(load_id, selected_load, status_label, default_note)
                    refresh_data()
                    if email_sent:
                        st.success(f"Updated to {status_label}. {email_msg}")
                    else:
                        st.warning(f"Updated to {status_label}, but customer email was not sent: {email_msg}")
                    st.rerun()

        st.markdown("#### Manual Driver Note / Message")
        manual_cols = st.columns([1, 2])
        recipient = manual_cols[0].text_input(
            "Driver / Phone",
            value=driver_name,
            key=f"driver_recipient_{load_id}",
        )
        message_body = manual_cols[1].text_area(
            "Message / Note",
            placeholder="Example: Confirm container released. Send ETA when loaded.",
            height=120,
            key=f"manual_driver_msg_{load_id}",
        )

        msg_cols = st.columns(3)
        with msg_cols[0]:
            message_type = st.selectbox(
                "Message Type",
                ["driver_note", "driver_message", "driver_reply_log", "motive_message_log"],
                key=f"driver_msg_type_{load_id}",
            )

        with msg_cols[1]:
            direction = st.selectbox(
                "Direction",
                ["outbound", "inbound", "internal"],
                key=f"driver_msg_direction_{load_id}",
            )

        with msg_cols[2]:
            st.write("")
            st.write("")
            save_manual = st.button("Save Driver Communication", key=f"save_manual_driver_msg_{load_id}")

        if save_manual:
            if not message_body.strip():
                st.error("Message is required.")
            else:
                _insert_dispatch_message(
                    load_id,
                    message_type,
                    direction,
                    recipient,
                    message_body.strip(),
                )
                st.success("Driver communication saved.")
                st.rerun()

        st.markdown("#### Driver Communication Thread")
        messages = _read_dispatch_messages(load_id)

        if messages.empty:
            st.info("No driver messages have been saved yet.")
        else:
            driver_messages = messages[
                messages["message_type"].astype(str).str.contains("driver|motive", case=False, na=False)
            ].copy()

            if driver_messages.empty:
                st.info("No driver-specific messages have been saved yet.")
            else:
                display_cols = [
                    "created_at",
                    "direction",
                    "message_type",
                    "recipient",
                    "message_body",
                    "sent_by",
                ]
                display_cols = [c for c in display_cols if c in driver_messages.columns]
                st.dataframe(driver_messages[display_cols], use_container_width=True, hide_index=True)

    with customer_tab:
        st.markdown("### Customer Notes / Updates")
        customer_note = st.text_area("Customer Update Note", placeholder="Example: Container picked up. ETA to warehouse 2:30 PM.", height=100)
        if st.button("Save Customer Note", key=f"save_customer_note_{load_id}"):
            if not customer_note.strip():
                st.error("Customer note is required.")
            else:
                _insert_dispatch_message(load_id, "customer_note", "outbound", customer, customer_note.strip())
                st.success("Customer note saved.")
                st.rerun()

        messages = _read_dispatch_messages(load_id)
        customer_messages = messages[messages["message_type"].astype(str).str.contains("customer", case=False, na=False)] if not messages.empty else pd.DataFrame()
        st.dataframe(customer_messages, use_container_width=True, hide_index=True)

    with docs_tab:
        st.markdown("### Documents")
        docs = _read_documents_for_load(load_id)
        st.dataframe(docs, use_container_width=True, hide_index=True)
        uploaded = st.file_uploader("Attach document to this load", type=["pdf", "png", "jpg", "jpeg"], key=f"doc_upload_{load_id}")
        if st.button("Attach Document", key=f"attach_doc_{load_id}") and uploaded is not None:
            DispatchDatabaseClient().attach_file_to_row(load_id, uploaded, source="dispatch_workspace")
            st.success("Document attached.")
            st.rerun()

    with billing_tab:
        st.markdown("### Billing Readiness")
        st.write("**Billing Notes**")
        st.info(str(selected_load.get("Billing Notes", "") or "No billing notes."))
        billing_status = str(selected_load.get("Status", "") or "")
        if billing_status in ["POD Received", "Ready for ProfitTools", "Exported to ProfitTools", "Invoiced", "Closed"]:
            st.success("This load is in the billing workflow.")
        else:
            st.warning("This load is not ready for billing yet.")

        if st.button("Mark Ready for ProfitTools", key=f"mark_billing_{load_id}"):
            old_status = str(selected_load.get("Status", "") or "")
            new_status = "Ready for ProfitTools"
            DispatchDatabaseClient().update_row_fields(load_id, {"Status": new_status})
            email_sent, email_msg = _send_customer_status_update_email(
                load_id,
                selected_load,
                old_status,
                new_status,
                "Load is ready for billing/export review.",
            )
            if email_sent:
                st.success(f"Marked Ready for ProfitTools. {email_msg}")
            else:
                st.warning(f"Marked Ready for ProfitTools, but customer email was not sent: {email_msg}")
            refresh_data()
            st.rerun()
def _send_customer_status_update_email(
    load_id: int,
    original_load,
    old_status: str,
    new_status: str,
    note: str = "",
    recipient_override: str = "",
) -> tuple[bool, str]:
    """Send a customer email only when status actually changes."""
    if old_status == new_status:
        return True, "Status unchanged; no customer email needed."

    updated_load = original_load.copy()
    updated_load["Status"] = new_status
    if note:
        updated_load["Dispatcher Notes"] = note

    recipient = recipient_override.strip() or _customer_email_for_load(updated_load)

    subject, body = _build_customer_status_email(updated_load, old_status, new_status, note)

    try:
        _send_smtp_email(recipient, subject, body)
        _log_customer_email_notification(load_id, old_status, new_status, recipient, subject, body, "sent")
        _insert_dispatch_message(load_id, "customer_status_email", "outbound", recipient, body)
        return True, f"Customer email sent to {recipient}."
    except Exception as exc:
        _log_customer_email_notification(load_id, old_status, new_status, recipient, subject, body, "failed", str(exc))
        return False, str(exc)

@st.dialog("Selected Load Workspace", width="large")
def open_load_workspace_dialog(selected_load):
    render_dispatch_workspace(selected_load)
def render_dispatch_board(df: pd.DataFrame) -> None:
    st.subheader("Dispatch Board")
    st.caption("Live Dispatch, Tomorrow Planning, and Future Pipeline.")

    board_df = df.copy()

    board_df["Delivery Date Parsed"] = pd.to_datetime(
        board_df["Delivery Need Date"].astype(str).str.strip(),
        errors="coerce",
    )

    today = pd.Timestamp(date.today()).normalize()
    tomorrow = today + pd.Timedelta(days=1)

    live_df = board_df[
        board_df["Delivery Date Parsed"].dt.normalize().eq(today)
        & board_df["Status"].isin(DISPATCH_BOARD_STATUSES)
    ].copy()

    tomorrow_df = board_df[
        board_df["Delivery Date Parsed"].dt.normalize().eq(tomorrow)
        & ~board_df["Status"].isin(["Closed", "Cancelled", "Invoiced"])
    ].copy()

    future_df = board_df[
        board_df["Delivery Date Parsed"].dt.normalize().gt(tomorrow)
        & ~board_df["Status"].isin(["Closed", "Cancelled", "Invoiced"])
    ].copy()

    main_tabs = st.tabs(["Live Dispatch", "Tomorrow Planning", "Future Pipeline"])

    with main_tabs[0]:
        st.markdown("### Live Dispatch")

        type_tabs = st.tabs(LOAD_TYPE_TABS)

        for type_tab, type_value in zip(type_tabs, LOAD_TYPE_TABS):
            with type_tab:
                type_df = live_df[
                    live_df["TYPE"].astype(str).str.strip().eq(type_value)
                ].copy()

                st.markdown(f"#### {type_value}")
                st.caption(f"{len(type_df)} active load(s) today")

                status_cols = st.columns(len(DISPATCH_BOARD_STATUSES), gap="small")

                for idx, status in enumerate(DISPATCH_BOARD_STATUSES):
                    with status_cols[idx]:
                        status_df = type_df[
                            type_df["Status"].astype(str).str.strip().eq(status)
                        ].copy()

                        st.markdown(
                            f"""
                            <div style="
                                text-align:center;
                                font-weight:800;
                                background:#f1f5f9;
                                border:1px solid #cbd5e1;
                                border-radius:10px;
                                padding:8px;
                                margin-bottom:8px;
                            ">
                                {status}<br>
                                <span style="font-size:18px;">{len(status_df)}</span>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                        if status_df.empty:
                            st.caption("No loads")
                        else:
                            for _, row in status_df.head(30).iterrows():
                                render_load_card(row)

    with main_tabs[1]:
        st.markdown("### Tomorrow Planning")

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Tomorrow Loads", len(tomorrow_df))
        k2.metric("Assigned", int(tomorrow_df["Driver Name"].astype(str).str.strip().ne("").sum()))
        k3.metric("Unassigned", int(tomorrow_df["Driver Name"].astype(str).str.strip().isin(["", "nan", "None", "Unassigned"]).sum()))
        k4.metric("Needs Info", int(tomorrow_df["Status"].eq("Hold/Need Info").sum()))

        type_tabs = st.tabs(LOAD_TYPE_TABS)

        for type_tab, type_value in zip(type_tabs, LOAD_TYPE_TABS):
            with type_tab:
                type_df = tomorrow_df[
                    tomorrow_df["TYPE"].astype(str).str.strip().eq(type_value)
                ].copy()

                st.markdown(f"#### {type_value} — Tomorrow")
                st.caption(f"{len(type_df)} planned load(s)")

                if type_df.empty:
                    st.info(f"No {type_value} loads planned for tomorrow.")
                    continue

                columns = [
                    "_row_id",
                    "TYPE",
                    "Booking Number",
                    "Load ID",
                    "Customer",
                    "Container Number",
                    "Warehouse",
                    "Delivery Need Date",
                    "LFD",
                    "Status",
                    "Driver Name",
                    "Truck Assigned",
                    "Chassis",
                    "Dispatcher Notes",
                ]

                display_cols = [c for c in columns if c in type_df.columns]

                styled = (
                    type_df.sort_values(["Status", "Delivery Need Date"], ascending=[True, True])[display_cols]
                    .style
                    .apply(_status_row_style, axis=1)
                )

                st.dataframe(styled, use_container_width=True, hide_index=True)

    with main_tabs[2]:
        st.markdown("### Future Pipeline")

        type_tabs = st.tabs(LOAD_TYPE_TABS)

        for type_tab, type_value in zip(type_tabs, LOAD_TYPE_TABS):
            with type_tab:
                type_df = future_df[
                    future_df["TYPE"].astype(str).str.strip().eq(type_value)
                ].copy()

                st.markdown(f"#### {type_value} — Future")
                st.caption(f"{len(type_df)} upcoming load(s)")

                if type_df.empty:
                    st.info(f"No future {type_value} loads found.")
                    continue

                columns = [
                    "_row_id",
                    "TYPE",
                    "Booking Number",
                    "Load ID",
                    "Customer",
                    "Container Number",
                    "Port",
                    "Warehouse",
                    "Delivery Need Date",
                    "LFD",
                    "Status",
                    "Driver Name",
                    "Dispatcher Notes",
                ]

                display_cols = [c for c in columns if c in type_df.columns]

                st.dataframe(
                    type_df.sort_values("Delivery Need Date")[display_cols],
                    use_container_width=True,
                    hide_index=True,
                )

    if st.session_state.get("show_load_workspace_dialog"):
        selected_load = _get_selected_dispatch_load(df)

        if selected_load is not None:
            open_load_workspace_dialog(selected_load)


def render_dispatch_board_focused(df: pd.DataFrame) -> None:
    st.subheader("Dispatch Board")
    st.caption("Live dispatch, tomorrow planning, and future pipeline.")

    board_df = df.copy()
    board_df["Delivery Date Parsed"] = pd.to_datetime(
        board_df["Delivery Need Date"].astype(str).str.strip(),
        errors="coerce",
    )

    today = pd.Timestamp(date.today()).normalize()
    tomorrow = today + pd.Timedelta(days=1)

    live_df = board_df[
        board_df["Delivery Date Parsed"].dt.normalize().eq(today)
        & board_df["Status"].isin(DISPATCH_BOARD_STATUSES)
    ].copy()
    tomorrow_df = board_df[
        board_df["Delivery Date Parsed"].dt.normalize().eq(tomorrow)
        & ~board_df["Status"].isin(["Closed", "Cancelled", "Invoiced"])
    ].copy()
    future_df = board_df[
        board_df["Delivery Date Parsed"].dt.normalize().gt(tomorrow)
        & ~board_df["Status"].isin(["Closed", "Cancelled", "Invoiced"])
    ].copy()

    selected_view = st.radio(
        "Dispatch View",
        ["Live Dispatch", "Tomorrow Planning", "Future Pipeline"],
        horizontal=True,
        key="dispatch_board_view",
    )
    type_value = st.radio(
        "Load Type",
        LOAD_TYPE_TABS,
        horizontal=True,
        key=f"dispatch_board_type_{selected_view}",
    )

    if selected_view == "Live Dispatch":
        type_df = live_df[live_df["TYPE"].astype(str).str.strip().eq(type_value)].copy()
        st.markdown("### Live Dispatch")
        st.caption(f"{len(type_df)} active {type_value} load(s) today")

        status_cols = st.columns(len(DISPATCH_BOARD_STATUSES), gap="small")
        for idx, status in enumerate(DISPATCH_BOARD_STATUSES):
            with status_cols[idx]:
                status_df = type_df[type_df["Status"].astype(str).str.strip().eq(status)].copy()
                st.markdown(
                    f"""
                    <div style="
                        text-align:center;
                        font-weight:800;
                        background:#f1f5f9;
                        border:1px solid #cbd5e1;
                        border-radius:10px;
                        padding:8px;
                        margin-bottom:8px;
                    ">
                        {status}<br>
                        <span style="font-size:18px;">{len(status_df)}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if status_df.empty:
                    st.caption("No loads")
                else:
                    for _, row in status_df.head(30).iterrows():
                        render_load_card(row)

    elif selected_view == "Tomorrow Planning":
        type_df = tomorrow_df[tomorrow_df["TYPE"].astype(str).str.strip().eq(type_value)].copy()
        st.markdown("### Tomorrow Planning")

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Tomorrow Loads", len(tomorrow_df))
        k2.metric("Assigned", int(tomorrow_df["Driver Name"].astype(str).str.strip().ne("").sum()))
        k3.metric("Unassigned", int(tomorrow_df["Driver Name"].astype(str).str.strip().isin(["", "nan", "None", "Unassigned"]).sum()))
        k4.metric("Needs Info", int(tomorrow_df["Status"].eq("Hold/Need Info").sum()))

        st.markdown(f"#### {type_value} - Tomorrow")
        st.caption(f"{len(type_df)} planned load(s)")
        if type_df.empty:
            st.info(f"No {type_value} loads planned for tomorrow.")
        else:
            columns = [
                "_row_id",
                "TYPE",
                "Booking Number",
                "Load ID",
                "Customer",
                "Container Number",
                "Warehouse",
                "Delivery Need Date",
                "LFD",
                "Status",
                "Driver Name",
                "Truck Assigned",
                "Chassis",
                "Dispatcher Notes",
            ]
            display_cols = [c for c in columns if c in type_df.columns]
            styled = (
                type_df.sort_values(["Status", "Delivery Need Date"], ascending=[True, True])[display_cols]
                .style
                .apply(_status_row_style, axis=1)
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)

    else:
        type_df = future_df[future_df["TYPE"].astype(str).str.strip().eq(type_value)].copy()
        st.markdown("### Future Pipeline")
        st.markdown(f"#### {type_value} - Future")
        st.caption(f"{len(type_df)} upcoming load(s)")
        if type_df.empty:
            st.info(f"No future {type_value} loads found.")
        else:
            columns = [
                "_row_id",
                "TYPE",
                "Booking Number",
                "Load ID",
                "Customer",
                "Container Number",
                "Port",
                "Warehouse",
                "Delivery Need Date",
                "LFD",
                "Status",
                "Driver Name",
                "Dispatcher Notes",
            ]
            display_cols = [c for c in columns if c in type_df.columns]
            st.dataframe(
                type_df.sort_values("Delivery Need Date")[display_cols],
                use_container_width=True,
                hide_index=True,
            )

    if st.session_state.get("show_load_workspace_dialog"):
        selected_load = _get_selected_dispatch_load(df)
        if selected_load is not None:
            open_load_workspace_dialog(selected_load)


def _render_order_detail_editor(work_df: pd.DataFrame, selected_row_id: int, context_key: str) -> None:
    selected_df = work_df[work_df["_row_id"].astype(int).eq(int(selected_row_id))]

    if selected_df.empty:
        st.warning("Selected order was not found.")
        return

    selected_load = selected_df.iloc[0]
    safe_context = re.sub(r"[^A-Za-z0-9_]+", "_", context_key)
    form_key = f"order_detail_editor_{safe_context}_{selected_row_id}"

    header_cols = st.columns([4, 1])
    with header_cols[0]:
        st.markdown("### Order Detail Editor")
        st.caption(
            f"Editing: {selected_load.get('Booking Number', '')} | "
            f"{selected_load.get('Customer', '')} | row {selected_row_id}"
        )
    with header_cols[1]:
        if st.button("Clear Editor", key=f"clear_order_editor_{safe_context}_{selected_row_id}", use_container_width=True):
            st.session_state.pop("orders_management_selected_row_id", None)
            st.session_state.pop("orders_management_selected_context", None)
            st.rerun()

    with st.form(form_key):
        c1, c2, c3 = st.columns(3)

        with c1:
            type_val = st.selectbox(
                "TYPE",
                LOAD_TYPE_TABS,
                index=LOAD_TYPE_TABS.index(_safe_str(selected_load.get("TYPE", "")))
                if _safe_str(selected_load.get("TYPE", "")) in LOAD_TYPE_TABS else 0,
                key=f"{form_key}_type",
            )
            booking = st.text_input("Booking Number", value=_safe_str(selected_load.get("Booking Number", "")), key=f"{form_key}_booking")
            load_id = st.text_input("Load ID", value=_safe_str(selected_load.get("Load ID", "")), key=f"{form_key}_load_id")
            reference = st.text_input("Reference Number", value=_safe_str(selected_load.get("Reference Number", "")), key=f"{form_key}_reference")
            customer = st.text_input("Customer", value=_safe_str(selected_load.get("Customer", "")), key=f"{form_key}_customer")
            container = st.text_input("Container Number", value=_safe_str(selected_load.get("Container Number", "")), key=f"{form_key}_container")

        with c2:
            port = st.text_input("Port / Pickup", value=_safe_str(selected_load.get("Port", "")), key=f"{form_key}_port")
            warehouse = st.text_input("Warehouse / Delivery", value=_safe_str(selected_load.get("Warehouse", "")), key=f"{form_key}_warehouse")
            address = st.text_input("Address", value=_safe_str(selected_load.get("Address", "")), key=f"{form_key}_address")
            delivery_need = st.date_input(
                "Delivery Need Date",
                value=_parse_date_or_none(selected_load.get("Delivery Need Date", "")),
                key=f"{form_key}_delivery_need",
            )
            lfd = st.date_input(
                "LFD",
                value=_parse_date_or_none(selected_load.get("LFD", "")),
                key=f"{form_key}_lfd",
            )

        with c3:
            status = st.selectbox(
                "Status",
                LOAD_STATUS_FLOW,
                index=LOAD_STATUS_FLOW.index(_safe_str(selected_load.get("Status", "New")))
                if _safe_str(selected_load.get("Status", "New")) in LOAD_STATUS_FLOW else 0,
                key=f"{form_key}_status",
            )
            driver = st.text_input("Driver Name", value=_safe_str(selected_load.get("Driver Name", "")), key=f"{form_key}_driver")
            truck = st.text_input("Truck Assigned", value=_safe_str(selected_load.get("Truck Assigned", "")), key=f"{form_key}_truck")
            chassis = st.text_input("Chassis", value=_safe_str(selected_load.get("Chassis", "")), key=f"{form_key}_chassis")
            notes = st.text_area(
                "Dispatcher Notes",
                value=_safe_str(selected_load.get("Dispatcher Notes", "")),
                height=135,
                key=f"{form_key}_notes",
            )

        save_order = st.form_submit_button("Save Order Updates")

    if save_order:
        updates = {
            "type": type_val,
            "booking_number": booking.strip(),
            "load_id": load_id.strip(),
            "reference_number": reference.strip(),
            "customer": customer.strip(),
            "container_number": container.strip(),
            "port": port.strip(),
            "warehouse": warehouse.strip(),
            "address": address.strip(),
            "delivery_need_date": delivery_need,
            "lfd": lfd,
            "status": status,
            "driver_name": driver.strip(),
            "truck_assigned": truck.strip(),
            "chassis": chassis.strip(),
            "dispatcher_notes": notes.strip(),
        }

        DispatchDatabaseClient().update_row_fields(selected_row_id, updates)
        st.session_state.pop("orders_management_selected_row_id", None)
        st.session_state.pop("orders_management_selected_context", None)
        refresh_data()
        st.success("Order updated successfully.")
        st.rerun()

    st.markdown("#### Quick Actions")
    q1, q2, q3 = st.columns(3)
    with q1:
        if st.button("Mark Missing Info", key=f"quick_missing_info_{safe_context}_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Hold/Need Info",
                    "Dispatcher Notes": notes.strip() or "Missing information requested from customer.",
                },
            )
            st.session_state.pop("orders_management_selected_row_id", None)
            st.session_state.pop("orders_management_selected_context", None)
            refresh_data()
            st.warning("Order marked Hold/Need Info.")
            st.rerun()
    with q2:
        if st.button("Move To Dispatch", key=f"quick_ready_dispatch_{safe_context}_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Ready to Dispatch",
                    "Dispatcher Notes": notes.strip() or "Order reviewed and moved to dispatch.",
                },
            )
            st.session_state.pop("orders_management_selected_row_id", None)
            st.session_state.pop("orders_management_selected_context", None)
            refresh_data()
            st.success("Order moved to Dispatch Board.")
            st.rerun()
    with q3:
        if st.button("Cancel Order", key=f"quick_cancel_order_{safe_context}_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Status": "Cancelled"},
            )
            st.session_state.pop("orders_management_selected_row_id", None)
            st.session_state.pop("orders_management_selected_context", None)
            refresh_data()
            st.error("Order cancelled.")
            st.rerun()


def render_orders_management(df: pd.DataFrame) -> None:
    st.subheader("Orders / Load Management")
    st.caption("Manage orders after they are created from Operations Inbox.")

    work_df = df.copy()

    planning_statuses = ["New", "Hold/Need Info", "Awaiting Appointment"]
    ready_statuses = ["Ready to Dispatch"]
    active_statuses = [
        "Assigned", "En Route to Pickup", "At Pickup",
        "Loaded", "En Route To Delivery", "Returning Empty",
    ]
    closed_statuses = [
        "Delivered", "POD Received", "Ready for ProfitTools",
        "Exported to ProfitTools", "Invoiced", "Closed", "Cancelled",
    ]

    planning_df = work_df[work_df["Status"].isin(planning_statuses)].copy()
    ready_df = work_df[work_df["Status"].isin(ready_statuses)].copy()
    active_df = work_df[work_df["Status"].isin(active_statuses)].copy()
    closed_df = work_df[work_df["Status"].isin(closed_statuses)].copy()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Planning", len(planning_df))
    k2.metric("Ready for Dispatch", len(ready_df))
    k3.metric("Active", len(active_df))
    k4.metric("Closed / Billing", len(closed_df))

    columns = [
        "_row_id", "TYPE", "Booking Number", "Load ID", "Customer",
        "Container Number", "Port", "Warehouse", "Delivery Need Date",
        "LFD", "Status", "Driver Name", "Truck Assigned",
        "Chassis", "Dispatcher Notes",
    ]

    def clear_order_editor() -> None:
        st.session_state.pop("orders_management_selected_row_id", None)
        st.session_state.pop("orders_management_selected_context", None)

    def render_clickable_order_table(table_df: pd.DataFrame, title: str):
        st.markdown(f"### {title}")
        st.caption(f"{len(table_df)} order(s)")

        if table_df.empty:
            st.info(f"No {title.lower()} orders.")
            return

        type_key = f"orders_management_type_{re.sub(r'[^A-Za-z0-9_]+', '_', title)}"
        type_value = st.radio("Load Type", LOAD_TYPE_TABS, horizontal=True, key=type_key)
        last_type_key = f"{type_key}_last"
        if st.session_state.get(last_type_key) != type_value:
            st.session_state[last_type_key] = type_value
            clear_order_editor()

        type_df = table_df[
            table_df["TYPE"].astype(str).str.strip().eq(type_value)
        ].copy()

        st.markdown(f"#### {type_value}")
        st.caption(f"{len(type_df)} order(s)")

        if type_df.empty:
            st.info(f"No {type_value} orders.")
            return

        display_cols = [c for c in columns if c in type_df.columns]
        sorted_type_df = type_df.sort_values("_row_id", ascending=False)
        context_key = f"{title}_{type_value}"

        event = st.dataframe(
            sorted_type_df[display_cols],
            use_container_width=True,
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
            key=f"orders_table_{title}_{type_value}",
        )

        selected_rows = event.selection.rows

        if selected_rows:
            selected_row_id = int(sorted_type_df.iloc[selected_rows[0]]["_row_id"])
            st.session_state["orders_management_selected_row_id"] = selected_row_id
            st.session_state["orders_management_selected_context"] = context_key

        selected_context = st.session_state.get("orders_management_selected_context")
        selected_row_id = st.session_state.get("orders_management_selected_row_id")

        if selected_context == context_key and selected_row_id is not None:
            visible_ids = set(sorted_type_df["_row_id"].dropna().astype(int).tolist())
            if int(selected_row_id) in visible_ids:
                st.divider()
                _render_order_detail_editor(work_df, int(selected_row_id), context_key)

    queue_options = [
        "Planning",
        "Ready for Dispatch",
        "Active Orders",
        "Closed / Billing",
    ]
    queue_map = {
        "Planning": planning_df,
        "Ready for Dispatch": ready_df,
        "Active Orders": active_df,
        "Closed / Billing": closed_df,
    }

    selected_queue = st.radio("Order Queue", queue_options, horizontal=True, key="orders_management_queue")
    if st.session_state.get("orders_management_last_queue") != selected_queue:
        st.session_state["orders_management_last_queue"] = selected_queue
        clear_order_editor()

    render_clickable_order_table(queue_map[selected_queue], selected_queue)

    st.caption("Select any order row to edit it under that queue. Changing queue or load type clears the previous editor.")
def render_billing(df: pd.DataFrame) -> None:
    st.subheader("Billing / ProfitTools")

    ready = df[df["Status"].isin(["POD Received", "Ready for ProfitTools", "Exported to ProfitTools", "Invoiced"])]
    st.dataframe(
        ready[[c for c in ["Booking Number", "Customer", "Container Number", "Status", "Billing Notes", "Rate"] if c in ready.columns]],
        use_container_width=True,
        hide_index=True,
    )

    if st.button("Generate ProfitTools Ready CSV"):
        path = export_ready_loads(df)
        st.success(f"Export created: {path}")
        st.download_button(
            "Download CSV",
            data=Path(path).read_bytes(),
            file_name=Path(path).name,
            mime="text/csv",
        )


def render_documents(df: pd.DataFrame) -> None:
    st.subheader("Documents")

    try:
        docs = read_df(
            """
            select
                d.id,
                l.booking_number,
                l.container_number,
                d.document_type,
                d.filename,
                d.file_path,
                d.source,
                d.created_at
            from documents d
            left join loads l on l.id = d.load_id
            order by d.created_at desc
            """
        )
    except Exception as exc:
        st.error(f"Could not load documents: {exc}")
        return

    st.dataframe(docs, use_container_width=True, hide_index=True)

    with st.expander("Upload document to load", expanded=False):
        labels = [
            f"{row['Booking Number']} | {row.get('Container Number', '')} | row {int(row['_row_id'])}"
            for _, row in df.iterrows()
        ]
        selected = st.selectbox("Select load", labels)
        row_id = int(selected.split("row ")[-1])
        doc_type = st.selectbox("Document Type", ["load_order", "rate_confirmation", "bol", "pod", "invoice", "other"])
        uploaded = st.file_uploader("Upload PDF or image", type=["pdf", "png", "jpg", "jpeg"])

        if st.button("Attach Document") and uploaded is not None:
            DispatchDatabaseClient().attach_file_to_row(row_id, uploaded, source=doc_type)
            st.success("Document attached.")
            st.rerun()


def render_booking_detail(df: pd.DataFrame, booking: str) -> None:
    booking = unquote(booking)
    booking_df = df[df["Booking Number"].astype(str).str.strip() == booking.strip()].copy()

    if booking_df.empty:
        st.error("Booking not found.")
        if st.button("Back"):
            st.query_params.clear()
            st.rerun()
        return

    st.title(f"Booking {booking}")
    st.caption("Load timeline, dispatch details, documents, and billing readiness")

    first = booking_df.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Customer", str(first.get("Customer", "")))
    c2.metric("Loads", len(booking_df))
    c3.metric("Status", str(first.get("Status", "")))
    c4.metric("Warehouse", str(first.get("Warehouse", "")))

    st.markdown("### Load Details")

    detail_cols = [
        "_row_id",
        "TYPE",
        "Load ID",
        "Container Number",
        "Status",
        "Driver Name",
        "Truck Assigned",
        "Chassis",
        "Delivery Need Date",
        "LFD",
        "Dispatcher Notes",
        "Billing Notes",
    ]
    detail_cols = [c for c in detail_cols if c in booking_df.columns]

    edited = st.data_editor(
        booking_df[detail_cols],
        hide_index=True,
        use_container_width=True,
        disabled=[c for c in detail_cols if c not in ["Status", "Driver Name", "Truck Assigned", "Chassis", "Dispatcher Notes"]],
        column_config={
            "Status": st.column_config.SelectboxColumn("Status", options=LOAD_STATUS_FLOW),
        },
    )

    if st.button("Save Booking Updates"):
        for i in range(len(edited)):
            row_id = int(booking_df.iloc[i]["_row_id"])
            updates = {}
            for col in ["Status", "Driver Name", "Truck Assigned", "Chassis", "Dispatcher Notes"]:
                if col in edited.columns and edited.iloc[i][col] != booking_df.iloc[i][col]:
                    updates[col] = edited.iloc[i][col]
            if updates:
                DispatchDatabaseClient().update_row_fields(row_id, updates)
                if "Status" in updates:
                    _send_customer_status_update_email(
                        row_id,
                        booking_df.iloc[i],
                        str(booking_df.iloc[i].get("Status", "") or ""),
                        str(updates["Status"] or ""),
                        str(updates.get("Dispatcher Notes", booking_df.iloc[i].get("Dispatcher Notes", "")) or ""),
                    )
        refresh_data()
        st.success("Booking updated.")
        st.rerun()

    st.markdown("### Status Timeline")
    row_ids = booking_df["_row_id"].dropna().astype(int).tolist()
    if row_ids:
        timeline = read_df(
            """
            select old_status, new_status, notes, created_by, created_at
            from status_events
            where load_id = any(:ids)
            order by created_at desc
            """,
            {"ids": row_ids},
        )
        st.dataframe(timeline, use_container_width=True, hide_index=True)

    st.markdown("### Documents")
    docs = read_df(
        """
        select filename, document_type, file_path, source, created_at
        from documents
        where load_id = any(:ids)
        order by created_at desc
        """,
        {"ids": row_ids},
    )
    st.dataframe(docs, use_container_width=True, hide_index=True)

    if st.button("Back to TMS"):
        st.query_params.clear()
        st.rerun()


def render_pdf_intake() -> None:
    st.subheader("PDF / Order Intake")

    uploaded_file = st.file_uploader("Upload load order PDF", type=["pdf"])

    if uploaded_file is not None:
        pdf_text = extract_text_from_pdf(uploaded_file)
        parsed = parse_order_text(pdf_text)

        st.markdown("### Parsed Order")
        st.json(parsed)

        if st.button("Create Load From Parsed PDF"):
            created = DispatchDatabaseClient().add_row(
                {
                    "TYPE": parsed.get("TYPE", "Import"),
                    "Booking Number": parsed.get("Booking Number", ""),
                    "Reference Number": parsed.get("Reference Number", ""),
                    "Customer": parsed.get("Customer", ""),
                    "Container Number": parsed.get("Container Number", ""),
                    "Port": parsed.get("Port", ""),
                    "Warehouse": parsed.get("Warehouse", ""),
                    "Document Cutoff": normalize_date(parsed.get("Document Cutoff", "")),
                    "Delivery Need Date": normalize_date(parsed.get("Delivery Need Date", "")),
                    "Status": parsed.get("Status", "New") or "New",
                    "Dispatcher Notes": parsed.get("Dispatcher Notes", ""),
                }
            )
            DispatchDatabaseClient().attach_file_to_row(created.id, uploaded_file, source="pdf_intake")
            refresh_data()
            st.success(f"Created load ID {created.id}")
            
def render_email_imports():
    st.subheader("Email Imports")

    try:
        imports = read_df("""
            select
                gmail_message_id,
                subject,
                sender,
                received_at,
                pdf_filename,
                parsed_status,
                created_load_id,
                created_at
            from email_imports
            order by created_at desc
        """)

        st.dataframe(
            imports,
            use_container_width=True,
            hide_index=True
        )

    except Exception as e:
        st.error(f"Could not load email imports: {e}")
def render_calendar_view(df: pd.DataFrame) -> None:
    st.subheader("Dispatch Calendar View")
    st.caption("Monthly calendar showing Booking and Customer by Delivery Need Date.")

    calendar_df = df.copy()

    calendar_df["Delivery Need Date Parsed"] = pd.to_datetime(
        calendar_df["Delivery Need Date"].astype(str).str.strip(),
        errors="coerce"
    )

    calendar_df = calendar_df[
        calendar_df["Delivery Need Date Parsed"].notna()
    ].copy()

    if calendar_df.empty:
        st.warning("No loads have valid Delivery Need Dates.")
        return

    selected_month = st.date_input(
        "Select Month",
        value=date.today()
    )

    month_start = pd.Timestamp(selected_month).replace(day=1)
    month_end = month_start + pd.offsets.MonthEnd(1)

    month_df = calendar_df[
        calendar_df["Delivery Need Date Parsed"].between(month_start, month_end)
    ].copy()

    st.markdown(f"### {month_start.strftime('%B %Y')}")

    days = pd.date_range(month_start, month_end, freq="D")
    first_weekday = month_start.weekday()  # Monday = 0
    calendar_slots = [None] * first_weekday + list(days)

    while len(calendar_slots) % 7 != 0:
        calendar_slots.append(None)

    weekday_cols = st.columns(7)
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for col, day_name in zip(weekday_cols, weekdays):
        col.markdown(f"**{day_name}**")

    for week_start in range(0, len(calendar_slots), 7):
        cols = st.columns(7)

        for col, day_value in zip(cols, calendar_slots[week_start:week_start + 7]):
            if day_value is None:
                col.markdown(
                    """
                    <div style="min-height:130px; border:1px solid #e5e7eb; border-radius:10px; background:#f8fafc;"></div>
                    """,
                    unsafe_allow_html=True,
                )
                continue

            day_loads = month_df[
                month_df["Delivery Need Date Parsed"].dt.date.eq(day_value.date())
            ]

            load_html = ""

            for _, row in day_loads.iterrows():
                booking = str(row.get("Booking Number", "") or "-")
                customer = str(row.get("Customer", "") or "-")
                status = str(row.get("Status", "") or "")
                color = _get_status_border_color(status)

                load_html += f"""
                <div style="
                    margin-top:6px;
                    padding:6px;
                    border-left:4px solid {color};
                    background:white;
                    border-radius:7px;
                    font-size:11px;
                    line-height:1.2;
                ">
                    <b>{booking}</b><br>
                    {customer}
                </div>
                """

            col.markdown(
                f"""
                <div style="
                    min-height:130px;
                    border:1px solid #e5e7eb;
                    border-radius:10px;
                    background:#f8fafc;
                    padding:8px;
                    margin-bottom:8px;
                ">
                    <div style="font-weight:700; font-size:13px;">
                        {day_value.day}
                    </div>
                    {load_html}
                </div>
                """,
                unsafe_allow_html=True,
            )     
PORT_HOUSTON_ENDPOINTS = {
    "Container / Unit": {
        "endpoint": "/inventory/units",
        "fields": UNIT_FIELDS,
        "hint": "Container availability, yard position, facility, line, routing, and visit state.",
    },
    "Booking": {
        "endpoint": "/orders/bookings",
        "fields": BOOKING_FIELDS,
        "hint": "Booking changes, line, vessel visit, equipment, quantity, and tally status.",
    },
    "Vessel Visit": {
        "endpoint": "/vessel/vesselvisits",
        "fields": VESSEL_FIELDS,
        "hint": "Vessel ETA/ETD, begin receive, cargo cutoff, empty pickup, and first availability.",
    },
    "Gate Appointments": {"endpoint": "/road/gateappointments", "fields": "", "hint": "Existing appointment visibility."},
    "Appointment Time Slots": {"endpoint": "/road/appointmenttimeslots", "fields": "", "hint": "Available appointment windows."},
    "Gate Transactions": {"endpoint": "/road/gatetransactions", "fields": "", "hint": "Ingate/outgate, trouble status, and gate stages."},
    "Truck Visits": {"endpoint": "/road/truckvisits", "fields": "", "hint": "Truck visit status."},
    "Service Events": {"endpoint": "/service/events", "fields": "", "hint": "Operational event history."},
}

PORT_HOUSTON_SUBSCRIPTION_EVENTS = [
    "Unit",
    "Booking",
    "GateAppointment",
    "TruckTransaction",
    "TruckVisit",
    "TruckVisitAppointment",
    "MoveEvent",
    "ServiceOrder",
    "VesselVisit",
    "VesselBerthing",
    "AppointmentTimeSlot",
    "AppointmentQuotaRule",
]

PORT_HOUSTON_APPOINTMENT_TRAN_TYPES = {
    "Deliver Import": "DI",
    "Deliver Empty": "DM",
    "Deliver Chassis": "DC",
    "Deliver Export": "DE",
    "Receive Export": "RE",
    "Receive Empty": "RM",
}


def _ensure_port_houston_sync_log_table() -> None:
    execute(
        """
        create table if not exists port_houston_sync_log (
            id bigserial primary key,
            load_id bigint references loads(id) on delete set null,
            action_type text not null,
            lookup_type text,
            request_reference text,
            response_summary jsonb,
            status text not null default 'success',
            error_message text,
            created_by text not null default 'streamlit',
            created_at timestamptz not null default now()
        )
        """
    )


def _log_port_houston_event(
    *,
    action_type: str,
    lookup_type: str = "",
    request_reference: str = "",
    response_summary: dict | None = None,
    load_id=None,
    status: str = "success",
    error_message: str = "",
) -> None:
    try:
        _ensure_port_houston_sync_log_table()
        execute(
            """
            insert into port_houston_sync_log (
                load_id,
                action_type,
                lookup_type,
                request_reference,
                response_summary,
                status,
                error_message
            )
            values (
                :load_id,
                :action_type,
                :lookup_type,
                :request_reference,
                cast(:response_summary as jsonb),
                :status,
                :error_message
            )
            """,
            {
                "load_id": int(load_id) if load_id not in [None, ""] else None,
                "action_type": action_type,
                "lookup_type": lookup_type or None,
                "request_reference": request_reference or None,
                "response_summary": _json_dump(response_summary or {}),
                "status": status,
                "error_message": error_message or None,
            },
        )
    except Exception:
        pass


def _redacted_config_value(value: str) -> str:
    value = _safe_str(value)
    if not value:
        return "Not set"
    if len(value) <= 10:
        return "Set"
    return f"{value[:4]}...{value[-4:]}"


def _get_port_houston_client_or_none() -> PortHoustonClient | None:
    try:
        return PortHoustonClient()
    except PortHoustonError as exc:
        st.warning(str(exc))
        return None


def _port_houston_records_df(records: list[dict], mode: str = "flat") -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    rows = [summarize_unit(record) for record in records] if mode == "unit" else [flatten_record(record) for record in records]
    return pd.DataFrame(rows)


def _store_port_houston_result(key: str, data, lookup_type: str, reference: str, load_id=None) -> None:
    records = content_records(data)
    st.session_state[key] = {
        "data": data,
        "records": records,
        "lookup_type": lookup_type,
        "reference": reference,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    _log_port_houston_event(
        action_type="lookup",
        lookup_type=lookup_type,
        request_reference=reference,
        response_summary={"record_count": len(records)},
        load_id=load_id,
    )


def _render_port_houston_result(key: str, mode: str = "flat") -> list[dict]:
    result = st.session_state.get(key)
    if not result:
        return []

    records = result.get("records") or []
    st.caption(f"Last checked: {result.get('checked_at', '')} | {len(records)} record(s)")
    result_df = _port_houston_records_df(records, mode=mode)
    if not result_df.empty:
        st.dataframe(result_df, use_container_width=True, hide_index=True)
    with st.expander("Raw API Response", expanded=False):
        st.json(result.get("data", {}))
    return records


def _port_houston_load_label(row) -> str:
    booking = _safe_str(row.get("Booking Number", "")) or "No booking"
    container = _safe_str(row.get("Container Number", "")) or "No container"
    customer = _safe_str(row.get("Customer", "")) or "No customer"
    row_id = _safe_str(row.get("_row_id", ""))
    return f"{booking} | {container} | {customer} | row {row_id}"


def _port_houston_load_options(df: pd.DataFrame) -> list[dict]:
    if df.empty or "_row_id" not in df.columns:
        return []
    active_df = df[~df["Status"].isin(["Closed", "Cancelled", "Invoiced"])].copy() if "Status" in df.columns else df.copy()
    return [row.to_dict() for _, row in active_df.sort_values("_row_id", ascending=False).head(250).iterrows()]


def _append_port_houston_notes(existing: str, summary: dict) -> str:
    lines = ["Port Houston EVP update:"]
    for key, value in summary.items():
        if _safe_str(value):
            lines.append(f"{key}: {value}")
    note = "\n".join(lines)
    existing = _safe_str(existing)
    return note if not existing else f"{existing}\n\n{note}"


def _updates_from_port_houston_unit(load_row: dict, unit_record: dict) -> dict:
    summary = summarize_unit(unit_record)
    updates = {}
    if _safe_str(summary.get("Container", "")) and not _safe_str(load_row.get("Container Number", "")):
        updates["Container Number"] = summary["Container"]
    if _safe_str(summary.get("Size", "")) and not _safe_str(load_row.get("Size", "")):
        updates["Size"] = summary["Size"]
    if _safe_str(summary.get("Facility", "")) and not _safe_str(load_row.get("Port", "")):
        updates["Port"] = summary["Facility"]
    updates["Dispatcher Notes"] = _append_port_houston_notes(load_row.get("Dispatcher Notes", ""), summary)
    return updates


def _updates_from_port_houston_booking(load_row: dict, booking_record: dict) -> dict:
    updates = {}
    booking = _safe_str(booking_record.get("nbr", ""))
    if booking and not _safe_str(load_row.get("Booking Number", "")):
        updates["Booking Number"] = booking
    client_ref = _safe_str(booking_record.get("clientRefNo", ""))
    if client_ref and not _safe_str(load_row.get("Reference Number", "")):
        updates["Reference Number"] = client_ref
    if _safe_str(booking_record.get("destination", "")) and not _safe_str(load_row.get("Warehouse", "")):
        updates["Warehouse"] = _safe_str(booking_record.get("destination", ""))

    first_item = {}
    items = booking_record.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        first_item = items[0]
    size = " ".join(
        [
            part
            for part in [
                _safe_str(first_item.get("eqSize", "")),
                _safe_str(first_item.get("eqHeight", "")),
                _safe_str(first_item.get("eqIsoGroup", "")),
            ]
            if part
        ]
    )
    if size and not _safe_str(load_row.get("Size", "")):
        updates["Size"] = size

    summary = {
        "Booking": booking,
        "Line": booking_record.get("lineId", ""),
        "Visit": get_nested(booking_record, "visit.visitId"),
        "POL": booking_record.get("polId", ""),
        "POD": booking_record.get("pod1Id", ""),
        "Earliest": booking_record.get("earliestDate", ""),
        "Latest": booking_record.get("latestDate", ""),
        "Quantity": booking_record.get("quantity", ""),
        "Tally": booking_record.get("tally", ""),
    }
    updates["Dispatcher Notes"] = _append_port_houston_notes(load_row.get("Dispatcher Notes", ""), summary)
    return updates


def _apply_port_houston_updates(load_id: int, updates: dict, action_type: str) -> None:
    if updates:
        DispatchDatabaseClient().update_row_fields(load_id, updates)
        _log_port_houston_event(
            action_type=action_type,
            load_id=load_id,
            response_summary={"updated_fields": list(updates.keys())},
        )


def _xml_escape(value) -> str:
    text = _safe_str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _build_port_houston_appointment_payload(
    *,
    action: str,
    appointment_nbr: str,
    appointment_date,
    appointment_time: str,
    gate_id: str,
    truck_license: str,
    trucking_co_id: str,
    tran_type: str,
    container: str,
    booking: str,
    chassis: str,
    equipment_type: str,
    owns_chassis: bool,
) -> str:
    appointment_date_text = appointment_date.strftime("%Y-%m-%d") if hasattr(appointment_date, "strftime") else _safe_str(appointment_date)
    chassis_owner_text = "true" if owns_chassis else "false"
    action_tag = {"Create": "create-appointment", "Update": "update-appointment", "Cancel": "cancel-appointment"}.get(action, "create-appointment")
    lines = ["<gate>", f"  <{action_tag}>"]
    if action in ["Update", "Cancel"]:
        lines.append(f"    <appointment-nbr>{_xml_escape(appointment_nbr)}</appointment-nbr>")
    if action != "Cancel":
        lines.extend(
            [
                f"    <appointment-date>{_xml_escape(appointment_date_text)}</appointment-date>",
                f"    <appointment-time>{_xml_escape(appointment_time)}</appointment-time>",
                f"    <gate-id>{_xml_escape(gate_id)}</gate-id>",
                f"    <truck license-nbr=\"{_xml_escape(truck_license)}\" trucking-co-id=\"{_xml_escape(trucking_co_id)}\" />",
                f"    <tran-type>{_xml_escape(tran_type)}</tran-type>",
            ]
        )
        if booking:
            lines.append(
                f"    <eq-order order-nbr=\"{_xml_escape(booking)}\"><eq-order-items>"
                f"<eq-order-item type=\"{_xml_escape(equipment_type)}\" />"
                f"</eq-order-items></eq-order>"
            )
        if container:
            container_attr = f"eqid=\"{_xml_escape(container)}\"" if tran_type in ["DI", "DE", "RE"] else f"type=\"{_xml_escape(equipment_type)}\""
            lines.append(f"    <container {container_attr} />")
        if chassis:
            lines.append(f"    <chassis eqid=\"{_xml_escape(chassis)}\" is-owners=\"{chassis_owner_text}\" />")
        elif tran_type == "DC":
            lines.append(f"    <chassis type=\"{_xml_escape(equipment_type)}\" />")
    lines.extend([f"  </{action_tag}>", "</gate>"])
    return "\n".join(lines)


def _render_port_houston_setup() -> None:
    settings = get_port_houston_settings()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Configured", "Yes" if settings.is_configured else "No")
    c2.metric("Operator", settings.operator or "-")
    c3.metric("API Base", "Set" if settings.base_url else "Missing")
    c4.metric("Timeout", f"{settings.timeout_seconds}s")

    if settings.missing:
        st.warning("Missing settings: " + ", ".join(settings.missing))
        st.caption("Add these to `.env` or Streamlit secrets. Do not put Port Houston credentials in source code.")
    else:
        st.success("Port Houston credentials are available from local settings.")

    with st.expander("Connection Settings", expanded=False):
        st.write(
            {
                "PORT_HOUSTON_BASE_URL": settings.base_url,
                "PORT_HOUSTON_AUTH_URL": settings.auth_url,
                "PORT_HOUSTON_CLIENT_ID": _redacted_config_value(settings.client_id),
                "PORT_HOUSTON_CLIENT_SECRET": _redacted_config_value(settings.client_secret),
                "PORT_HOUSTON_OPERATOR": settings.operator,
            }
        )

    if st.button("Test Port Houston Connection", use_container_width=True, disabled=not settings.is_configured):
        client = _get_port_houston_client_or_none()
        if client:
            try:
                client.get_token(force_refresh=True)
                _log_port_houston_event(action_type="token_test")
                st.success("Connection test passed. Token was received and cached for this session.")
            except Exception as exc:
                _log_port_houston_event(action_type="token_test", status="failed", error_message=str(exc))
                st.error(f"Connection test failed: {exc}")


def _render_port_houston_selected_load(df: pd.DataFrame) -> None:
    st.markdown("#### Load Lookup and Sync")
    st.caption("Pull Port Houston unit or booking data for a TMS load and update safe fields/notes.")
    load_options = _port_houston_load_options(df)
    if not load_options:
        st.info("No active loads are available for Port Houston lookup.")
        return

    selected_load = st.selectbox("Select Load", load_options, format_func=_port_houston_load_label, key="port_houston_selected_load")
    load_id = int(selected_load["_row_id"])
    default_container = _safe_str(selected_load.get("Container Number", ""))
    default_booking = _safe_str(selected_load.get("Booking Number", ""))

    l1, l2, l3, l4 = st.columns(4)
    l1.metric("Booking", default_booking or "-")
    l2.metric("Container", default_container or "-")
    l3.metric("Customer", _safe_str(selected_load.get("Customer", "")) or "-")
    l4.metric("Status", _safe_str(selected_load.get("Status", "")) or "-")

    container_value = st.text_input("Container to Check", value=default_container, key="port_houston_load_container")
    booking_value = st.text_input("Booking to Check", value=default_booking, key="port_houston_load_booking")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("Lookup Container", key="port_houston_lookup_load_container", use_container_width=True):
            client = _get_port_houston_client_or_none()
            if client:
                try:
                    data = client.get_inventory_units(container=container_value)
                    _store_port_houston_result("port_houston_load_unit_result", data, "Container / Unit", container_value, load_id)
                    st.success("Container lookup complete.")
                except Exception as exc:
                    _log_port_houston_event(action_type="lookup", lookup_type="Container / Unit", request_reference=container_value, load_id=load_id, status="failed", error_message=str(exc))
                    st.error(f"Container lookup failed: {exc}")
    with b2:
        if st.button("Lookup Booking", key="port_houston_lookup_load_booking", use_container_width=True):
            client = _get_port_houston_client_or_none()
            if client:
                try:
                    data = client.get_bookings(booking=booking_value)
                    _store_port_houston_result("port_houston_load_booking_result", data, "Booking", booking_value, load_id)
                    st.success("Booking lookup complete.")
                except Exception as exc:
                    _log_port_houston_event(action_type="lookup", lookup_type="Booking", request_reference=booking_value, load_id=load_id, status="failed", error_message=str(exc))
                    st.error(f"Booking lookup failed: {exc}")

    unit_records = _render_port_houston_result("port_houston_load_unit_result", mode="unit")
    if unit_records and st.button("Update Load From Container Data", key="port_houston_update_from_unit", use_container_width=True):
        updates = _updates_from_port_houston_unit(selected_load, unit_records[0])
        _apply_port_houston_updates(load_id, updates, "update_load_from_unit")
        refresh_data()
        st.success("Load updated from Port Houston container data.")
        st.rerun()

    booking_records = _render_port_houston_result("port_houston_load_booking_result")
    if booking_records and st.button("Update Load From Booking Data", key="port_houston_update_from_booking", use_container_width=True):
        updates = _updates_from_port_houston_booking(selected_load, booking_records[0])
        _apply_port_houston_updates(load_id, updates, "update_load_from_booking")
        refresh_data()
        st.success("Load updated from Port Houston booking data.")
        st.rerun()
        st.divider()
    st.markdown("#### Express Pass / PIN Request")

    pin_c1, pin_c2, pin_c3 = st.columns(3)

    with pin_c1:
        pin_driver = st.text_input(
            "Driver",
            value=_safe_str(selected_load.get("Driver Name", "")),
            key=f"pin_driver_{load_id}",
        )

    with pin_c2:
        pin_truck = st.text_input(
            "Truck License / Truck #",
            value=_safe_str(selected_load.get("Truck Assigned", "")),
            key=f"pin_truck_{load_id}",
        )

    with pin_c3:
        pin_chassis = st.text_input(
            "Chassis",
            value=_safe_str(selected_load.get("Chassis", "")),
            key=f"pin_chassis_{load_id}",
        )

    pin_tran_label = st.selectbox(
        "Port Transaction Type",
        list(PORT_HOUSTON_APPOINTMENT_TRAN_TYPES.keys()),
        key=f"pin_tran_type_{load_id}",
    )

    pin_date = st.date_input(
        "Requested Date",
        value=date.today(),
        key=f"pin_date_{load_id}",
    )

    pin_time = st.selectbox(
        "Requested Time",
        ["06:00:00", "07:00:00", "08:00:00", "09:00:00", "10:00:00", "11:00:00", "12:00:00", "13:00:00", "14:00:00", "15:00:00", "16:00:00", "17:00:00"],
        key=f"pin_time_{load_id}",
    )

    pin_gate = st.selectbox(
        "Gate",
        ["BPT MAIN", "BCT MAIN"],
        key=f"pin_gate_{load_id}",
    )

    pin_scac = st.text_input(
        "Trucking Company / SCAC",
        value=_get_app_setting("PORT_HOUSTON_OPERATOR", "POHA"),
        key=f"pin_scac_{load_id}",
    )

    pin_equipment_type = st.text_input(
        "Equipment Type",
        value=_safe_str(selected_load.get("Size", "")) or "40HC",
        key=f"pin_equipment_{load_id}",
    )

    pin_payload = _build_port_houston_appointment_payload(
        action="Create",
        appointment_nbr="",
        appointment_date=pin_date,
        appointment_time=pin_time,
        gate_id=pin_gate,
        truck_license=pin_truck,
        trucking_co_id=pin_scac,
        tran_type=PORT_HOUSTON_APPOINTMENT_TRAN_TYPES[pin_tran_label],
        container=container_value,
        booking=booking_value,
        chassis=pin_chassis,
        equipment_type=pin_equipment_type,
        owns_chassis=True,
    )

    with st.expander("Review Port Houston PIN / Appointment Payload", expanded=False):
        st.text_area(
            "Payload",
            value=pin_payload,
            height=260,
            key=f"pin_payload_{load_id}",
        )

    if st.button("Save PIN Request To Load", key=f"save_pin_request_{load_id}", use_container_width=True):
        if not booking_value and not container_value:
            st.error("Booking or container is required.")
        elif not pin_truck.strip():
            st.error("Truck license / truck number is required.")
        else:
            execute(
                """
                update loads
                set dispatcher_notes = concat(
                    coalesce(dispatcher_notes, ''),
                    E'\n\nPort Houston PIN / Express Pass Request:',
                    E'\nTransaction Type: ', :tran_type,
                    E'\nDate/Time: ', :pin_date, ' ', :pin_time,
                    E'\nGate: ', :gate,
                    E'\nBooking: ', :booking,
                    E'\nContainer: ', :container,
                    E'\nTruck: ', :truck,
                    E'\nDriver: ', :driver,
                    E'\nChassis: ', :chassis
                )
                where id = :load_id
                """,
                {
                    "load_id": load_id,
                    "tran_type": pin_tran_label,
                    "pin_date": str(pin_date),
                    "pin_time": pin_time,
                    "gate": pin_gate,
                    "booking": booking_value,
                    "container": container_value,
                    "truck": pin_truck,
                    "driver": pin_driver,
                    "chassis": pin_chassis,
                },
            )

            _log_port_houston_event(
                action_type="pin_request_saved",
                lookup_type="Express Pass / PIN",
                request_reference=booking_value or container_value,
                load_id=load_id,
                response_summary={
                    "transaction_type": pin_tran_label,
                    "date": str(pin_date),
                    "time": pin_time,
                    "gate": pin_gate,
                    "booking": booking_value,
                    "container": container_value,
                    "truck": pin_truck,
                    "driver": pin_driver,
                    "chassis": pin_chassis,
                    "payload": pin_payload,
                },
            )

            refresh_data()
            st.success("PIN request saved to load notes and Port Houston log.")
            st.rerun()
    
def _render_port_houston_direct_lookup() -> None:
    st.markdown("#### Live Endpoint Lookup")
    endpoint_name = st.selectbox("Data Type", list(PORT_HOUSTON_ENDPOINTS.keys()), key="port_houston_endpoint_name")
    endpoint = PORT_HOUSTON_ENDPOINTS[endpoint_name]
    st.caption(endpoint["hint"])

    c1, c2 = st.columns(2)
    reference = c1.text_input("Quick Reference", placeholder="Container, booking, or vessel visit", key="port_houston_reference")
    predicate = c2.text_input("Predicate", placeholder="Example: routing.pod1Id=TWKHH", key="port_houston_predicate")
    fields = st.text_area("Fields", value=endpoint["fields"], height=90, key=f"port_houston_fields_{endpoint_name}")

    if st.button("Run Lookup", key="port_houston_direct_lookup", use_container_width=True):
        client = _get_port_houston_client_or_none()
        if client:
            try:
                if endpoint_name == "Container / Unit":
                    data = client.get_inventory_units(container=reference, predicate=predicate, fields=fields or UNIT_FIELDS)
                elif endpoint_name == "Booking":
                    data = client.get_bookings(booking=reference, predicate=predicate, fields=fields or BOOKING_FIELDS)
                elif endpoint_name == "Vessel Visit":
                    data = client.get_vessel_visits(visit_id=reference, predicate=predicate, fields=fields or VESSEL_FIELDS)
                elif endpoint_name == "Gate Appointments":
                    data = client.get_gate_appointments(predicate=predicate)
                elif endpoint_name == "Appointment Time Slots":
                    data = client.get_appointment_time_slots(predicate=predicate)
                else:
                    params = {}
                    if predicate.strip():
                        params["predicate"] = predicate.strip()
                    if fields.strip():
                        params["fields"] = fields.strip()
                    data = client.request(endpoint["endpoint"], params=params)
                _store_port_houston_result("port_houston_direct_result", data, endpoint_name, reference or predicate)
                st.success("Lookup complete.")
            except Exception as exc:
                _log_port_houston_event(action_type="lookup", lookup_type=endpoint_name, request_reference=reference or predicate, status="failed", error_message=str(exc))
                st.error(f"Lookup failed: {exc}")

    _render_port_houston_result("port_houston_direct_result", mode="unit" if endpoint_name == "Container / Unit" else "flat")


def _render_port_houston_appointments(df: pd.DataFrame) -> None:
    st.markdown("#### Appointment Tools")
    st.caption("Build Port Houston appointment payloads from a load. Live appointment creation also requires N4 authorization from Port Houston.")

    load_options = _port_houston_load_options(df)
    selected_load = None
    if load_options:
        selected_load = st.selectbox("Use Load Defaults", load_options, format_func=_port_houston_load_label, key="port_houston_appt_load")

    default_container = _safe_str(selected_load.get("Container Number", "")) if selected_load else ""
    default_booking = _safe_str(selected_load.get("Booking Number", "")) if selected_load else ""
    default_chassis = _safe_str(selected_load.get("Chassis", "")) if selected_load else ""
    default_size = _safe_str(selected_load.get("Size", "")) if selected_load else "40HC"

    a1, a2, a3 = st.columns(3)
    action = a1.selectbox("Action", ["Create", "Update", "Cancel"], key="port_houston_appt_action")
    appointment_nbr = a2.text_input("Appointment Number", key="port_houston_appt_nbr")
    tran_label = a3.selectbox("Transaction Type", list(PORT_HOUSTON_APPOINTMENT_TRAN_TYPES.keys()), key="port_houston_appt_tran")

    d1, d2, d3, d4 = st.columns(4)
    appointment_date = d1.date_input("Appointment Date", value=date.today(), key="port_houston_appt_date")
    appointment_time = d2.selectbox(
        "Arrival Hour",
        ["06:00:00", "07:00:00", "08:00:00", "09:00:00", "10:00:00", "11:00:00", "12:00:00", "13:00:00", "14:00:00", "15:00:00", "16:00:00", "17:00:00"],
        key="port_houston_appt_time",
    )
    gate_id = d3.selectbox("Gate", ["BPT MAIN", "BCT MAIN"], key="port_houston_appt_gate")
    owns_chassis = d4.checkbox("Driver brings/owns chassis", value=True, key="port_houston_appt_owns_chassis")

    f1, f2, f3 = st.columns(3)
    truck_license = f1.text_input("Truck License", placeholder="LP12345 or SCAC if unknown", key="port_houston_appt_truck")
    trucking_co_id = f2.text_input("Trucking Company / SCAC", key="port_houston_appt_scac")
    equipment_type = f3.text_input("Equipment Type", value=default_size or "40HC", key="port_houston_appt_equipment")

    c1, c2, c3 = st.columns(3)
    container = c1.text_input("Container", value=default_container, key="port_houston_appt_container")
    booking = c2.text_input("Booking / Order", value=default_booking, key="port_houston_appt_booking")
    chassis = c3.text_input("Chassis", value=default_chassis, key="port_houston_appt_chassis")

    payload = _build_port_houston_appointment_payload(
        action=action,
        appointment_nbr=appointment_nbr,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        gate_id=gate_id,
        truck_license=truck_license,
        trucking_co_id=trucking_co_id,
        tran_type=PORT_HOUSTON_APPOINTMENT_TRAN_TYPES[tran_label],
        container=container,
        booking=booking,
        chassis=chassis,
        equipment_type=equipment_type,
        owns_chassis=owns_chassis,
    )
    st.text_area("Appointment SOAP Payload", value=payload, height=280, key="port_houston_appt_payload")
    st.download_button("Download Appointment Payload", data=payload, file_name=f"port_houston_{action.lower()}_appointment.xml", mime="application/xml", use_container_width=True)

    predicate = st.text_input("Time Slot Predicate", placeholder="Optional field filter", key="port_houston_timeslot_predicate")
    if st.button("Check Appointment Time Slots", key="port_houston_check_timeslots", use_container_width=True):
        client = _get_port_houston_client_or_none()
        if client:
            try:
                data = client.get_appointment_time_slots(predicate=predicate)
                _store_port_houston_result("port_houston_timeslot_result", data, "Appointment Time Slots", predicate)
                st.success("Time slot lookup complete.")
            except Exception as exc:
                st.error(f"Time slot lookup failed: {exc}")
    _render_port_houston_result("port_houston_timeslot_result")


def _render_port_houston_subscriptions() -> None:
    st.markdown("#### Event Subscriptions")
    st.caption("Create or review Navis EVP event subscriptions for booking changes, gate events, units, and vessel updates.")

    s1, s2, s3 = st.columns(3)
    event_name = s1.selectbox("Event", PORT_HOUSTON_SUBSCRIPTION_EVENTS, key="port_houston_sub_event")
    operation = s2.selectbox("Operation", ["", "create", "update", "delete"], key="port_houston_sub_operation")
    persistence = s3.checkbox("Persistent", value=True, key="port_houston_sub_persistent")

    group_default = f"Calitrans{event_name}{datetime.now().strftime('%Y%m%d')}"
    group_id = st.text_input("Group ID", value=group_default, key="port_houston_sub_group")
    predicate = st.text_input("Subscription Predicate", placeholder="Example: unitId=ABCD1234567 or freightKind=FCL", key="port_houston_sub_predicate")
    fields = st.text_area("Fields to Include", value="", placeholder="Comma-separated field list, optional", height=80, key="port_houston_sub_fields")

    filter_payload = {"eventName": event_name}
    if operation:
        filter_payload["operation"] = operation
    if predicate.strip() or fields.strip():
        filter_payload["filter"] = {}
        if predicate.strip():
            filter_payload["filter"]["predicate"] = predicate.strip()
        if fields.strip():
            filter_payload["filter"]["fields"] = [field.strip() for field in fields.split(",") if field.strip()]

    payload = {"groupId": group_id, "persistence": persistence, "transport": "ws", "filters": [filter_payload]}
    st.json(payload)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("List Subscriptions", key="port_houston_list_subscribers", use_container_width=True):
            client = _get_port_houston_client_or_none()
            if client:
                try:
                    data = client.get_subscribers()
                    _store_port_houston_result("port_houston_subscribers_result", data, "Subscriptions", "")
                    st.success("Subscriptions loaded.")
                except Exception as exc:
                    st.error(f"Could not load subscriptions: {exc}")
    with c2:
        if st.button("Create Subscription", key="port_houston_create_subscriber", use_container_width=True):
            client = _get_port_houston_client_or_none()
            if client:
                try:
                    data = client.create_subscriber(payload)
                    _store_port_houston_result("port_houston_subscribers_result", data, "Create Subscription", group_id)
                    st.success("Subscription request sent.")
                except Exception as exc:
                    st.error(f"Could not create subscription: {exc}")

    records = _render_port_houston_result("port_houston_subscribers_result")
    if records:
        st.info("For websocket monitoring, connect to the documented stream URL with the returned subscription id and groupId.")


def _render_port_houston_mapping() -> None:
    st.markdown("#### Drayage Mapping")
    st.caption("Recommended Port Houston EVP data mapping for CaliTrans TMS.")
    rows = [
        {"EVP Area": "Inventory Unit", "Endpoint": "/inventory/units", "TMS Use": "Container status, position, yard/facility, routing, return location", "TMS Action": "Update load notes, container size, port/facility, and availability checks"},
        {"EVP Area": "Booking", "Endpoint": "/orders/bookings", "TMS Use": "Booking changes, quantity/tally, line, vessel visit, receiving window", "TMS Action": "Update booking review, dispatcher notes, and avoid dry runs"},
        {"EVP Area": "Vessel Visit", "Endpoint": "/vessel/vesselvisits", "TMS Use": "ETA/ETD, begin receive, cutoff, first availability", "TMS Action": "Drive appointment planning and exception alerts"},
        {"EVP Area": "Gate Appointments", "Endpoint": "/road/gateappointments", "TMS Use": "Existing appointment visibility", "TMS Action": "Confirm appointment state before dispatch"},
        {"EVP Area": "Gate Transactions", "Endpoint": "/road/gatetransactions", "TMS Use": "Ingate/outgate and trouble stages", "TMS Action": "Update dispatch timeline and customer status"},
        {"EVP Area": "Notify Subscriptions", "Endpoint": "/notify/subscribers", "TMS Use": "Booking, unit, appointment, and gate event monitoring", "TMS Action": "Future automation feed for Operations Inbox alerts"},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("Required Local Settings", expanded=False):
        st.code(
            "\n".join(
                [
                    "PORT_HOUSTON_CLIENT_ID=your_client_id",
                    "PORT_HOUSTON_CLIENT_SECRET=your_client_secret",
                    "PORT_HOUSTON_OPERATOR=POHA",
                    "PORT_HOUSTON_BASE_URL=https://api.america.naviscloudops.com/v3/evp",
                    "PORT_HOUSTON_AUTH_URL=https://auth-v1.america.naviscloudops.com/auth/realms/phaprod/protocol/openid-connect/token",
                ]
            ),
            language="bash",
        )


def render_port_houston_integration(df: pd.DataFrame) -> None:
    st.subheader("Port Houston Integration")
    st.caption("All-in-one Navis EVP workspace for Port Houston container, booking, vessel, gate, appointment, and subscription data.")
    _render_port_houston_setup()

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Load Sync", "Live Lookup", "Appointments", "Subscriptions", "Data Map"])
    with tab1:
        _render_port_houston_selected_load(df)
    with tab2:
        _render_port_houston_direct_lookup()
    with tab3:
        _render_port_houston_appointments(df)
    with tab4:
        _render_port_houston_subscriptions()
    with tab5:
        _render_port_houston_mapping()


def _load_current_tms_data_or_stop() -> pd.DataFrame:
    try:
        return load_tms_data()
    except Exception as exc:
        st.error(f"Could not load PostgreSQL/Supabase data: {exc}")
        st.info("Make sure DATABASE_URL is set and database/schema.sql has been run.")
        st.stop()


def main() -> None:
    load_css()
    show_header()

    selected_booking = st.query_params.get("booking", None)
    if selected_booking:
        df = _load_current_tms_data_or_stop()
        render_booking_detail(df, selected_booking)
        return

    with st.sidebar:
        if Path("assets/calitrans_logo.png").exists():
            st.image("assets/calitrans_logo.png", width=160)

        section = st.radio(
            "Navigation",
            NAVIGATION_SECTIONS,
        )

        st.divider()

        if st.button("Refresh Data"):
            refresh_data()
            st.rerun()

        st.divider()
        _render_status_legend()

    df = _load_current_tms_data_or_stop() if section in LOAD_DATA_SECTIONS else pd.DataFrame()

    if section == "Operations Inbox":
        render_operations_inbox()
    elif section == "Port Houston Integration":
        render_port_houston_integration(df)
    elif section == "Dashboard":
        render_dashboard(df)
    elif section == "Orders/Load Management":
        render_orders_management(df)
    elif section == "Dispatch Board":
        render_dispatch_board_focused(df)
    elif section == "Calendar View":
        render_calendar_view(df)
    elif section == "Documents":
        render_documents(df)
    elif section == "Email Imports":
        render_email_imports()    
    elif section == "Billing / ProfitTools":
        render_billing(df)
    elif section == "Validation":
        st.subheader("Validation")
        issues = validate_dispatch_rows(df)
        if issues.empty:
            st.success("No validation issues found.")
        else:
            st.dataframe(issues, use_container_width=True, hide_index=True)
    elif section == "Master Data":
        render_master_data_admin()


if __name__ == "__main__":
    main()
