from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, unquote
import base64

import pandas as pd
import streamlit as st

from admin_pages import render_master_data_admin
from config import ACTIVE_STATUSES, APP_NAME, EDITABLE_COLUMNS
from db_client import DispatchDatabaseClient, execute, read_df
from order_parser import extract_text_from_pdf, parse_order_text
from profittools_export import export_ready_loads
from validators import validate_dispatch_rows
from order_intake import get_intake_queue, get_intake_record, create_load_from_intake, update_intake_status, render_order_upload_panel, render_email_intake_panel


st.set_page_config(
    page_title="CaliTrans TMS",
    page_icon="🚚",
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


def refresh_data() -> None:
    st.cache_data.clear()


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()

    for col in SUMMARY_COLUMNS + ["Reference Number", "Address", "Billing Notes", "Ready for ProfitTools", "Rate", "current_location", "eta", "live_load_status", "live_unload_status", "last_driver_update"]:
        if col not in df.columns:
            df[col] = ""

    df["TYPE"] = df["TYPE"].astype(str).str.strip()
    df["Status"] = df["Status"].astype(str).str.strip()
    df["Booking Number"] = df["Booking Number"].astype(str).str.strip()

    return df


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
        return df
    return df.merge(ext, on="_row_id", how="left")


def filter_loads(df: pd.DataFrame, search_text: str = "", status_filter: str = "All", type_filter: str = "All") -> pd.DataFrame:
    filtered = df.copy()

    if status_filter != "All":
        filtered = filtered[filtered["Status"].astype(str).eq(status_filter)]

    if type_filter != "All":
        filtered = filtered[filtered["TYPE"].astype(str).eq(type_filter)]

    if search_text:
        needle = search_text.lower()
        filtered = filtered[
            filtered.astype(str)
            .apply(lambda row: row.str.lower().str.contains(needle, regex=False).any(), axis=1)
        ]

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
    driver = str(row.get("Driver Name", "") or "Unassigned")
    need_date = str(row.get("Delivery Need Date", "") or "-")
    lfd = str(row.get("LFD", "") or "-")

    status_color = _get_status_color(status)
    border_color = _get_status_border_color(status)

    if st.button(
        f"{booking}\\n{container}",
        key=f"load_card_btn_{row_id}",
        use_container_width=True,
    ):
        st.session_state["selected_dispatch_load_id"] = row_id
        st.rerun()

    st.markdown(
        f"""
        <div class="load-card" style="
            background:{status_color};
            border-left: 7px solid {border_color};
            border-top: 1px solid {border_color};
            border-right: 1px solid {border_color};
            border-bottom: 1px solid {border_color};
        ">
            <div class="load-card-small"><b>{customer}</b></div>
            <div class="load-card-small">Driver: {driver}</div>
            <div class="load-card-small">Need: {need_date}</div>
            <div class="load-card-small">LFD: {lfd}</div>
            <span class="status-pill" style="background:white; color:{border_color}; border:1px solid {border_color};">{status}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )



def render_dashboard(df: pd.DataFrame) -> None:
    show_kpis(df)

    st.markdown("### Operations Filters")

    today = pd.Timestamp(date.today())
    delivery_dates = pd.to_datetime(df.get("Delivery Need Date", ""), errors="coerce")
    lfd_dates = pd.to_datetime(df.get("LFD", ""), errors="coerce")

    filter_options = {
        "All Open Loads": ~df["Status"].isin(["Closed", "Cancelled", "Invoiced"]),
        "Ready to Dispatch": df["Status"].eq("Ready to Dispatch"),
        "On Driver": df["Status"].isin(
            ["Assigned", "En Route to Pickup", "At Pickup", "Loaded", "En Route To Delivery"]
        ),
        "LFD Risk": (lfd_dates.notna())
        & (lfd_dates <= today + pd.Timedelta(days=1))
        & (~df["Status"].isin(["Delivered", "Closed", "Cancelled"])),
        "Late Deliveries": (delivery_dates.notna())
        & (delivery_dates < today)
        & (~df["Status"].isin(["Delivered", "Closed", "Cancelled"])),
        "Needs Info": df["Status"].eq("Hold/Need Info"),
        "Delivered / POD Needed": df["Status"].eq("Delivered"),
        "Ready for Billing": df["Status"].isin(["POD Received", "Ready for ProfitTools"]),
    }

    selected_filter = st.radio(
        "Select workload",
        list(filter_options.keys()),
        horizontal=True,
        label_visibility="collapsed",
    )

    filtered_df = df[filter_options[selected_filter]].copy()

    st.markdown(f"### Operations Snapshot — {selected_filter}")
    st.caption(f"Showing {len(filtered_df)} affected load(s). Rows are color-coded by load status.")

    type_tabs = st.tabs(["OTR Import", "OTR Export", "OTR Local Import"])

    type_map = {
        "OTR Import": "OTR Import",
        "OTR Export": "OTR Export",
        "OTR Local Import": "OTR Local Import",
    }

    columns = [
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

    for tab, (tab_name, type_value) in zip(type_tabs, type_map.items()):
        with tab:
            type_df = filtered_df[
                filtered_df["TYPE"].astype(str).str.strip().eq(type_value)
            ].copy()

            st.markdown(f"#### {tab_name}")
            st.caption(f"{len(type_df)} load(s)")

            if type_df.empty:
                st.success(f"No {tab_name} loads found for this filter.")
                continue

            display_cols = [c for c in columns if c in type_df.columns]
            styled = (
                type_df.sort_values("_row_id", ascending=False)[display_cols]
                .style
                .apply(_status_row_style, axis=1)
            )

            st.dataframe(
                styled,
                use_container_width=True,
                hide_index=True,
            )


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


def render_orders(df: pd.DataFrame) -> None:
    st.subheader("Orders")

    intake_tab, confirm_tab, active_tab = st.tabs(
        ["1. Intake Queue", "2. Confirm Orders", "3. Booking Verification"]
    )

    with intake_tab:
        st.markdown("### New Order Intake")
        st.caption(
            "Capture new customer requests from email PDFs, manual PDF uploads, or phone orders. "
            "These are not active dispatch loads yet."
        )

        try:
            queue_df = get_intake_queue("Open")
        except Exception as exc:
            st.error(f"Could not load intake queue: {exc}")
            st.info("Run database/order_intake_migration.sql in Supabase if you have not already.")
            queue_df = pd.DataFrame()

        c1, c2, c3, c4 = st.columns(4)
        if queue_df.empty:
            c1.metric("Open Intake", 0)
            c2.metric("Needs Review", 0)
            c3.metric("Missing Info", 0)
            c4.metric("Ready to Convert", 0)
        else:
            c1.metric("Open Intake", len(queue_df))
            c2.metric("Needs Review", int(queue_df["intake_status"].eq("Needs Review").sum()))
            c3.metric(
                "Missing Info",
                int(queue_df["intake_status"].isin(["Needs Customer Info", "Needs Appointment"]).sum()),
            )
            c4.metric("Ready to Convert", int(queue_df["intake_status"].eq("Ready to Create Load").sum()))

        intake_source_tab, intake_queue_tab = st.tabs(["Add New Intake", "Recent Intake Items"])

        with intake_source_tab:
            source = st.radio(
                "How was the order received?",
                ["Upload PDF", "Review Email Orders", "Phone / Manual Order"],
                horizontal=True,
            )

            if source == "Upload PDF":
                render_order_upload_panel()

            elif source == "Review Email Orders":
                render_email_intake_panel()

            else:
                st.markdown("#### Phone / Manual Order")
                st.caption("Capture only the basic information. Full load details are added during confirmation.")

                with st.form("phone_manual_intake_form", clear_on_submit=True):
                    col1, col2 = st.columns(2)

                    with col1:
                        customer = st.text_input("Customer")
                        contact = st.text_input("Contact / Caller")
                        booking = st.text_input("Booking #")
                        container = st.text_input("Container #")

                    with col2:
                        order_type = st.selectbox("Order Type", ["OTR Import", "OTR Export", "OTR Local Import", "Unknown"])
                        pickup_or_port = st.text_input("Pickup / Port / Terminal")
                        delivery_location = st.text_input("Delivery Location / Warehouse")
                        requested_date = st.date_input("Requested Date", value=None)

                    action_needed = st.text_area(
                        "Action Needed",
                        placeholder="Example: Need customer confirmation, warehouse address, appointment time, rate confirmation...",
                    )

                    submitted = st.form_submit_button("Add Phone Order to Intake Queue")

                if submitted:
                    parsed_data = {
                        "TYPE": "" if order_type == "Unknown" else order_type,
                        "Customer": customer,
                        "Contact": contact,
                        "Booking Number": booking,
                        "Container Number": container,
                        "Port": pickup_or_port,
                        "Warehouse": delivery_location,
                        "Delivery Need Date": requested_date,
                        "Dispatcher Notes": action_needed,
                    }

                    execute(
                        """
                        insert into order_intake (
                            source,
                            source_subject,
                            source_sender,
                            parsed_data,
                            raw_text,
                            intake_status,
                            action_required
                        )
                        values (
                            'phone_order',
                            :source_subject,
                            :source_sender,
                            cast(:parsed_data as jsonb),
                            :raw_text,
                            'Needs Review',
                            :action_required
                        )
                        """,
                        {
                            "source_subject": f"Phone order - {customer or booking or 'Unknown'}",
                            "source_sender": contact,
                            "parsed_data": pd.Series(parsed_data).to_json(),
                            "raw_text": str(parsed_data),
                            "action_required": action_needed or "Review phone order and confirm required details",
                        },
                    )
                    st.success("Phone order added to Intake Queue.")
                    st.cache_data.clear()
                    st.rerun()

        with intake_queue_tab:
            st.markdown("#### Recent Intake Items")
            if queue_df.empty:
                st.success("No intake items require action.")
            else:
                compact_cols = [
                    "id",
                    "created_at",
                    "source",
                    "source_sender",
                    "filename",
                    "intake_status",
                    "action_required",
                ]
                compact_cols = [c for c in compact_cols if c in queue_df.columns]
                st.dataframe(queue_df[compact_cols], use_container_width=True, hide_index=True)

    with confirm_tab:
        st.markdown("### Confirm Orders")
        st.caption(
            "Review intake items here. If enough information is available, convert the intake item into an active load."
        )

        try:
            queue_df = get_intake_queue("Open")
        except Exception as exc:
            st.error(f"Could not load intake queue: {exc}")
            queue_df = pd.DataFrame()

        if queue_df.empty:
            st.success("No orders are waiting for confirmation.")
        else:
            selected_id = st.selectbox(
                "Select intake item",
                queue_df["id"].astype(int).tolist(),
                format_func=lambda x: f"Intake #{x}",
            )

            record = get_intake_record(int(selected_id))
            parsed = record.get("parsed_data") or {}

            st.markdown("#### Intake Summary")
            summary_cols = st.columns(4)
            summary_cols[0].metric("Source", str(record.get("source", "") or "-"))
            summary_cols[1].metric("Status", str(record.get("intake_status", "") or "-"))
            summary_cols[2].metric("Filename", str(record.get("filename", "") or "-"))
            summary_cols[3].metric("Action Needed", str(record.get("action_required", "") or "-")[:40])

            with st.expander("Raw parsed intake data", expanded=False):
                st.json(parsed)

            st.markdown("#### Confirmation Form")
            st.caption("Only confirm the fields needed to create an active dispatch load.")

            with st.form(f"confirm_order_{selected_id}"):
                col1, col2 = st.columns(2)

                with col1:
                    type_val = st.selectbox(
                        "Order Type",
                        ["OTR Import", "OTR Export", "OTR Local Import"],
                        index=0,
                    )
                    booking = st.text_input("Booking Number *", value=str(parsed.get("Booking Number", "") or ""))
                    customer = st.text_input("Customer *", value=str(parsed.get("Customer", "") or ""))
                    container = st.text_input("Container Number", value=str(parsed.get("Container Number", "") or ""))
                    reference = st.text_input("Reference Number", value=str(parsed.get("Reference Number", "") or ""))

                with col2:
                    port = st.text_input("Pickup / Port / Terminal", value=str(parsed.get("Port", "") or ""))
                    warehouse = st.text_input("Delivery Location / Warehouse", value=str(parsed.get("Warehouse", "") or ""))
                    delivery_need = st.text_input("Requested / Delivery Need Date", value=str(parsed.get("Delivery Need Date", "") or ""))
                    lfd = st.text_input("LFD", value=str(parsed.get("LFD", "") or ""))

                notes = st.text_area(
                    "Dispatcher Notes",
                    value=str(parsed.get("Dispatcher Notes", "") or record.get("action_required", "") or ""),
                )

                action = st.selectbox(
                    "Action",
                    [
                        "Create Active Load",
                        "Missing Info",
                        "Waiting Customer Confirmation",
                        "Hold for Appointment",
                        "Duplicate",
                        "Reject",
                    ],
                )

                submitted = st.form_submit_button("Submit Confirmation Action")

            if submitted:
                if action == "Create Active Load":
                    missing_required = []
                    if not booking.strip():
                        missing_required.append("Booking Number")
                    if not customer.strip():
                        missing_required.append("Customer")

                    if missing_required:
                        st.error("Missing required fields: " + ", ".join(missing_required))
                    else:
                        load_id = create_load_from_intake(
                            int(selected_id),
                            {
                                "TYPE": type_val,
                                "Booking Number": booking,
                                "Reference Number": reference,
                                "Customer": customer,
                                "Container Number": container,
                                "Port": port,
                                "Warehouse": warehouse,
                                "Delivery Need Date": delivery_need,
                                "LFD": lfd,
                                "Status": "New",
                                "Dispatcher Notes": notes,
                            },
                        )
                        refresh_data()
                        st.success(f"Created active load ID {load_id}.")
                        st.rerun()

                elif action == "Missing Info":
                    update_intake_status(int(selected_id), "Needs Customer Info", notes)
                    st.warning("Marked as Missing Info.")
                    st.rerun()

                elif action == "Waiting Customer Confirmation":
                    update_intake_status(int(selected_id), "Needs Customer Info", notes or "Waiting customer confirmation")
                    st.warning("Marked as Waiting Customer Confirmation.")
                    st.rerun()

                elif action == "Hold for Appointment":
                    update_intake_status(int(selected_id), "Needs Appointment", notes)
                    st.warning("Marked as Hold for Appointment.")
                    st.rerun()

                elif action == "Duplicate":
                    update_intake_status(int(selected_id), "Duplicate", notes)
                    st.info("Marked as Duplicate.")
                    st.rerun()

                elif action == "Reject":
                    update_intake_status(int(selected_id), "Rejected", notes)
                    st.info("Rejected.")
                    st.rerun()

    with active_tab:
        st.markdown("### Booking Verification")
        st.caption(
            "This is the final office check after order confirmation. "
            "Bookings stay here while they are New, missing minor information, or awaiting appointment confirmation. "
            "Once moved to Ready to Dispatch, they leave this tab and appear on the Dispatch Board."
        )

        verification_statuses = ["New", "Hold/Need Info", "Awaiting Appointment"]
        verification_df = df[df["Status"].isin(verification_statuses)].copy()
        verification_df = _add_booking_verification_columns(verification_df)

        ready_to_dispatch_count = int(df["Status"].eq("Ready to Dispatch").sum())

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Bookings Awaiting Review", int(verification_df["Status"].eq("New").sum()) if not verification_df.empty else 0)
        k2.metric("Missing Information", int(verification_df["Status"].eq("Hold/Need Info").sum()) if not verification_df.empty else 0)
        k3.metric("Awaiting Appointment", int(verification_df["Status"].eq("Awaiting Appointment").sum()) if not verification_df.empty else 0)
        k4.metric("Moved to Dispatch", ready_to_dispatch_count)

        st.markdown("#### Booking Verification Queues")
        q1, q2, q3, q4 = st.tabs(["Needs Review", "Missing Info", "Awaiting Appointment", "All Verification"])

        with q1:
            needs_review = verification_df[verification_df["Status"].eq("New")].copy()
            _render_booking_verification_table(needs_review, "Needs Review / New Bookings")

        with q2:
            missing_info = verification_df[verification_df["Status"].eq("Hold/Need Info")].copy()
            _render_booking_verification_table(missing_info, "Missing Information")

        with q3:
            awaiting_appt = verification_df[verification_df["Status"].eq("Awaiting Appointment")].copy()
            _render_booking_verification_table(awaiting_appt, "Awaiting Appointment")

        with q4:
            _render_booking_verification_table(verification_df, "All Bookings in Verification")

        _render_booking_verification_actions(verification_df)

        with st.expander("Create confirmed booking manually", expanded=False):
            st.caption("Use this only after the order is confirmed but before it is ready for dispatch.")

            with st.form("new_load_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    type_val = st.selectbox("TYPE", ["OTR Import", "OTR Export", "OTR Local Import"])
                    booking = st.text_input("Booking Number *")
                    customer = st.text_input("Customer")
                    container = st.text_input("Container Number")
                    reference = st.text_input("Reference Number")
                    try:
                        size_options = _dropdown_options(_load_master_options().get("sizes", []), "")
                    except Exception:
                        size_options = ["", "20", "40", "40HC", "40ST", "20FR", "40FR", "20 STRF", "40STRF"]
                    size_value = st.selectbox("Size", size_options, key="manual_create_size_verification")
                with c2:
                    port = st.text_input("Port / Terminal")
                    warehouse = st.text_input("Warehouse")
                    address = st.text_input("Address")
                    delivery_need = st.date_input("Delivery Need Date", value=None)
                    lfd = st.date_input("LFD", value=None)
                notes = st.text_area("Verification Notes")
                submitted = st.form_submit_button("Create Booking for Verification")

            if submitted:
                if not booking.strip():
                    st.error("Booking Number is required.")
                else:
                    row = {
                        "TYPE": type_val,
                        "Booking Number": booking,
                        "Customer": customer,
                        "Container Number": container,
                        "Reference Number": reference,
                        "Size": size_value,
                        "Port": port,
                        "Warehouse": warehouse,
                        "Address": address,
                        "Delivery Need Date": delivery_need,
                        "LFD": lfd,
                        "Status": "New",
                        "Dispatcher Notes": notes,
                    }
                    created = DispatchDatabaseClient().add_row(row)
                    refresh_data()
                    st.success(f"Created booking ID {created.id} for verification.")
                    st.rerun()

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


def _save_status_quick_update(load_id: int, new_status: str, note: str) -> None:
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
                st.success("Status updated.")
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
                    _save_status_quick_update(load_id, status_label, default_note)
                    refresh_data()
                    st.success(f"Updated to {status_label}")
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
            DispatchDatabaseClient().update_row_fields(load_id, {"Status": "Ready for ProfitTools"})
            st.success("Marked Ready for ProfitTools.")
            refresh_data()
            st.rerun()


def render_dispatch_board(df: pd.DataFrame) -> None:
    st.subheader("Dispatch Board")
    st.caption("Click a load card to open the dispatcher workspace below.")

    board_df = df[df["Status"].isin(DISPATCH_BOARD_STATUSES)].copy()
    status_cols = st.columns(len(DISPATCH_BOARD_STATUSES))

    for idx, status in enumerate(DISPATCH_BOARD_STATUSES):
        with status_cols[idx]:
            st.markdown(f"**{status}**")
            status_df = board_df[board_df["Status"] == status].head(20)
            if status_df.empty:
                st.caption("No loads")
            for _, row in status_df.iterrows():
                render_load_card(row)

    selected_load = _get_selected_dispatch_load(df)
    if selected_load is not None:
        render_dispatch_workspace(selected_load)
    else:
        st.info("Select a load card above to open the dispatcher workspace.")

def render_containers(df: pd.DataFrame) -> None:
    st.subheader("Container Tracking")

    c1, c2, c3 = st.columns(3)
    search = c1.text_input("Search container / booking")
    status = c2.selectbox("Container Status", ["All"] + LOAD_STATUS_FLOW)
    risk_only = c3.checkbox("Show LFD risk only")

    filtered = filter_loads(df, search, status, "All")

    if risk_only:
        lfd = pd.to_datetime(filtered["LFD"], errors="coerce")
        filtered = filtered[(lfd.notna()) & (lfd <= pd.Timestamp(date.today()) + pd.Timedelta(days=1))]

    columns = [
        "Booking Number",
        "Container Number",
        "TYPE",
        "Customer",
        "Port",
        "Warehouse",
        "Delivery Need Date",
        "LFD",
        "Status",
        "Driver Name",
        "Truck Assigned",
        "Chassis",
    ]
    columns = [c for c in columns if c in filtered.columns]
    st.dataframe(filtered[columns], use_container_width=True, hide_index=True)


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
                    "TYPE": parsed.get("TYPE", "OTR Import"),
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


def main() -> None:
    load_css()
    show_header()

    try:
        df = clean_df(load_dispatch_data())
        df = merge_ext(df)
    except Exception as exc:
        st.error(f"Could not load PostgreSQL/Supabase data: {exc}")
        st.info("Make sure DATABASE_URL is set and database/schema.sql has been run.")
        st.stop()

    selected_booking = st.query_params.get("booking", None)
    if selected_booking:
        render_booking_detail(df, selected_booking)
        return

    with st.sidebar:
        if Path("assets/calitrans_logo.png").exists():
            st.image("assets/calitrans_logo.png", width=160)

        section = st.radio(
            "Navigation",
            [
                "Dashboard",
                "Orders",
                "Dispatch Board",
                "Containers",
                "Documents",
                "Billing / ProfitTools",
                "Validation",
                "Master Data",
            ],
        )

        st.divider()

        if st.button("Refresh Data"):
            refresh_data()
            st.rerun()

        st.divider()
        _render_status_legend()

    if section == "Dashboard":
        render_dashboard(df)
    elif section == "Orders":
        render_orders(df)
    elif section == "Dispatch Board":
        render_dispatch_board(df)
    elif section == "Containers":
        render_containers(df)
    elif section == "Documents":
        render_documents(df)
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
