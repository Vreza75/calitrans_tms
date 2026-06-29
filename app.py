from __future__ import annotations
import json
from datetime import date, datetime
from email.utils import getaddresses, parseaddr
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote
from ai_agents.intent_agent import IntentAgent
from ai_agents.operations_parser_agent import OperationsParserAgent
from ai_agents.load_intelligence_agent import LoadIntelligenceAgent
from ai_agents.workflow_agent import WorkflowAgent
from ai_agents.response_agent import ResponseAgent


import base64
import importlib
import os
import smtplib
import zipfile
import xml.etree.ElementTree as ET

intent_agent = IntentAgent()
operations_parser_agent = OperationsParserAgent()
load_intelligence_agent = LoadIntelligenceAgent()
workflow_agent = WorkflowAgent()
response_agent = ResponseAgent()


def _load_local_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value and os.getenv(key) in [None, ""]:
            os.environ[key] = value


_load_local_env_file()

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.message import EmailMessage

import pandas as pd
import streamlit as st

from admin_pages import render_master_data_admin
from config import ACTIVE_STATUSES, APP_NAME, DOCUMENT_STORAGE_DIR, EDITABLE_COLUMNS, get_config_source, get_secret
from db_client import DispatchDatabaseClient, execute, read_df
import email_client as _email_client
from email_parser import extract_latest_email_body, parse_email_text
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

if not hasattr(_email_client, "fetch_operations_email_by_message_id"):
    _email_client = importlib.reload(_email_client)

fetch_operations_email_sync = _email_client.fetch_operations_email_sync
fetch_operations_email_by_message_id = getattr(_email_client, "fetch_operations_email_by_message_id", None)


st.set_page_config(
    page_title="CaliTrans TMS",
    page_icon="CT",
    layout="wide",
    initial_sidebar_state="expanded",
)


LOAD_STATUS_FLOW = [
    "New Email",
    "Needs Review",
    "Order Created",
    "New",
    "Hold/Need Info",
    "Booking Verified",
    "Port Verified",
    "Ready for Appointment / PIN",
    "Ready for Port PIN",
    "PIN Received",
    "Awaiting Appointment",
    "Ready to Dispatch",
    "Driver Assigned",
    "Assigned",
    "Dispatched",
    "En Route to Pickup",
    "At Port",
    "At Pickup",
    "Loaded / Picked Up",
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

ORDER_MANAGEMENT_STATUSES = [
    "New",
    "Hold/Need Info",
    "Booking Verified",
    "Cancelled",
]

ORDER_MANAGEMENT_STATUS_LABELS = {
    "New": "New",
    "Hold/Need Info": "Missing Info",
    "Booking Verified": "Booking Verified",
    "Cancelled": "Cancel",
}

DISPATCH_BOARD_STATUSES = [
    "Port Verified",
    "Ready for Appointment / PIN",
    "Ready for Port PIN",
    "PIN Received",
    "Ready to Dispatch",
    "Driver Assigned",
    "Assigned",
    "Dispatched",
    "En Route to Pickup",
    "At Port",
    "At Pickup",
    "Loaded / Picked Up",
    "Loaded",
    "En Route To Delivery",
    "Delivered",
    "Returning Empty",
]

LOAD_TYPE_TABS = ["Import", "Export", "Export Local", "Import Local"]

DISPATCH_MOVE_TYPES = LOAD_TYPE_TABS + ["Other"]

DISPATCH_ACTION_WORKFLOWS = {
    "Import": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("sync_port", "Sync Port Data"),
            ("appointment_needed", "Appointment / PIN"),
            ("assign_driver", "Assign Driver"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Port"),
            ("at_port", "At Port"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Warehouse"),
        ],
        "Completion": [
            ("delivered", "Delivered"),
            ("empty_return", "Empty Return"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
    "Export": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("sync_port", "Sync Booking / Terminal"),
            ("appointment_needed", "Empty / Port Appointment"),
            ("assign_driver", "Assign Driver"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Pickup"),
            ("at_pickup", "At Empty Yard / Shipper"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Port"),
            ("at_port", "At Port"),
        ],
        "Completion": [
            ("delivered", "Delivered to Port"),
            ("empty_return", "Chassis Return"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
    "Import Local": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("assign_driver", "Assign Driver"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Pickup"),
            ("at_pickup", "At Pickup"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Delivery"),
        ],
        "Completion": [
            ("delivered", "Delivered"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
    "Export Local": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("assign_driver", "Assign Driver"),
            ("appointment_needed", "Pickup Empty"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Customer"),
            ("at_pickup", "At Customer"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Delivery"),
        ],
        "Completion": [
            ("delivered", "Delivered"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
    "Other": {
        "Verification": [
            ("new_orders", "New Orders"),
            ("needs_verification", "Needs Verification"),
            ("documents", "Documents"),
        ],
        "Planning": [
            ("assign_driver", "Assign Driver"),
            ("send_packet", "Send Packet"),
        ],
        "Execution": [
            ("enroute_pickup", "Enroute Pickup"),
            ("at_pickup", "At Pickup"),
            ("loaded", "Loaded"),
            ("enroute_delivery", "Enroute Delivery"),
        ],
        "Completion": [
            ("delivered", "Delivered"),
            ("ready_billing", "Ready for Billing"),
            ("completed", "Completed"),
        ],
    },
}

ACTIVE_DRIVER_STATUSES = [
    "Driver Assigned",
    "Assigned",
    "Dispatched",
    "En Route to Pickup",
    "At Port",
    "At Pickup",
    "Loaded / Picked Up",
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
    "Active Status",
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
    "Active Status",
    "Dispatch Board",
    "Calendar View",
    "Documents",
    "Billing / ProfitTools",
    "Port Houston Integration",
    "Validation",
}

STATUS_LEGEND_SECTIONS = {"Active Status", "Dispatch Board", "Calendar View"}


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
        html, body, [class*="css"] {
            font-family: "Inter", "Segoe UI", Arial, sans-serif;
        }
        [data-testid="stAppViewContainer"] {
            background: #f6f8fb;
        }
        .block-container {
            max-width: 1320px;
            padding-top: 0.8rem;
            padding-bottom: 2rem;
        }
        h1, h2, h3 {
            letter-spacing: 0;
            color: #0f172a;
        }
        h2, h3 {
            font-weight: 750;
        }
        div[data-testid="stExpander"] {
            border: 1px solid #d8e0ec;
            border-radius: 8px;
            background: #ffffff;
            box-shadow: none;
        }
        div[data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #d8e0ec;
            border-radius: 8px;
            padding: 10px 12px;
            box-shadow: none;
        }
        div[data-testid="stMetricLabel"] {
            font-size: 0.72rem;
            color: #475569;
            font-weight: 650;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.35rem;
            color: #0f172a;
            font-weight: 750;
        }
        .stButton > button {
            border-radius: 8px !important;
            min-height: 2.35rem;
            padding: 0.45rem 0.9rem;
            font-size: 0.82rem;
            font-weight: 700;
            box-shadow: none !important;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px !important;
            border-bottom: 1px solid #d8e0ec !important;
        }
        .stTabs [data-baseweb="tab"] {
            min-height: 38px !important;
            height: 38px !important;
            padding: 0 10px !important;
            border-radius: 8px 8px 0 0 !important;
            font-size: 0.78rem !important;
            font-weight: 650 !important;
            box-shadow: none !important;
        }
        .stTabs [aria-selected="true"] {
            background: #fff8d7 !important;
            border-color: #ffd200 !important;
            border-bottom: 3px solid #ffd200 !important;
        }
        [data-testid="stDataFrame"] {
            border-radius: 8px !important;
            border: 1px solid #d8e0ec !important;
            box-shadow: none !important;
        }
        .ops-header {
            margin: 0.25rem 0 0.85rem 0;
            padding: 0;
        }
        .ops-kicker {
            color: #64748b;
            font-size: 0.78rem;
            font-weight: 650;
            text-transform: uppercase;
            letter-spacing: 0;
            margin-bottom: 0.25rem;
        }
        .ops-title {
            color: #0f172a;
            font-size: 1.35rem;
            line-height: 1.2;
            font-weight: 800;
            margin: 0;
        }
        .ops-subtitle {
            color: #64748b;
            font-size: 0.86rem;
            line-height: 1.45;
            margin-top: 0.35rem;
            max-width: 780px;
        }
        .ops-metric-card {
            background: #ffffff;
            border: 1px solid #d8e0ec;
            border-radius: 8px;
            padding: 10px 12px;
            min-height: 74px;
        }
        .ops-metric-label {
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 700;
            margin-bottom: 0.35rem;
        }
        .ops-metric-value {
            color: #0f172a;
            font-size: 1.45rem;
            font-weight: 800;
            line-height: 1.05;
        }
        .ops-metric-sub {
            color: #64748b;
            font-size: 0.72rem;
            margin-top: 0.25rem;
        }
        .ops-alert {
            border: 1px solid #cfe0f8;
            border-radius: 8px;
            background: #eaf3ff;
            color: #064b91;
            padding: 0.75rem 0.85rem;
            font-size: 0.84rem;
            line-height: 1.45;
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
    "New Email": "#f8fafc",
    "Needs Review": "#fef3c7",
    "Order Created": "#e0f2fe",
    "New": "#f8fafc",
    "Hold/Need Info": "#fecaca",
    "Booking Verified": "#dbeafe",
    "Port Verified": "#c7d2fe",
    "Ready for Appointment / PIN": "#ddd6fe",
    "Ready for Port PIN": "#ddd6fe",
    "PIN Received": "#bfdbfe",
    "Awaiting Appointment": "#fdba74",
    "Ready to Dispatch": "#bbf7d0",
    "Driver Assigned": "#dcfce7",
    "Assigned": "#dcfce7",
    "Dispatched": "#ccfbf1",
    "En Route to Pickup": "#bef264",
    "At Port": "#fde68a",
    "At Pickup": "#fde047",
    "Loaded / Picked Up": "#a5b4fc",
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
    "New Email": "Email received but not converted to order yet",
    "Needs Review": "Needs dispatcher or manager review before order work",
    "Order Created": "Order/load created from intake",
    "New": "New confirmed load, not dispatched yet",
    "Hold/Need Info": "Issue or missing information; dispatcher action required",
    "Booking Verified": "Core order information verified",
    "Port Verified": "Port Houston/container/booking data checked",
    "Ready for Appointment / PIN": "Ready to request port appointment or PIN",
    "Ready for Port PIN": "Ready to request port appointment or PIN",
    "PIN Received": "PIN or gate appointment confirmation is ready",
    "Awaiting Appointment": "Booking confirmed but waiting for pickup or delivery appointment",
    "Ready to Dispatch": "Driver, truck, port, and PIN/appointment are ready for dispatch packet",
    "Driver Assigned": "Driver assigned; confirm truck, PIN/appointment, and packet",
    "Assigned": "Driver and truck assigned",
    "Dispatched": "Driver has been dispatched",
    "En Route to Pickup": "Driver moving toward pickup or terminal",
    "At Port": "Driver is at port or terminal",
    "At Pickup": "Driver checked in or waiting at pickup",
    "Loaded / Picked Up": "Container or freight picked up",
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
    "Intake / Verification": ["New Email", "Needs Review", "Order Created", "New", "Hold/Need Info", "Booking Verified", "Port Verified"],
    "Ready / Active": ["Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch", "Driver Assigned", "Assigned", "Dispatched", "En Route to Pickup", "En Route To Delivery", "Ready for ProfitTools"],
    "Pickup / Loading": ["At Port", "At Pickup", "Loaded / Picked Up", "Loaded"],
    "Delivered / Return": ["Delivered", "Returning Empty", "POD Received"],
    "Issues / Stops": ["Hold/Need Info", "Awaiting Appointment", "Cancelled"],
    "Billing / Closed": ["Exported to ProfitTools", "Invoiced", "Closed"],
}



def _get_status_color(status: str) -> str:
    return STATUS_COLORS.get(str(status or "").strip(), "#f8fafc")


def _get_status_border_color(status: str) -> str:
    border_colors = {
        "New Email": "#94a3b8",
        "Needs Review": "#d97706",
        "Order Created": "#0284c7",
        "New": "#94a3b8",
        "Hold/Need Info": "#dc2626",
        "Booking Verified": "#2563eb",
        "Port Verified": "#4f46e5",
        "Ready for Appointment / PIN": "#7c3aed",
        "Ready for Port PIN": "#7c3aed",
        "PIN Received": "#1d4ed8",
        "Awaiting Appointment": "#ea580c",
        "Ready to Dispatch": "#16a34a",
        "Driver Assigned": "#22c55e",
        "Assigned": "#22c55e",
        "Dispatched": "#14b8a6",
        "En Route to Pickup": "#65a30d",
        "At Port": "#ca8a04",
        "At Pickup": "#ca8a04",
        "Loaded / Picked Up": "#4f46e5",
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
    c2.metric("Ready to Dispatch", int(df["Status"].isin(["PIN Received", "Ready to Dispatch"]).sum()))
    c3.metric("On Driver", int(df["Status"].isin(ACTIVE_DRIVER_STATUSES).sum()))
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
    st.markdown("### Booking Final Check")
    st.caption("Use this section as the last office check before the order becomes Booking Verified. Port sync, PIN, driver assignment, and dispatch packet happen in the load workspace.")

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
        if st.button("Save Verification Note", key=f"save_verify_note_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Dispatcher Notes": action_note},
            )
            refresh_data()
            st.success("Verification note saved.")
            st.rerun()

    with a3:
        disabled = readiness_score < 100
        if st.button(
            "Mark Booking Verified",
            key=f"mark_booking_verified_{selected_row_id}",
            use_container_width=True,
            disabled=disabled,
            help="Requires 100% booking completeness. The next action will be Port Houston verification.",
        ):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Booking Verified",
                    "Dispatcher Notes": action_note or "Booking information verified. Next action: verify booking with Port Houston.",
                },
            )
            refresh_data()
            st.success("Booking marked verified. Open the load workspace for Port Sync / PIN.")
            st.rerun()

    with a4:
        if st.button("Cancel Booking", key=f"verification_cancel_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Status": "Cancelled", "Dispatcher Notes": action_note or "Booking cancelled during review."},
            )
            refresh_data()
            st.error("Booking cancelled.")
            st.rerun()

    if readiness_score < 100:
        st.info("Mark Booking Verified is disabled until all required booking fields are complete.")

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
    "Billing",
    "Business Communication",
    "Driver Issue",
    "Port Issue",
    "Customer Request",
    "POD Request",
    "No Action / FYI",
    "Spam/Marketing",
    "Other",
]

INBOX_TERMINAL_REVIEW_STATUSES = [
    "Order Created",
    "Attached",
    "Quote Created",
    "Order Cancelled",
    "Closed",
]

OPERATIONS_EMAIL_SYNC_SOURCES = [
    "operations_email",
    "operations_email_sent",
    "email_body",
    "email_combined",
]

DEFAULT_OPERATIONS_QUEUE_ORDER = [
    "Action Required",
    "New Orders",
    "Existing Loads",
    "Waiting",
    "Documents",
    "Billing",
    "Business",
    "Archive",
    "Review",
]

OPERATIONS_CONTROL_LEVELS = [
    "Level 1 - Operational Cases",
    "Level 2 - Business Communications",
    "Level 3 - No Action / Archive",
    "Needs Review",
]

OPERATIONS_CONTROL_LEVEL_DESCRIPTIONS = {
    "Level 1 - Operational Cases": "Shipment, load, appointment, driver, port, document, and customer work that should move through dispatch.",
    "Level 2 - Business Communications": "Important company communications for accounting, management, sales, safety, vendors, or administration.",
    "Level 3 - No Action / Archive": "FYI, marketing, newsletters, duplicate, spam, and other messages that should not become dispatcher work.",
    "Needs Review": "Low-confidence or unclear messages that need a person to choose Operational, Business, Archive, or Spam once.",
}

OPERATIONAL_REQUEST_TYPES = {
    "New Booking",
    "Booking Update",
    "Appointment Update",
    "Quote Request",
    "Missing Information",
    "Cancellation",
    "Driver Issue",
    "Port Issue",
    "Customer Request",
    "POD Request",
}

BUSINESS_REQUEST_TYPES = {"Billing", "Business Communication"}
NO_ACTION_REQUEST_TYPES = {"No Action / FYI", "Spam/Marketing"}

BUSINESS_COMMUNICATION_TERMS = [
    "insurance",
    "renewal",
    "contract",
    "agreement",
    "legal",
    "attorney",
    "claim",
    "bank",
    "loan",
    "utility",
    "vendor",
    "supplier",
    "equipment purchase",
    "software",
    "it support",
    "password",
    "recruiting",
    "resume",
    "candidate",
    "employment",
    "hr",
    "human resources",
    "new customer inquiry",
    "sales lead",
    "credit application",
]

NO_ACTION_COMMUNICATION_TERMS = [
    "advertisement",
    "newsletter",
    "unsubscribe",
    "promotion",
    "webinar",
    "container sales",
    "equipment marketing",
    "holiday notice",
    "office closed",
    "for your records only",
    "no action required",
    "do not reply",
]

VIP_OPERATIONS_DOMAINS = {
    "msc.com",
    "maersk.com",
    "hapag-lloyd.com",
    "evergreen-shipping.com",
    "porthouston.com",
}

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


def _normalize_reference_token(value: str) -> str:
    return re.sub(r"\s+", "-", str(value or "").strip(" :#-")).upper()


def _extract_reference_tokens(text: str) -> dict:
    text = str(text or "")

    corrected_container_match = (
        re.search(
            r"\bcontainer\s+([A-Z]{4}\d{7})\b.{0,80}?\b(?:instead\s+of|not|rather\s+than)\s+([A-Z]{4}\d{7})\b",
            text,
            re.I | re.S,
        )
        or re.search(
            r"\b([A-Z]{4}\d{7})\b.{0,80}?\b(?:instead\s+of|not|rather\s+than)\s+([A-Z]{4}\d{7})\b",
            text,
            re.I | re.S,
        )
    )
    subject_pair_match = re.search(r"\b(\d{5,})\s*/\s*([A-Z]{4}\d{7})\b", text, re.I)
    booking_match = (
        re.search(r"\b(?:booking|bkg|bk)\s*(?:number|no\.?|#)?\s*[:#-]\s*([A-Z0-9][A-Z0-9-]{4,})\b", text, re.I)
        or re.search(r"\bbooking\s+(?:confirmation|ref(?:erence)?|number|no\.?)\b[^A-Z0-9]{0,20}([A-Z0-9][A-Z0-9-]{4,})\b", text, re.I)
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
    elif subject_pair_match:
        ref_value = subject_pair_match.group(1)

    container_value = ""
    if corrected_container_match:
        container_value = corrected_container_match.group(1).upper()
    elif container_match:
        container_value = container_match.group(0).upper()
    elif subject_pair_match:
        container_value = subject_pair_match.group(2).upper()

    return {
        "booking_number": _normalize_reference_token(booking_value) if booking_value else "",
        "container_number": container_value,
        "reference_number": _normalize_reference_token(ref_value) if ref_value else "",
    }


APPOINTMENT_INTENT_TERMS = [
    "have the container",
    "please have the container",
    "deliver at",
    "delivery time",
    "pickup time",
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
    "please have the container",
    "requested time",
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

INFORMATION_UPDATE_TERMS = [
    "please note",
    "note the",
    "fyi",
    "for your information",
    "for your records",
    "see below",
    "please see",
    "hours",
    "receiving hours",
    "delivery hours",
    "warehouse hours",
    "office hours",
    "mon - fri",
    "mon-fri",
    "monday",
    "friday",
    "correction",
    "corrected",
    "instead of",
    "revised",
    "updated",
    "confirmed",
    "pre-alert",
    "pre alert",
    "please be advised",
    "tomar nota",
    "para su informacion",
    "para su informaciÃ³n",
    "horario",
    "horarios",
    "correccion",
    "correcciÃ³n",
    "actualizado",
    "confirmado",
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
    "following shipment",
    "shipment details",
    "truckload details",
    "please book",
    "nuevo booking",
    "nueva carga",
    "orden de carga",
    "orden adjunta",
    "favor reservar",
]

ORDER_PLACEMENT_TERMS = [
    "new booking",
    "new load",
    "create order",
    "create load",
    "please book",
    "please arrange",
    "please schedule",
    "please dispatch",
    "please pick up",
    "please pickup",
    "please deliver",
    "need drayage",
    "following shipment",
    "shipment details",
    "truckload details",
    "drayage for the booking",
    "drayage for booking",
    "set up this load",
    "setup this load",
    "load order attached",
    "delivery order attached",
    "attached delivery order",
    "attached load order",
    "nuevo booking",
    "nueva carga",
    "crear orden",
    "favor reservar",
    "favor programar",
    "favor recoger",
    "favor entregar",
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
BILLING_TERMS = [
    "invoice",
    "billing",
    "bill",
    "payment",
    "statement",
    "accessorial",
    "detention",
    "demurrage",
    "lumper",
    "factura",
    "facturacion",
    "facturaciÃ³n",
    "pago",
    "cobro",
]
DRIVER_ISSUE_TERMS = [
    "driver",
    "truck",
    "chassis",
    "flat tire",
    "breakdown",
    "accident",
    "late driver",
    "no show",
    "chofer",
    "conductor",
    "camion",
    "camiÃ³n",
    "chasis",
    "accidente",
]
PORT_ISSUE_TERMS = [
    "port",
    "terminal",
    "hold",
    "customs hold",
    "line hold",
    "exam",
    "x-ray",
    "gate",
    "trouble ticket",
    "puerto",
    "retenido",
    "aduana",
    "inspeccion",
    "inspecciÃ³n",
]
SPAM_MARKETING_TERMS = [
    "unsubscribe",
    "newsletter",
    "marketing",
    "promotion",
    "webinar",
    "seo",
    "lead generation",
    "limited time offer",
    "sales outreach",
]

REPLY_LANGUAGE_OPTIONS = ["Auto", "English", "Spanish", "Bilingual"]
REPLY_TONE_OPTIONS = ["Professional", "Concise", "Friendly", "Apology / Delay"]

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


def _subject_is_reply(subject: str) -> bool:
    return bool(re.match(r"^\s*(?:re|fw|fwd)\s*:", _safe_str(subject), re.I))


def _is_information_update(text: str) -> bool:
    lowered = str(text or "").lower()
    if _contains_any(lowered, INFORMATION_UPDATE_TERMS):
        return True
    if re.search(r"\bplease\s+have\b.{0,80}\b(?:container|load|truck)\b", lowered, re.I):
        return True
    if re.search(r"\b(?:about|around|at|by)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:-|to)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", lowered, re.I):
        return True
    return bool(re.search(r"\b(?:please\s+note|note)\b.{0,80}\b(?:hours?|schedule|address|cutoff|lfd)\b", lowered, re.I))


def _has_order_placement_signal(text: str) -> bool:
    lowered = str(text or "").lower()
    if _contains_any(lowered, ORDER_PLACEMENT_TERMS):
        return True
    return bool(
        re.search(
            r"\b(?:please|pls)\b.{0,60}\b(?:arrange|book|schedule|dispatch|pickup|pick up|deliver|handle|process)\b",
            lowered,
            re.I,
        )
    )


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
    has_order_signal = _has_order_placement_signal(text)
    has_order_document_signal = _contains_any(text, ["delivery order", "load order", "work order", "tender", "tendered"]) and not _is_information_update(text)
    if not has_order_signal and not has_order_document_signal:
        return False

    detail_score = 0
    for field in ["Booking Number", "Customer", "Container Number", "Port", "Warehouse", "Delivery Need Date"]:
        if _safe_str(parsed.get(field, "")):
            detail_score += 1
    if _has_reference_details(tokens, parsed):
        detail_score += 1
    if has_order_signal or has_order_document_signal:
        detail_score += 1

    return detail_score >= 3


def _operations_intent_scores(subject: str, body: str, parsed: dict | None = None) -> dict[str, int]:
    text = f"{subject or ''} {body or ''}"
    parsed = _coerce_parsed_for_classification(subject, body, parsed)
    tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    has_reference = _has_reference_details(tokens, parsed)
    is_info_update = _is_information_update(text)
    has_order_signal = _has_order_placement_signal(text)

    scores = {request_type: 0 for request_type in REQUEST_TYPES}
    scores["Customer Request"] = 20

    def add(request_type: str, points: int, condition: bool = True) -> None:
        if condition and request_type in scores:
            scores[request_type] += points

    add("Missing Information", 70, _contains_any(text, MISSING_INFO_TERMS))
    add("Cancellation", 75, _contains_any(text, CANCELLATION_TERMS))
    add("POD Request", 75, _contains_any(text, POD_TERMS))
    add("Appointment Update", 70, _contains_any(text, APPOINTMENT_INTENT_TERMS))
    add("Quote Request", 70, _contains_any(text, QUOTE_INTENT_TERMS))
    add("Booking Update", 60, _contains_any(text, UPDATE_INTENT_TERMS))
    add("New Booking", 65, _contains_any(text, NEW_ORDER_INTENT_TERMS))
    add("Billing", 75, _contains_any(text, BILLING_TERMS))
    add("Driver Issue", 70, _contains_any(text, DRIVER_ISSUE_TERMS))
    add("Port Issue", 70, _contains_any(text, PORT_ISSUE_TERMS))
    add("Spam/Marketing", 85, _contains_any(text, SPAM_MARKETING_TERMS))
    add("Booking Update", 45, is_info_update and has_reference)
    add("Customer Request", 35, is_info_update and not has_reference)

    if has_reference:
        for request_type in [
            "Booking Update",
            "Appointment Update",
            "POD Request",
            "Cancellation",
            "Billing",
            "Driver Issue",
            "Port Issue",
        ]:
            add(request_type, 18)
    else:
        for request_type in ["Booking Update", "Appointment Update", "POD Request", "Cancellation"]:
            scores[request_type] = max(0, scores[request_type] - 35)

    if _has_quote_details(text, parsed, tokens):
        add("Quote Request", 25)
    else:
        scores["Quote Request"] = max(0, scores["Quote Request"] - 30)

    if _has_new_order_details(text, parsed, tokens):
        add("New Booking", 35)
    else:
        scores["New Booking"] = max(0, scores["New Booking"] - 35)

    if is_info_update and not has_order_signal:
        scores["New Booking"] = max(0, scores["New Booking"] - 70)
        if has_reference:
            scores["Booking Update"] = max(scores["Booking Update"], 78)
        else:
            scores["Customer Request"] = max(scores["Customer Request"], 65)

    if _safe_str(parsed.get(OPERATIONS_PDF_ATTACHMENTS_KEY, "")) or _safe_str(parsed.get("Booking Number", "")):
        add("New Booking", 15, has_order_signal and not is_info_update)
        add("Booking Update", 10)

    if max(scores.values() or [0]) < 45:
        scores["Customer Request"] = max(scores["Customer Request"], 50)

    return scores


def classify_customer_request(subject: str, body: str, parsed: dict | None = None) -> str:
    text = f"{subject or ''} {body or ''}"
    parsed = _coerce_parsed_for_classification(subject, body, parsed)
    tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    has_reference = _has_reference_details(tokens, parsed)
    if _is_information_update(text) and not _has_order_placement_signal(text):
        return "Booking Update" if has_reference else "Customer Request"

    scores = _operations_intent_scores(subject, body, parsed)
    scored_types = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_type = scored_types[0][0] if scored_types else "Customer Request"

    if _contains_any(text, MISSING_INFO_TERMS):
        return "Missing Information"

    if best_type == "Spam/Marketing":
        return "Spam/Marketing"

    if best_type == "Quote Request":
        return "Quote Request" if _has_quote_details(text, parsed, tokens) else "Customer Request"

    if best_type == "New Booking":
        return "New Booking" if _has_new_order_details(text, parsed, tokens) else "Customer Request"

    if best_type in ["Cancellation", "POD Request", "Appointment Update", "Booking Update"]:
        return best_type if has_reference else "Customer Request"

    if best_type in ["Billing", "Driver Issue", "Port Issue"]:
        return best_type

    return best_type if best_type in REQUEST_TYPES else "Customer Request"


def _extract_load_id_hint(text: str) -> str:
    match = re.search(r"\b(?:load|order)\s*(?:id|#|number)?\s*[:#-]?\s*(\d{2,})\b", str(text or ""), re.I)
    return match.group(1) if match else ""


def _extract_date_hint(text: str) -> str:
    match = re.search(r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", str(text or ""))
    if not match:
        return ""
    parsed = pd.to_datetime(match.group(1), errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def _row_match_text(row: dict, column: str) -> str:
    return _safe_str(row.get(column, "")).upper()


def _score_load_match_row(row: dict, search: dict) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    checks = [
        ("booking", "booking_number", 100, "Booking"),
        ("container", "container_number", 98, "Container"),
        ("reference", "reference_number", 90, "Reference"),
        ("load_id", "id", 95, "Load ID"),
        ("load_id", "load_id", 90, "External Load ID"),
        ("vessel", "vessel_name", 65, "Vessel"),
    ]
    for search_key, column, points, label in checks:
        needle = _safe_str(search.get(search_key, "")).upper()
        haystack = _row_match_text(row, column)
        if needle and haystack and (needle == haystack or needle in haystack):
            score = max(score, points)
            reasons.append(label)

    customer = _safe_str(search.get("customer", "")).lower()
    row_customer = _safe_str(row.get("customer", "")).lower()
    date_hint = _safe_str(search.get("date", ""))
    row_date = _safe_str(row.get("delivery_need_date", ""))
    date_matches = date_hint and date_hint in row_date
    if customer and len(customer) >= 4 and customer in row_customer:
        score = max(score, 55)
        reasons.append("Customer")
        if date_matches:
            score = max(score, 82)
            reasons.append("Date")
    elif date_matches:
        score = max(score, 45)
        reasons.append("Date")

    return score, reasons


def find_load_match_candidates(
    tokens: dict,
    parsed: dict | None = None,
    subject: str = "",
    body: str = "",
    limit: int = 5,
) -> list[dict]:
    parsed = parsed or {}
    text = f"{subject or ''} {body or ''} {parsed}"
    existing_columns = _existing_load_columns()
    select_columns = [
        column
        for column in [
            "id",
            "load_id",
            "booking_number",
            "reference_number",
            "container_number",
            "customer",
            "delivery_need_date",
            "status",
            "driver_name",
            "pickup_appointment",
            "delivery_appointment",
            "vessel_name",
            "updated_at",
        ]
        if column in existing_columns
    ]
    if "id" not in select_columns:
        return []

    search = {
        "booking": _safe_str(tokens.get("booking_number") or parsed.get("Booking Number", "")),
        "container": _safe_str(tokens.get("container_number") or parsed.get("Container Number", "")),
        "reference": _safe_str(tokens.get("reference_number") or parsed.get("Reference Number", "")),
        "load_id": _extract_load_id_hint(text),
        "customer": _safe_str(parsed.get("Customer", "")),
        "date": _extract_date_hint(text),
        "vessel": _safe_str(parsed.get("Vessel", "") or parsed.get("Vessel Name", "")),
    }

    conditions = []
    params = {"limit": max(int(limit) * 4, 20)}
    if search["booking"] and "booking_number" in existing_columns:
        conditions.append("lower(coalesce(booking_number, '')) like lower(:booking_like)")
        params["booking_like"] = f"%{search['booking']}%"
    if search["container"] and "container_number" in existing_columns:
        conditions.append("lower(coalesce(container_number, '')) like lower(:container_like)")
        params["container_like"] = f"%{search['container']}%"
    if search["reference"] and "reference_number" in existing_columns:
        conditions.append("lower(coalesce(reference_number, '')) like lower(:reference_like)")
        params["reference_like"] = f"%{search['reference']}%"
    if search["load_id"]:
        conditions.append("cast(id as text) = :load_id")
        params["load_id"] = search["load_id"]
        if "load_id" in existing_columns:
            conditions.append("lower(coalesce(load_id, '')) = lower(:external_load_id)")
            params["external_load_id"] = search["load_id"]
    if search["customer"] and len(search["customer"]) >= 4 and "customer" in existing_columns:
        conditions.append("lower(coalesce(customer, '')) like lower(:customer_like)")
        params["customer_like"] = f"%{search['customer']}%"
    if search["vessel"] and "vessel_name" in existing_columns:
        conditions.append("lower(coalesce(vessel_name, '')) like lower(:vessel_like)")
        params["vessel_like"] = f"%{search['vessel']}%"

    if not conditions:
        return []

    order_clause = "updated_at desc nulls last, id desc" if "updated_at" in existing_columns else "id desc"
    try:
        match_df = read_df(
            f"""
            select {", ".join(select_columns)}
            from loads
            where {" or ".join(conditions)}
            order by {order_clause}
            limit :limit
            """,
            params,
        )
    except Exception:
        return []

    candidates = []
    for _, row in match_df.iterrows():
        row_dict = row.to_dict()
        score, reasons = _score_load_match_row(row_dict, search)
        if score <= 0:
            continue
        candidates.append(
            {
                "Load ID": int(row_dict["id"]),
                "External Load ID": _safe_str(row_dict.get("load_id", "")),
                "Booking Number": _safe_str(row_dict.get("booking_number", "")),
                "Container Number": _safe_str(row_dict.get("container_number", "")),
                "Reference Number": _safe_str(row_dict.get("reference_number", "")),
                "Customer": _safe_str(row_dict.get("customer", "")),
                "Status": _safe_str(row_dict.get("status", "")),
                "Driver": _safe_str(row_dict.get("driver_name", "")),
                "Pickup Appointment": _safe_str(row_dict.get("pickup_appointment", "")),
                "Delivery Appointment": _safe_str(row_dict.get("delivery_appointment", "")),
                "Vessel": _safe_str(row_dict.get("vessel_name", "")),
                "Match Score": int(score),
                "Match Reason": ", ".join(reasons),
            }
        )

    candidates = sorted(candidates, key=lambda item: item["Match Score"], reverse=True)
    return candidates[: int(limit)]


def find_matching_load(tokens: dict, parsed: dict | None = None, subject: str = "", body: str = "") -> tuple[int | None, int]:
    candidates = find_load_match_candidates(tokens, parsed=parsed, subject=subject, body=body, limit=5)
    if not candidates:
        return None, 0
    top = candidates[0]
    top_score = int(top.get("Match Score", 0) or 0)
    second_score = int(candidates[1].get("Match Score", 0) or 0) if len(candidates) > 1 else 0
    if top_score >= 90 and top_score - second_score >= 5:
        return int(top["Load ID"]), top_score
    if top_score >= 98:
        return int(top["Load ID"]), top_score
    return None, top_score


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

def save_load_communication(
    load_id,
    intake_id,
    conversation_key,
    request_type,
    subject,
    sender,
    body,
    direction: str = "inbound",
    case_id=None,
) -> None:
    execute(
        """
        insert into load_communications (
            load_id,
            intake_id,
            case_id,
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
            :case_id,
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
            "case_id": _int_or_none(case_id),
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


OPERATIONS_ATTACHMENTS_KEY = "_operations_attachments"
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
    "Contact Name",
    "Contact Email",
    "Contact Phone",
    "Contact Company",
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


def _is_pdf_filename(filename: str, content_type: str = "") -> bool:
    return _safe_str(filename).lower().endswith(".pdf") or _safe_str(content_type).lower() == "application/pdf"


def _decode_text_attachment(content: bytes) -> str:
    for encoding in ["utf-8", "latin-1", "cp1252"]:
        try:
            return (content or b"").decode(encoding, errors="ignore").strip()
        except Exception:
            continue
    return ""


def _extract_docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(content or b"")) as archive:
            xml_data = archive.read("word/document.xml")
    except Exception:
        return ""

    try:
        root = ET.fromstring(xml_data)
    except Exception:
        return ""

    text_nodes = []
    for node in root.iter():
        if node.tag.endswith("}t") and node.text:
            text_nodes.append(node.text)
        elif node.tag.endswith("}p"):
            text_nodes.append("\n")
    return " ".join(text_nodes).replace(" \n ", "\n").strip()


def _parse_operations_pdf_bytes(content: bytes, filename: str) -> tuple[str, dict]:
    pdf_file = BytesIO(content or b"")
    pdf_file.name = filename or "attachment.pdf"
    pdf_text = extract_text_from_pdf(pdf_file)
    pdf_parsed = parse_order_text(pdf_text) if pdf_text else {}
    return pdf_text, pdf_parsed


def _parse_operations_attachment_bytes(content: bytes, filename: str, content_type: str = "") -> tuple[str, dict]:
    filename_lower = _safe_str(filename).lower()
    content_type = _safe_str(content_type).lower()

    if _is_pdf_filename(filename, content_type):
        return _parse_operations_pdf_bytes(content, filename)

    if filename_lower.endswith(".docx") or content_type.endswith("wordprocessingml.document"):
        text = _extract_docx_text(content)
        return text, parse_order_text(text) if text else {}

    if filename_lower.endswith((".txt", ".csv", ".tsv")) or content_type.startswith("text/"):
        text = _decode_text_attachment(content)
        return text, parse_order_text(text) if text else {}

    return "", {}


def _field_count(parsed: dict) -> int:
    return sum(1 for field in OPERATIONS_ORDER_FIELDS if _safe_str(parsed.get(field, "")))


def _save_operations_attachment(
    *,
    content: bytes,
    filename: str,
    message_id: str,
    attachment_index: int,
    content_type: str = "",
) -> dict:
    safe_message = _safe_storage_name(message_id, "operations_email")[:90]
    fallback_extension = ".pdf" if _safe_str(content_type).lower() == "application/pdf" else ""
    safe_filename = _safe_storage_name(filename, f"attachment_{attachment_index}{fallback_extension}")
    stored_path = _operations_pdf_storage_dir() / f"{safe_message}_{attachment_index}_{safe_filename}"
    stored_path.write_bytes(content or b"")

    try:
        attachment_text, attachment_parsed = _parse_operations_attachment_bytes(content or b"", safe_filename, content_type)
        parse_error = ""
    except Exception as exc:
        attachment_text = ""
        attachment_parsed = {}
        parse_error = str(exc)

    is_pdf = _is_pdf_filename(safe_filename, content_type)
    return {
        "filename": safe_filename,
        "file_path": str(stored_path),
        "content_type": content_type or ("application/pdf" if is_pdf else "application/octet-stream"),
        "is_pdf": is_pdf,
        "parsed_data": attachment_parsed,
        "fields_found": _field_count(attachment_parsed),
        "text_preview": attachment_text[:1800],
        "parse_error": parse_error,
        "size_bytes": len(content or b""),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


def _save_operations_pdf_attachment(
    *,
    content: bytes,
    filename: str,
    message_id: str,
    attachment_index: int,
) -> dict:
    return _save_operations_attachment(
        content=content,
        filename=filename,
        message_id=message_id,
        attachment_index=attachment_index,
        content_type="application/pdf",
    )


def _extract_operations_attachments(parsed: dict, record: dict | pd.Series | None = None) -> list[dict]:
    attachments = parsed.get(OPERATIONS_ATTACHMENTS_KEY, [])
    if not isinstance(attachments, list):
        attachments = []

    normalized = [item for item in attachments if isinstance(item, dict)]
    for pdf_item in parsed.get(OPERATIONS_PDF_ATTACHMENTS_KEY, []) or []:
        if isinstance(pdf_item, dict) and not any(_safe_str(item.get("file_path", "")) == _safe_str(pdf_item.get("file_path", "")) for item in normalized):
            normalized.append(pdf_item)

    if record is not None:
        filename = _safe_str(record.get("filename", "") if hasattr(record, "get") else "")
        file_path = _safe_str(record.get("file_path", "") if hasattr(record, "get") else "")
        if filename and file_path and not any(_safe_str(item.get("file_path", "")) == file_path for item in normalized):
            normalized.append(
                {
                    "filename": filename,
                    "file_path": file_path,
                    "content_type": "application/pdf" if filename.lower().endswith(".pdf") else "application/octet-stream",
                    "is_pdf": filename.lower().endswith(".pdf"),
                    "parsed_data": {},
                    "fields_found": 0,
                    "text_preview": "",
                    "parse_error": "",
                }
            )

    return normalized


def _extract_operations_pdf_attachments(parsed: dict, record: dict | pd.Series | None = None) -> list[dict]:
    return [
        item
        for item in _extract_operations_attachments(parsed, record)
        if _is_pdf_filename(item.get("filename", ""), item.get("content_type", "")) or bool(item.get("is_pdf"))
    ]


def _merge_operations_order_fields(body_parsed: dict, pdf_parsed: dict) -> tuple[dict, list[dict], list[str]]:
    final_data = {}
    rows = []
    conflicts = []

    for field in OPERATIONS_ORDER_FIELDS:
        body_value = _safe_str(body_parsed.get(field, ""))
        pdf_value = _safe_str(pdf_parsed.get(field, ""))
        if field == "Dispatcher Notes" and body_value and pdf_value:
            final_value = body_value if pdf_value in body_value else f"{body_value}\n{pdf_value}"
        else:
            final_value = pdf_value or body_value
        final_data[field] = final_value

        if field == "Dispatcher Notes" and body_value and pdf_value:
            status = "Combined"
        elif body_value and pdf_value and body_value.lower() != pdf_value.lower():
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
                "Document": pdf_value,
                "Final Value": final_value,
                "Status": status,
            }
        )

    return final_data, rows, conflicts


def _merge_operations_body_parsed_fields(current: dict, reparsed: dict) -> tuple[dict, bool]:
    updated = dict(current or {})
    changed = False

    for field in OPERATIONS_ORDER_FIELDS:
        existing_value = _safe_str(updated.get(field, ""))
        incoming_value = _safe_str((reparsed or {}).get(field, ""))
        if not incoming_value:
            continue

        should_replace = not existing_value
        if field == "Container Number" and incoming_value and existing_value.upper() != incoming_value.upper():
            incoming_notes = _safe_str((reparsed or {}).get("Dispatcher Notes", ""))
            should_replace = "container correction noted" in incoming_notes.lower()

        if should_replace:
            updated[field] = incoming_value
            changed = True

    current_notes = _safe_str(updated.get("Dispatcher Notes", ""))
    incoming_notes = _safe_str((reparsed or {}).get("Dispatcher Notes", ""))
    if incoming_notes and incoming_notes.lower() not in current_notes.lower():
        updated["Dispatcher Notes"] = f"{current_notes}; {incoming_notes}".strip("; ").strip()
        changed = True

    return updated, changed


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


def _read_operations_attachment_bytes(file_path: str) -> bytes:
    path = Path(file_path)
    return _read_operations_pdf_file(str(path), path.stat().st_mtime_ns)


def _read_operations_pdf_bytes(file_path: str) -> bytes:
    return _read_operations_attachment_bytes(file_path)


@st.cache_data(show_spinner=False, ttl=900)
def _parse_operations_pdf_file(file_path: str, filename: str, modified_ns: int) -> tuple[str, dict]:
    content = Path(file_path).read_bytes()
    return _parse_operations_pdf_bytes(content, filename)


def _parse_saved_operations_pdf(file_path: str, filename: str) -> tuple[str, dict]:
    path = Path(file_path)
    return _parse_operations_pdf_file(str(path), filename, path.stat().st_mtime_ns)


@st.cache_data(show_spinner=False, ttl=900)
def _parse_operations_attachment_file(file_path: str, filename: str, content_type: str, modified_ns: int) -> tuple[str, dict]:
    content = Path(file_path).read_bytes()
    return _parse_operations_attachment_bytes(content, filename, content_type)


def _parse_saved_operations_attachment(file_path: str, filename: str, content_type: str = "") -> tuple[str, dict]:
    path = Path(file_path)
    return _parse_operations_attachment_file(str(path), filename, content_type, path.stat().st_mtime_ns)


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


def _sql_literal_list(values: list[str]) -> str:
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)


def _operations_email_source_filter(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"{prefix}source in ({_sql_literal_list(OPERATIONS_EMAIL_SYNC_SOURCES)})"


def _conversation_join_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"coalesce("
        f"nullif({prefix}conversation_key, ''), "
        f"nullif({prefix}email_thread_id, ''), "
        f"nullif({prefix}source_message_id, ''), "
        f"nullif({prefix}email_normalized_subject, ''), "
        f"lower(coalesce({prefix}source_subject, ''))"
        f")"
    )


def _ensure_operations_email_sync_schema() -> None:
    if st.session_state.get("_operations_email_sync_schema_ready"):
        _ensure_operations_case_schema()
        return

    execute("alter table order_intake add column if not exists email_direction text not null default 'inbound'")
    execute("alter table order_intake add column if not exists email_mailbox text")
    execute("alter table order_intake add column if not exists email_in_reply_to text")
    execute("alter table order_intake add column if not exists email_references jsonb not null default '[]'::jsonb")
    execute("alter table order_intake add column if not exists email_thread_id text")
    execute("alter table order_intake add column if not exists email_normalized_subject text")
    execute("alter table order_intake add column if not exists conversation_status text not null default 'New Conversation'")
    execute("alter table order_intake add column if not exists email_synced_at timestamptz")
    execute("create index if not exists idx_order_intake_email_thread_id on order_intake(email_thread_id)")
    execute("create index if not exists idx_order_intake_email_normalized_subject on order_intake(email_normalized_subject)")
    execute("create index if not exists idx_order_intake_conversation_status on order_intake(conversation_status)")
    execute("create index if not exists idx_order_intake_email_direction on order_intake(email_direction)")
    execute("create index if not exists idx_order_intake_email_mailbox on order_intake(email_mailbox)")
    _ensure_operations_case_schema()
    st.session_state["_operations_email_sync_schema_ready"] = True


def _ensure_operations_case_schema() -> None:
    if st.session_state.get("_operations_case_schema_ready"):
        return

    execute(
        """
        create table if not exists operations_cases (
            id bigserial primary key,
            case_number text unique not null,
            conversation_key text,
            status text not null default 'New',
            owner text not null default 'Unassigned',
            priority text not null default 'Normal',
            customer text,
            source_subject text,
            request_type text,
            linked_load_id bigint references loads(id) on delete set null,
            next_action text,
            last_message_direction text,
            last_message_at timestamptz,
            message_count integer not null default 0,
            first_response_due_at timestamptz,
            first_response_at timestamptz,
            resolution_due_at timestamptz,
            resolved_at timestamptz,
            customer_wait_started_at timestamptz,
            department_wait_started_at timestamptz,
            sla_status text not null default 'On Track',
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            closed_at timestamptz,
            reopened_at timestamptz
        )
        """
    )
    execute(
        """
        create table if not exists operations_case_notes (
            id bigserial primary key,
            case_id bigint references operations_cases(id) on delete cascade,
            note_body text not null,
            note_type text not null default 'internal',
            created_by text not null default 'dispatcher',
            created_at timestamptz not null default now()
        )
        """
    )
    execute(
        """
        create table if not exists operations_case_owner_history (
            id bigserial primary key,
            case_id bigint references operations_cases(id) on delete cascade,
            old_owner text,
            new_owner text not null,
            changed_by text not null default 'dispatcher',
            changed_at timestamptz not null default now()
        )
        """
    )
    execute(
        """
        create table if not exists operations_case_events (
            id bigserial primary key,
            case_id bigint references operations_cases(id) on delete cascade,
            event_type text not null,
            title text,
            details text,
            actor text not null default 'system',
            department text,
            created_at timestamptz not null default now()
        )
        """
    )
    execute("alter table operations_cases add column if not exists first_response_due_at timestamptz")
    execute("alter table operations_cases add column if not exists first_response_at timestamptz")
    execute("alter table operations_cases add column if not exists resolution_due_at timestamptz")
    execute("alter table operations_cases add column if not exists resolved_at timestamptz")
    execute("alter table operations_cases add column if not exists customer_wait_started_at timestamptz")
    execute("alter table operations_cases add column if not exists department_wait_started_at timestamptz")
    execute("alter table operations_cases add column if not exists sla_status text not null default 'On Track'")
    execute(
        """
        update operations_cases
        set first_response_due_at = coalesce(first_response_due_at, created_at + interval '2 hours'),
            resolution_due_at = coalesce(resolution_due_at, created_at + interval '48 hours')
        """
    )
    execute("alter table order_intake add column if not exists case_id bigint references operations_cases(id) on delete set null")
    execute("alter table load_communications add column if not exists case_id bigint references operations_cases(id) on delete set null")
    execute("alter table operations_email_replies add column if not exists case_id bigint references operations_cases(id) on delete set null")
    execute("create index if not exists idx_operations_cases_conversation_key on operations_cases(conversation_key)")
    execute("create index if not exists idx_operations_cases_status on operations_cases(status)")
    execute("create index if not exists idx_operations_cases_owner on operations_cases(owner)")
    execute("create index if not exists idx_operations_cases_linked_load_id on operations_cases(linked_load_id)")
    execute("create index if not exists idx_operations_cases_updated_at on operations_cases(updated_at desc)")
    execute("create index if not exists idx_operations_cases_sla_status on operations_cases(sla_status)")
    execute("create index if not exists idx_operations_cases_first_response_due_at on operations_cases(first_response_due_at)")
    execute("create index if not exists idx_operations_cases_resolution_due_at on operations_cases(resolution_due_at)")
    execute("create index if not exists idx_operations_case_notes_case_id on operations_case_notes(case_id)")
    execute("create index if not exists idx_operations_case_owner_history_case_id on operations_case_owner_history(case_id)")
    execute("create index if not exists idx_operations_case_events_case_id on operations_case_events(case_id)")
    execute("create index if not exists idx_operations_case_events_created_at on operations_case_events(created_at)")
    execute("create index if not exists idx_order_intake_case_id on order_intake(case_id)")
    execute("create index if not exists idx_load_communications_case_id on load_communications(case_id)")
    execute("create index if not exists idx_operations_email_replies_case_id on operations_email_replies(case_id)")
    st.session_state["_operations_case_schema_ready"] = True


def _inbox_review_where_clause() -> str:
    terminal = ", ".join([f"'{status}'" for status in INBOX_TERMINAL_REVIEW_STATUSES])
    return f"where coalesce(review_status, 'Open') not in ({terminal})"


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_inbox_df(where_clause: str) -> pd.DataFrame:
    return read_df(
        f"""
        select
            oi.id,
            oi.created_at,
            oi.source_received_at,
            oi.source,
            oi.source_subject,
            oi.source_sender,
            oi.source_message_id,
            oi.email_direction,
            oi.email_mailbox,
            oi.email_thread_id,
            oi.email_normalized_subject,
            oi.conversation_status,
            oi.email_in_reply_to,
            oi.email_references,
            oi.filename,
            oi.file_path,
            oi.parsed_data,
            left(coalesce(oi.raw_text, ''), 1200) as raw_text_preview,
            case
                when jsonb_typeof(oi.parsed_data -> :pdf_attachments_key) = 'array'
                    then jsonb_array_length(oi.parsed_data -> :pdf_attachments_key)
                when oi.filename is not null and oi.filename <> '' then 1
                else 0
            end as pdf_count,
            case
                when jsonb_typeof(oi.parsed_data -> :attachments_key) = 'array'
                    then jsonb_array_length(oi.parsed_data -> :attachments_key)
                when jsonb_typeof(oi.parsed_data -> :pdf_attachments_key) = 'array'
                    then jsonb_array_length(oi.parsed_data -> :pdf_attachments_key)
                when oi.filename is not null and oi.filename <> '' then 1
                else 0
            end as attachment_count,
            case
                when coalesce(oi.parsed_data #>> '{{_email_sync,source_attachment_count}}', '') ~ '^[0-9]+$'
                    then (oi.parsed_data #>> '{{_email_sync,source_attachment_count}}')::int
                else 0
            end as source_attachment_count,
            oi.intake_status,
            oi.request_type,
            oi.conversation_key,
            oi.matched_load_id,
            oi.case_id,
            oc.case_number,
            oc.status as case_status,
            oc.owner as case_owner,
            oc.priority as case_priority,
            oc.customer as case_customer,
            oc.linked_load_id as case_linked_load_id,
            oc.next_action as case_next_action,
            oc.sla_status as case_sla_status,
            oc.message_count as case_message_count,
            oc.last_message_at as case_last_message_at,
            oc.last_message_direction as case_last_message_direction,
            oc.first_response_due_at as case_first_response_due_at,
            oc.resolution_due_at as case_resolution_due_at,
            oc.customer_wait_started_at as case_customer_wait_started_at,
            oc.department_wait_started_at as case_department_wait_started_at,
            oi.confidence_score,
            oi.action_required,
            oi.review_status
        from (
            select *
            from order_intake
            {where_clause}
        ) oi
        left join operations_cases oc on oc.id = oi.case_id
        order by oi.created_at desc
        """,
        {
            "pdf_attachments_key": OPERATIONS_PDF_ATTACHMENTS_KEY,
            "attachments_key": OPERATIONS_ATTACHMENTS_KEY,
        },
    )


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_inbox_record(intake_id: int) -> pd.DataFrame:
    return read_df(
        """
        select
            oi.id,
            oi.created_at,
            oi.source_received_at,
            oi.source,
            oi.source_subject,
            oi.source_sender,
            oi.source_message_id,
            oi.email_direction,
            oi.email_mailbox,
            oi.email_thread_id,
            oi.email_normalized_subject,
            oi.conversation_status,
            oi.email_in_reply_to,
            oi.email_references,
            oi.filename,
            oi.file_path,
            oi.parsed_data,
            oi.raw_text,
            oi.intake_status,
            oi.request_type,
            oi.conversation_key,
            oi.matched_load_id,
            oi.case_id,
            oc.case_number,
            oc.status as case_status,
            oc.owner as case_owner,
            oc.priority as case_priority,
            oc.customer as case_customer,
            oc.linked_load_id as case_linked_load_id,
            oc.next_action as case_next_action,
            oc.sla_status as case_sla_status,
            oc.message_count as case_message_count,
            oc.last_message_at as case_last_message_at,
            oc.last_message_direction as case_last_message_direction,
            oc.first_response_due_at as case_first_response_due_at,
            oc.resolution_due_at as case_resolution_due_at,
            oc.customer_wait_started_at as case_customer_wait_started_at,
            oc.department_wait_started_at as case_department_wait_started_at,
            oi.confidence_score,
            oi.action_required,
            oi.review_status
        from order_intake oi
        left join operations_cases oc on oc.id = oi.case_id
        where oi.id = :intake_id
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
            source_sender,
            source_message_id,
            email_direction,
            email_thread_id,
            email_normalized_subject,
            conversation_status,
            parsed_data,
            raw_text,
            request_type,
            conversation_key,
            matched_load_id,
            case_id,
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
    subject_series = inbox_df["source_subject"].fillna("").astype(str) if "source_subject" in inbox_df.columns else pd.Series("", index=inbox_df.index)
    body_series = (
        inbox_df["raw_text"].fillna("").astype(str).apply(extract_latest_email_body)
        if "raw_text" in inbox_df.columns
        else pd.Series("", index=inbox_df.index)
    )
    message_series = subject_series + "\n" + body_series
    obvious_info_new_booking = current_type.eq("New Booking") & message_series.apply(
        lambda value: _is_information_update(value) and not _has_order_placement_signal(value)
    )

    action_type_needs_reference = current_type.isin([
        "New Booking",
        "Booking Update",
        "Appointment Update",
        "Quote Request",
        "Cancellation",
        "POD Request",
        "Billing",
        "Driver Issue",
        "Port Issue",
    ]) & ~has_match & confidence.lt(70)

    return current_type.isin(["", "Needs Classification", "Other", "Spam/Marketing"]) | action_type_needs_reference | obvious_info_new_booking


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_conversation_summary_df() -> pd.DataFrame:
    conversation_key_expr = _conversation_join_expr()
    try:
        return read_df(
            f"""
            select
                {conversation_key_expr} as conversation_join_key,
                count(*) as conversation_message_count,
                max(source_received_at) as last_message_at,
                (array_agg(coalesce(email_direction, 'inbound') order by source_received_at desc nulls last, created_at desc))[1] as latest_direction,
                (array_agg(coalesce(source_sender, '') order by source_received_at desc nulls last, created_at desc))[1] as latest_sender,
                (array_agg(coalesce(conversation_status, 'New Conversation') order by source_received_at desc nulls last, created_at desc))[1] as latest_conversation_status,
                max(case when coalesce(email_direction, 'inbound') = 'inbound' then source_received_at end) as last_inbound_at,
                max(case when coalesce(email_direction, 'inbound') = 'outbound' then source_received_at end) as last_outbound_at,
                max(matched_load_id) as thread_matched_load_id
            from order_intake
            where {_operations_email_source_filter()}
            group by {conversation_key_expr}
            """
        )
    except Exception:
        return pd.DataFrame()


def _row_conversation_join_key(row) -> str:
    for key in ["conversation_key", "email_thread_id", "source_message_id", "email_normalized_subject", "source_subject"]:
        value = _safe_str(row.get(key, "") if hasattr(row, "get") else "")
        if value:
            return value.lower() if key == "source_subject" else value
    return ""


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_conversation_timeline(conversation_key: str) -> pd.DataFrame:
    if not _safe_str(conversation_key):
        return pd.DataFrame()

    conversation_key_expr = _conversation_join_expr()
    try:
        return read_df(
            f"""
            select
                id,
                source_received_at,
                created_at,
                coalesce(email_direction, 'inbound') as email_direction,
                coalesce(email_mailbox, '') as email_mailbox,
                coalesce(source_sender, '') as source_sender,
                coalesce(source_subject, '') as source_subject,
                coalesce(source_message_id, '') as source_message_id,
                coalesce(email_thread_id, '') as email_thread_id,
                coalesce(conversation_key, '') as conversation_key,
                matched_load_id,
                parsed_data,
                coalesce(conversation_status, 'New Conversation') as conversation_status,
                coalesce(review_status, 'Open') as review_status,
                left(coalesce(raw_text, ''), 1200) as message_preview
            from order_intake
            where {_operations_email_source_filter()}
              and {conversation_key_expr} = :conversation_key
            order by coalesce(source_received_at, created_at) asc, id asc
            """,
            {"conversation_key": conversation_key},
        )
    except Exception:
        return pd.DataFrame()


def _timeline_filter_tokens(record, tokens: dict, subject: str, body: str) -> set[str]:
    parsed = _coerce_json_dict(record.get("parsed_data") if hasattr(record, "get") else {})
    candidates = [
        tokens.get("booking_number", ""),
        tokens.get("container_number", ""),
        tokens.get("reference_number", ""),
        parsed.get("Booking Number", ""),
        parsed.get("Container Number", ""),
        parsed.get("Reference Number", ""),
        record.get("matched_load_id", "") if hasattr(record, "get") else "",
    ]
    text_tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    candidates.extend([text_tokens.get("booking_number", ""), text_tokens.get("container_number", ""), text_tokens.get("reference_number", "")])
    return {_safe_str(value).upper() for value in candidates if len(_safe_str(value)) >= 4}


def _filter_operations_timeline_for_record(
    timeline_df: pd.DataFrame,
    record,
    tokens: dict,
    subject: str,
    body: str,
) -> pd.DataFrame:
    if timeline_df.empty:
        return timeline_df

    selected_id = _int_or_none(record.get("id") if hasattr(record, "get") else None)
    selected_thread = _safe_str(record.get("email_thread_id", "") if hasattr(record, "get") else "")
    selected_message_id = _safe_str(record.get("source_message_id", "") if hasattr(record, "get") else "")
    token_values = _timeline_filter_tokens(record, tokens, subject, body)
    if not token_values:
        return timeline_df

    def row_matches(row) -> bool:
        row_id = _int_or_none(row.get("id"))
        if selected_id is not None and row_id == selected_id:
            return True
        if selected_message_id and _safe_str(row.get("source_message_id", "")) == selected_message_id:
            return True

        haystack = " ".join(
            [
                _safe_str(row.get("source_subject", "")),
                _safe_str(row.get("message_preview", "")),
                _safe_str(row.get("conversation_key", "")),
                _safe_str(row.get("parsed_data", "")),
                _safe_str(row.get("matched_load_id", "")),
            ]
        ).upper()
        if any(value and value in haystack for value in token_values):
            return True

        if selected_thread and _safe_str(row.get("email_thread_id", "")) == selected_thread:
            row_tokens = _extract_reference_tokens(haystack)
            row_token_values = {_safe_str(value).upper() for value in row_tokens.values() if _safe_str(value)}
            return bool(token_values & row_token_values)

        return False

    filtered = timeline_df[timeline_df.apply(row_matches, axis=1)].copy()
    return filtered if not filtered.empty else timeline_df


def _conversation_context_from_lookup(lookup: dict, metadata: dict) -> dict:
    candidates = []
    for key in [metadata.get("conversation_key"), metadata.get("thread_id")]:
        key = _safe_str(key)
        if key:
            candidates.extend(lookup.get("by_thread_id", {}).get(key, []))

    normalized_subject = _safe_str(metadata.get("normalized_subject", ""))
    if normalized_subject:
        candidates.extend(lookup.get("by_normalized_subject", {}).get(normalized_subject, []))

    if not candidates:
        return {}

    def score(record: dict) -> tuple[int, str]:
        has_match = 1 if _safe_str(record.get("matched_load_id", "")) else 0
        direction_score = 1 if _safe_str(record.get("email_direction", "")).lower() == "inbound" else 0
        return (has_match + direction_score, _safe_str(record.get("source_received_at", "")))

    candidates = sorted(candidates, key=score, reverse=True)
    chosen = candidates[0]
    matched_load_id = chosen.get("matched_load_id")
    if not _safe_str(matched_load_id):
        matched_load_id = None
    return {
        "conversation_key": _safe_str(chosen.get("conversation_key", "")),
        "matched_load_id": matched_load_id,
        "request_type": _safe_str(chosen.get("request_type", "")),
        "conversation_status": _safe_str(chosen.get("conversation_status", "")),
    }


def _sync_conversation_status(conversation_key: str) -> None:
    if not _safe_str(conversation_key):
        return

    timeline = _load_operations_conversation_timeline(conversation_key)
    if timeline.empty:
        return

    latest = timeline.iloc[-1]
    latest_direction = _safe_str(latest.get("email_direction", "inbound")).lower() or "inbound"
    message_count = len(timeline)

    if latest_direction == "outbound":
        latest_mailbox = _safe_str(latest.get("email_mailbox", "")).lower()
        new_status = "Waiting Customer" if latest_mailbox == "tms" else "Answered Outside TMS"
    elif message_count <= 1:
        new_status = "New Conversation"
    else:
        new_status = "Waiting Dispatcher"

    try:
        execute(
            f"""
            update order_intake
            set conversation_status = :conversation_status
            where {_operations_email_source_filter()}
              and {_conversation_join_expr()} = :conversation_key
            """,
            {
                "conversation_status": new_status,
                "conversation_key": conversation_key,
            },
        )
        _load_operations_conversation_summary_df.clear()
        _load_operations_conversation_timeline.clear()
    except Exception:
        pass


def _int_or_none(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    value_text = _safe_str(value)
    if not value_text:
        return None
    try:
        return int(float(value_text))
    except Exception:
        return None


def _case_customer_from_sender(sender: str) -> str:
    name, email = parseaddr(str(sender or ""))
    return _safe_str(name) or _safe_str(email) or _safe_str(sender)


def _default_operations_case_owner(request_type: str) -> str:
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


def _operations_case_priority_from_text(subject: str, body: str, request_type: str) -> str:
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


def _operations_case_status_for_message(direction: str, current_status: str = "", is_new: bool = False) -> str:
    direction = _safe_str(direction).lower() or "inbound"
    current_status = _safe_str(current_status)
    if direction == "outbound":
        return "Waiting Customer"
    if current_status == "Closed":
        return "Reopened"
    if is_new:
        return "New"
    return "Waiting Dispatcher"


def _next_operations_case_number() -> str:
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


def _load_operations_case_by_id(case_id) -> dict:
    case_id = _int_or_none(case_id)
    if case_id is None:
        return {}
    try:
        case_df = read_df(
            """
            select *
            from operations_cases
            where id = :case_id
            limit 1
            """,
            {"case_id": case_id},
        )
    except Exception:
        return {}
    return case_df.iloc[0].to_dict() if not case_df.empty else {}


def _load_operations_case_by_conversation(conversation_key: str) -> dict:
    if not _safe_str(conversation_key):
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


def _normalize_case_subject(subject: str) -> str:
    text = _safe_str(subject).lower()
    text = re.sub(r"^\s*(?:re|fw|fwd)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _case_identity_values(
    *,
    conversation_key: str = "",
    subject: str = "",
    sender: str = "",
    body: str = "",
    parsed: dict | None = None,
    matched_load_id=None,
) -> dict:
    parsed = parsed if isinstance(parsed, dict) else {}
    tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
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


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_case_match_context(limit: int = 1000) -> pd.DataFrame:
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


def _score_operations_case_identity(case_row: dict, identity: dict) -> int:
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


def _find_existing_operations_case_for_message(
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


def _log_operations_case_event(
    case_id,
    event_type: str,
    title: str = "",
    details: str = "",
    actor: str = "system",
    department: str = "",
) -> None:
    case_id = _int_or_none(case_id)
    if case_id is None or not _safe_str(event_type):
        return
    try:
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
    except Exception:
        pass


def _record_operations_case_owner_change(case_id, old_owner: str, new_owner: str, changed_by: str = "dispatcher") -> None:
    case_id = _int_or_none(case_id)
    old_owner = _safe_str(old_owner)
    new_owner = _safe_str(new_owner) or "Unassigned"
    if case_id is None or old_owner == new_owner:
        return
    try:
        execute(
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
            """,
            {
                "case_id": case_id,
                "old_owner": old_owner or None,
                "new_owner": new_owner,
                "changed_by": changed_by,
            },
        )
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


def _update_operations_case_sla(case_id) -> None:
    case_id = _int_or_none(case_id)
    if case_id is None:
        return
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
                end,
                updated_at = now()
            where id = :case_id
            """,
            {"case_id": case_id},
        )
    except Exception:
        pass


def _refresh_operations_case_sla_statuses() -> None:
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


def _get_or_create_operations_case(
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


def _load_operations_case_by_number(case_number: str) -> dict:
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


def _sync_operations_case_summary(case_id) -> None:
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


def _sync_operations_case_for_intake_record(record) -> dict:
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


def _sync_operations_case_for_intake_id(intake_id: int) -> dict:
    try:
        record_df = _load_operations_inbox_record(int(intake_id))
    except Exception:
        record_df = pd.DataFrame()
    if record_df.empty:
        return {}
    return _sync_operations_case_for_intake_record(record_df.iloc[0])


def _set_operations_case_status(case_id, status: str, next_action: str = "") -> None:
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


def _update_operations_case(
    *,
    case_id,
    status: str,
    owner: str,
    priority: str,
    linked_load_id=None,
    next_action: str = "",
) -> None:
    case_id = _int_or_none(case_id)
    if case_id is None:
        return
    old_case = _load_operations_case_by_id(case_id)
    linked_load_id = _int_or_none(linked_load_id)
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
    _record_operations_case_owner_change(
        case_id,
        _safe_str(old_case.get("owner", "")),
        owner,
        changed_by="dispatcher",
    )
    if _safe_str(old_case.get("status", "")) != status:
        _log_operations_case_event(
            case_id,
            "status_change",
            f"Status changed to {status}",
            next_action,
            actor="dispatcher",
            department=owner,
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
            'dispatcher'
        )
        """,
        {
            "case_id": case_id,
            "note_body": (
                f"Case updated to {status}; owner {owner}; priority {priority}; "
                f"linked load {_safe_str(linked_load_id) or '-'}."
            ),
        },
    )
    _update_operations_case_sla(case_id)
    execute(
        """
        update order_intake
        set matched_load_id = coalesce(:linked_load_id, matched_load_id)
        where case_id = :case_id
        """,
        {"case_id": case_id, "linked_load_id": linked_load_id},
    )


def _add_operations_case_note(case_id, note_body: str, note_type: str = "internal", created_by: str = "dispatcher") -> None:
    case_id = _int_or_none(case_id)
    note_body = _safe_str(note_body)
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
    _log_operations_case_event(
        case_id,
        "note",
        "Internal note added" if note_type == "internal" else "Case note added",
        note_body,
        actor=created_by,
    )


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_case_timeline(case_id) -> pd.DataFrame:
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


@st.cache_data(show_spinner=False, ttl=30)
def _load_recent_operations_cases(current_case_id=None) -> pd.DataFrame:
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


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_case_owner_history(case_id) -> pd.DataFrame:
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


@st.cache_data(show_spinner=False, ttl=30)
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


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_case_dashboard_df() -> pd.DataFrame:
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


def _hours_between(start, end) -> float | None:
    start_ts = pd.to_datetime(start, errors="coerce", utc=True)
    end_ts = pd.to_datetime(end, errors="coerce", utc=True)
    if pd.isna(start_ts) or pd.isna(end_ts):
        return None
    return round((end_ts - start_ts).total_seconds() / 3600, 2)


def render_communication_dashboard() -> None:
    st.markdown("### Communication Dashboard")
    st.caption("Operations Case visibility across Dispatch, Management, Billing, and Customer Service.")
    try:
        _ensure_operations_email_sync_schema()
    except Exception as exc:
        st.info(f"Communication dashboard will be available after the Operations Inbox migration is ready: {exc}")
        return

    _refresh_operations_case_sla_statuses()
    _load_operations_case_dashboard_df.clear()
    case_df = _load_operations_case_dashboard_df()
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
        st.dataframe(case_df[display_cols], use_container_width=True, hide_index=True)


def _merge_operations_cases(source_case_id, target_case_id) -> bool:
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


def _find_existing_operations_email_record(
    message_id: str,
    subject: str,
    sender: str,
    received_at: str | None = None,
) -> dict | None:
    if message_id:
        existing = read_df(
            """
            select id, parsed_data, filename, file_path, raw_text, action_required, email_thread_id, email_direction,
                   email_normalized_subject, conversation_key, matched_load_id, case_id, request_type, conversation_status,
                   source_subject, source_sender
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
            f"""
            select id, parsed_data, filename, file_path, raw_text, action_required, email_thread_id, email_direction,
                   email_normalized_subject, conversation_key, matched_load_id, case_id, request_type, conversation_status,
                   source_subject, source_sender
            from order_intake
            where {_operations_email_source_filter()}
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
        f"""
        select id, parsed_data, filename, file_path, raw_text, action_required, email_thread_id, email_direction,
               email_normalized_subject, conversation_key, matched_load_id, case_id, request_type, conversation_status,
               source_subject, source_sender
        from order_intake
        where {_operations_email_source_filter()}
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
        "by_thread_id": {},
        "by_normalized_subject": {},
        "by_received": {},
        "by_subject_sender_no_received": {},
    }

    try:
        existing_df = read_df(
            f"""
            select
                id,
                parsed_data,
                filename,
                file_path,
                raw_text,
                action_required,
                email_direction,
                email_mailbox,
                email_thread_id,
                email_normalized_subject,
                email_in_reply_to,
                email_references,
                conversation_key,
                matched_load_id,
                case_id,
                request_type,
                conversation_status,
                source_message_id,
                coalesce(source_subject, '') as source_subject,
                coalesce(source_sender, '') as source_sender,
                source_received_at
            from order_intake
            where {_operations_email_source_filter()}
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
        thread_id = _safe_str(record.get("email_thread_id", ""))
        normalized_subject = _safe_str(record.get("email_normalized_subject", ""))
        subject = _safe_str(record.get("source_subject", ""))
        sender = _safe_str(record.get("source_sender", ""))
        received_key = _email_received_lookup_key(record.get("source_received_at"))

        if message_id and message_id not in lookup["by_message_id"]:
            lookup["by_message_id"][message_id] = record
        if thread_id:
            lookup["by_thread_id"].setdefault(thread_id, []).append(record)
        if normalized_subject:
            lookup["by_normalized_subject"].setdefault(normalized_subject, []).append(record)
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


def _merge_saved_attachment_fields(parsed: dict, saved_attachments: list[dict]) -> dict:
    updated = dict(parsed)
    for attachment in saved_attachments:
        attachment_parsed = attachment.get("parsed_data") or {}
        for field in OPERATIONS_ORDER_FIELDS:
            attachment_value = _safe_str(attachment_parsed.get(field, ""))
            if not attachment_value:
                continue
            if field == "Dispatcher Notes":
                existing_value = _safe_str(updated.get(field, ""))
                if existing_value and attachment_value not in existing_value:
                    updated[field] = f"{existing_value}\n{attachment_value}"
                elif not existing_value:
                    updated[field] = attachment_value
            elif not _safe_str(updated.get(field, "")):
                updated[field] = attachment_value
    if saved_attachments:
        updated[OPERATIONS_ATTACHMENTS_KEY] = saved_attachments
        pdf_attachments = [
            item
            for item in saved_attachments
            if _is_pdf_filename(item.get("filename", ""), item.get("content_type", "")) or bool(item.get("is_pdf"))
        ]
        if pdf_attachments:
            updated[OPERATIONS_PDF_ATTACHMENTS_KEY] = pdf_attachments
    return updated


def _backfill_operations_email_attachments(
    *,
    existing_record: dict,
    email_item: dict,
    message_id: str,
) -> int:
    parsed = _coerce_json_dict(existing_record.get("parsed_data"))
    existing_attachments = _extract_operations_attachments(parsed, existing_record)
    existing_names = {_safe_str(item.get("filename", "")).lower() for item in existing_attachments}
    existing_paths = {_safe_str(item.get("file_path", "")) for item in existing_attachments}

    new_attachments = []
    for attachment_index, attachment in enumerate(email_item.get("attachments", []) or [], start=1):
        filename = _safe_str(attachment.get("filename", ""))
        content = attachment.get("content") or b""
        content_type = _safe_str(attachment.get("content_type", ""))
        if not filename or not content:
            continue
        if filename.lower() in existing_names:
            continue

        saved = _save_operations_attachment(
            content=content,
            filename=filename,
            message_id=message_id or f"intake-{existing_record.get('id')}",
            attachment_index=len(existing_attachments) + len(new_attachments) + attachment_index,
            content_type=content_type,
        )
        if _safe_str(saved.get("file_path", "")) in existing_paths:
            continue
        new_attachments.append(saved)

    if not new_attachments:
        return 0

    merged_attachments = existing_attachments + new_attachments
    updated_parsed = _merge_saved_attachment_fields(parsed, merged_attachments)

    primary = next(
        (item for item in merged_attachments if _is_pdf_filename(item.get("filename", ""), item.get("content_type", ""))),
        merged_attachments[0],
    )
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


def _backfill_operations_pdf_attachments(*, existing_record: dict, email_item: dict, message_id: str) -> int:
    return _backfill_operations_email_attachments(
        existing_record=existing_record,
        email_item=email_item,
        message_id=message_id,
    )


def _email_subject_match_key(value: str) -> str:
    text = _safe_str(value).lower()
    while True:
        cleaned = re.sub(r"^\s*(?:re|fw|fwd)\s*:\s*", "", text, flags=re.I)
        if cleaned == text:
            break
        text = cleaned
    return re.sub(r"\s+", " ", text).strip()


def _email_body_match_key(value: str) -> str:
    text = re.sub(r"\s+", " ", _safe_str(value).lower()).strip()
    return text[:240]


def _email_item_matches_operations_record(item: dict, record: dict) -> bool:
    item_message_id = _safe_str(item.get("message_id") or item.get("id", ""))
    record_message_id = _safe_str(record.get("source_message_id", ""))
    if item_message_id and record_message_id and item_message_id == record_message_id:
        return True

    item_subject = _email_subject_match_key(item.get("subject", ""))
    record_subject = _email_subject_match_key(record.get("source_subject", ""))
    if not item_subject or item_subject != record_subject:
        return False

    item_sender = _extract_email_address(item.get("from", "")).lower()
    record_sender = _extract_email_address(record.get("source_sender", "")).lower()
    if item_sender and record_sender and item_sender != record_sender:
        return False

    item_received = _email_received_lookup_key(item.get("received_at"))
    record_received = _email_received_lookup_key(record.get("source_received_at"))
    if item_received and record_received and item_received == record_received:
        return True

    item_body = _email_body_match_key(item.get("body", ""))
    record_body = _email_body_match_key(record.get("raw_text", ""))
    return bool(item_body and record_body and (item_body in record_body or record_body in item_body))


def _message_mentions_attachment(subject: str, body: str) -> bool:
    return bool(
        re.search(
            r"\b(attached|attachment|attachments|adjunto|adjuntos|pdf|doc(?:ument)?|delivery order|packing list|imo|bol)\b",
            f"{subject or ''}\n{body or ''}",
            re.I,
        )
    )


def _operations_attachment_status_for_row(row) -> str:
    saved_count = int(float(row.get("attachment_count", 0) or 0)) if hasattr(row, "get") else 0
    source_count = int(float(row.get("source_attachment_count", 0) or 0)) if hasattr(row, "get") else 0
    subject = _safe_str(row.get("source_subject", "") if hasattr(row, "get") else "")
    preview = _safe_str(row.get("raw_text_preview", "") if hasattr(row, "get") else "")
    if saved_count > 0:
        return f"Saved {saved_count}"
    if source_count > 0:
        return f"Mailbox {source_count}"
    if _message_mentions_attachment(subject, preview):
        return "Mentioned"
    return "None"


def _effective_operations_request_type_for_row(row) -> str:
    saved_type = _safe_str(row.get("request_type", "") if hasattr(row, "get") else "") or "Needs Classification"
    if saved_type != "New Booking":
        return saved_type

    subject = _safe_str(row.get("source_subject", "") if hasattr(row, "get") else "")
    body = _safe_str(row.get("raw_text_preview", "") if hasattr(row, "get") else "") or _safe_str(
        row.get("raw_text", "") if hasattr(row, "get") else ""
    )
    text = f"{subject}\n{body}"
    parsed = _coerce_json_dict(row.get("parsed_data") if hasattr(row, "get") else {})
    tokens = _extract_reference_tokens(f"{text}\n{parsed}")
    has_reference = _has_reference_details(tokens, parsed)

    if _is_information_update(text) and not _has_order_placement_signal(text):
        return "Booking Update" if has_reference else "Customer Request"

    if _subject_is_reply(subject) and not _has_new_order_details(text, parsed, tokens):
        return "Booking Update" if has_reference else "Customer Request"

    return saved_type


def _operations_has_matched_load(row) -> bool:
    value = row.get("matched_load_id", "") if hasattr(row, "get") else ""
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    return _safe_str(value).lower() not in {"", "nan", "none", "null"}


def _operations_row_text(row) -> str:
    if not hasattr(row, "get"):
        return ""
    return "\n".join(
        [
            _safe_str(row.get("source_subject", "")),
            _safe_str(row.get("raw_text_preview", "")),
            _safe_str(row.get("raw_text", "")),
            _safe_str(row.get("action_required", "")),
        ]
    )


def _normalize_operations_owner(value: str) -> str:
    owner = _safe_str(value)
    if owner == "Customer Service":
        return "Customer"
    if owner == "Manager":
        return "Operations"
    return owner


def _operations_owner_label_for_row(row) -> str:
    saved_owner = _normalize_operations_owner(row.get("case_owner", "") if hasattr(row, "get") else "")
    if saved_owner and saved_owner != "Unassigned":
        return saved_owner

    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    text = _operations_row_text(row).lower()
    if request_type == "Billing" or any(term in text for term in ["invoice", "billing", "detention", "demurrage", "rate"]):
        return "Billing"
    if request_type == "Driver Issue" or any(term in text for term in ["driver", "truck", "chassis"]):
        return "Driver"
    if request_type == "Port Issue" or any(term in text for term in ["port", "terminal", "gate", "steamship"]):
        return "Port"
    if request_type in {"Customer Request", "Missing Information"}:
        return "Customer"
    if request_type == "Spam/Marketing":
        return "Operations"
    if request_type in {"Cancellation", "POD Request"}:
        return "Operations"
    return "Dispatch"


def _operations_priority_label_for_row(row) -> str:
    saved_priority = _safe_str(row.get("case_priority", "") if hasattr(row, "get") else "")
    priority_map = {
        "Urgent": "Critical",
        "Normal": "Medium",
        "Critical": "Critical",
        "High": "High",
        "Medium": "Medium",
        "Low": "Low",
    }
    if saved_priority in priority_map:
        return priority_map[saved_priority]

    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    text = _operations_row_text(row).lower()
    critical_terms = [
        "urgent",
        "asap",
        "critical",
        "lfd today",
        "last free day today",
        "driver waiting",
        "driver stuck",
        "no show",
        "gate closed",
        "truck down",
    ]
    high_terms = [
        "lfd",
        "last free day",
        "cutoff",
        "appointment today",
        "same day",
        "hold",
        "released",
        "available now",
        "cancel",
    ]
    if any(term in text for term in critical_terms):
        return "Critical"
    if any(term in text for term in high_terms) or request_type in {"Cancellation", "Driver Issue", "Port Issue"}:
        return "High"
    if request_type in {"Billing", "Spam/Marketing"}:
        return "Low"
    return "Medium"


def _operations_status_label_for_row(row) -> str:
    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    action_required = _safe_str(row.get("action_required", "") if hasattr(row, "get") else "")
    confidence = int(float(row.get("confidence_score", 0) or 0)) if hasattr(row, "get") else 0
    has_match = _operations_has_matched_load(row)
    review_status = _safe_str(row.get("review_status_clean", "") if hasattr(row, "get") else "")
    case_status = _safe_str(row.get("case_status", "") if hasattr(row, "get") else "")

    if request_type == "Missing Information" or "missing" in action_required.lower():
        return "Needs Details"
    if (
        not has_match
        and confidence < 70
        and request_type in {
            "New Booking",
            "Booking Update",
            "Appointment Update",
            "Quote Request",
            "Cancellation",
            "POD Request",
            "Billing",
            "Driver Issue",
            "Port Issue",
        }
    ):
        return "Needs Details"
    if case_status:
        return case_status
    if review_status in {"Waiting on Customer", "Waiting Customer"}:
        return "Waiting Customer"
    if review_status:
        return review_status
    return "Open"


def _operations_document_signal_for_row(row) -> bool:
    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    if request_type == "POD Request":
        return True
    text = _operations_row_text(row).lower()
    document_terms = [
        "pod",
        "proof of delivery",
        "delivery order",
        "bill of lading",
        "bol",
        "packing list",
        "hazmat",
        "imo",
        "document",
        "documents attached",
        "attachment contains",
    ]
    if any(term in text for term in document_terms):
        return True
    attachment_status = _safe_str(row.get("attachment_status", "") if hasattr(row, "get") else "")
    return attachment_status.startswith(("Saved", "Mailbox")) and request_type not in {"New Booking", "Quote Request"}


def _operations_work_queue_for_row(row) -> str:
    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    status_label = _safe_str(row.get("status_label", "") if hasattr(row, "get") else "")
    review_status = _safe_str(row.get("review_status_clean", "") if hasattr(row, "get") else "")
    confidence = int(float(row.get("confidence_score", 0) or 0)) if hasattr(row, "get") else 0
    has_match = _operations_has_matched_load(row)

    if request_type in {"No Action / FYI", "Spam/Marketing"}:
        return "Archive"
    if request_type == "Business Communication":
        return "Business"
    if status_label.startswith("Waiting") or review_status in {"Waiting on Customer", "Waiting Customer"}:
        return "Waiting"
    if request_type == "Billing" or _operations_owner_label_for_row(row) == "Billing":
        return "Billing"
    if request_type in {"Needs Classification", "Other", ""} or confidence < 50:
        return "Review"
    if _operations_document_signal_for_row(row):
        return "Documents"
    if request_type in {"New Booking", "Quote Request"}:
        return "New Orders"
    if has_match or request_type in {
        "Booking Update",
        "Appointment Update",
        "Cancellation",
        "Driver Issue",
        "Port Issue",
    }:
        return "Existing Loads"
    if request_type in {"Customer Request", "Missing Information"}:
        return "Action Required"
    return "Action Required"


def _operations_sender_is_vip(row) -> bool:
    sender = _extract_email_address(_safe_str(row.get("source_sender", "") if hasattr(row, "get") else "")).lower()
    if not sender or "@" not in sender:
        return False
    domain = sender.rsplit("@", 1)[-1]
    return any(domain == vip_domain or domain.endswith(f".{vip_domain}") for vip_domain in VIP_OPERATIONS_DOMAINS)


def _operations_parsed_for_row(row) -> dict:
    return _coerce_json_dict(row.get("parsed_data") if hasattr(row, "get") else {})


def _operations_reference_tokens_for_row(row) -> dict:
    parsed = _operations_parsed_for_row(row)
    return _extract_reference_tokens(
        "\n".join(
            [
                _safe_str(row.get("source_subject", "") if hasattr(row, "get") else ""),
                _safe_str(row.get("raw_text_preview", "") if hasattr(row, "get") else ""),
                _safe_str(parsed),
            ]
        )
    )


def _operations_parsed_hint(row, field: str, fallback: str = "") -> str:
    parsed = _operations_parsed_for_row(row)
    value = _safe_str(parsed.get(field, ""))
    return value or fallback


def _operations_identifier_hints_for_row(row) -> dict:
    parsed = _operations_parsed_for_row(row)
    tokens = _operations_reference_tokens_for_row(row)
    return {
        "booking": _safe_str(parsed.get("Booking Number", "")) or tokens.get("booking_number", ""),
        "container": _safe_str(parsed.get("Container Number", "")) or tokens.get("container_number", ""),
        "reference": _safe_str(parsed.get("Reference Number", "")) or tokens.get("reference_number", ""),
        "customer": (
            _safe_str(parsed.get("Customer", ""))
            or _safe_str(row.get("case_customer", "") if hasattr(row, "get") else "")
            or _safe_str(row.get("client_name", "") if hasattr(row, "get") else "")
        ),
        "warehouse": _safe_str(parsed.get("Warehouse", "")) or _safe_str(parsed.get("Address", "")),
        "port": _safe_str(parsed.get("Port", "")),
        "delivery": _safe_str(parsed.get("Delivery Need Date", "")) or _safe_str(parsed.get("LFD", "")),
    }


def _operations_has_shipment_signal(row) -> bool:
    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    if _operations_has_matched_load(row) or request_type in OPERATIONAL_REQUEST_TYPES:
        return True
    parsed = _operations_parsed_for_row(row)
    tokens = _operations_reference_tokens_for_row(row)
    return _has_reference_details(tokens, parsed) or _operations_document_signal_for_row(row)


def _operations_can_open_case(row, request_type: str = "") -> bool:
    level = _safe_str(row.get("control_level", "") if hasattr(row, "get") else "")
    request_type = _safe_str(request_type) or _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    if level != "Level 1 - Operational Cases":
        return False
    if request_type in NO_ACTION_REQUEST_TYPES or request_type in BUSINESS_REQUEST_TYPES:
        return False
    return request_type in OPERATIONAL_REQUEST_TYPES or _operations_has_shipment_signal(row)


def _operations_control_level_for_row(row) -> str:
    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    confidence = int(float(row.get("confidence_score", 0) or 0)) if hasattr(row, "get") else 0
    text = _operations_row_text(row).lower()
    is_vip = _operations_sender_is_vip(row)
    has_shipment_signal = _operations_has_shipment_signal(row)
    owner = _operations_owner_label_for_row(row)

    if request_type in NO_ACTION_REQUEST_TYPES:
        return "Level 3 - No Action / Archive"
    if _contains_any(text, NO_ACTION_COMMUNICATION_TERMS + SPAM_MARKETING_TERMS) and not is_vip and not has_shipment_signal:
        return "Level 3 - No Action / Archive"

    if request_type in {"", "Other", "Needs Classification"} or confidence < 50:
        return "Needs Review"

    if request_type in BUSINESS_REQUEST_TYPES:
        return "Level 2 - Business Communications"
    if owner in {"Billing", "Manager", "Safety"} and not has_shipment_signal:
        return "Level 2 - Business Communications"
    if _contains_any(text, BUSINESS_COMMUNICATION_TERMS) and not has_shipment_signal:
        return "Level 2 - Business Communications"

    return "Level 1 - Operational Cases"


def _operations_department_lane_for_row(row) -> str:
    level = _safe_str(row.get("control_level", "") if hasattr(row, "get") else "") or _operations_control_level_for_row(row)
    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    text = _operations_row_text(row).lower()
    owner = _operations_owner_label_for_row(row)

    if level == "Needs Review":
        return "Human Review"
    if level == "Level 3 - No Action / Archive":
        if request_type == "Spam/Marketing" or _contains_any(text, SPAM_MARKETING_TERMS):
            return "Spam"
        if "duplicate" in text:
            return "Duplicates"
        return "Archive / FYI"
    if level == "Level 2 - Business Communications":
        if request_type == "Billing" or _contains_any(text, BILLING_TERMS):
            return "Accounting"
        if any(term in text for term in ["insurance", "legal", "contract", "agreement", "claim", "bank", "utility"]):
            return "Management"
        if any(term in text for term in ["recruiting", "resume", "candidate", "employment", "hr", "human resources"]):
            return "Management"
        if any(term in text for term in ["new customer inquiry", "sales lead", "credit application"]):
            return "Sales"
        if any(term in text for term in ["vendor", "supplier", "software", "it support", "password"]):
            return "Management"
        return owner if owner not in {"Dispatch", "Customer", "Driver", "Port", "Warehouse"} else "Management"

    if request_type == "Billing" or owner == "Billing":
        return "Accounting"
    if owner in {"Driver", "Port", "Warehouse"}:
        return "Dispatch"
    if owner == "Customer":
        return "Customer Service"
    if owner in {"Operations", "Manager"}:
        return "Operations"
    return "Dispatch"


def _operations_confidence_label(score) -> str:
    try:
        score_int = int(float(score or 0))
    except Exception:
        score_int = 0
    if score_int >= 80:
        return "High Match"
    if score_int >= 60:
        return "Medium Match"
    return "Unknown"


def _operations_work_item_for_row(row) -> str:
    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    work_queue = _safe_str(row.get("work_queue", "") if hasattr(row, "get") else "")
    if request_type == "New Booking":
        return "Create Order"
    if request_type == "Quote Request":
        return "Create Quote"
    if request_type == "Appointment Update":
        return "Appointment"
    if request_type == "Booking Update":
        return "Booking Revision"
    if request_type == "Cancellation":
        return "Cancel Booking"
    if request_type == "POD Request":
        return "POD / Documents"
    if request_type == "Billing":
        return "Billing Case"
    if request_type == "Business Communication":
        return "Business Case"
    if request_type in NO_ACTION_REQUEST_TYPES:
        return "Archive"
    if work_queue:
        return work_queue
    return request_type or "Review"


def _operations_recommended_action_for_row(row) -> str:
    level = _safe_str(row.get("control_level", "") if hasattr(row, "get") else "") or _operations_control_level_for_row(row)
    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    work_queue = _safe_str(row.get("work_queue", "") if hasattr(row, "get") else "")
    has_match = _operations_has_matched_load(row)

    if level == "Needs Review":
        return "Classify"
    if level == "Level 3 - No Action / Archive":
        return "Archive / Close"
    if level == "Level 2 - Business Communications":
        lane = _operations_department_lane_for_row(row)
        return f"Route to {lane}"
    if request_type == "New Booking":
        return "Create Order"
    if request_type == "Quote Request":
        return "Create Quote"
    if has_match:
        return "Attach / Update Load"
    if work_queue == "Documents":
        return "Review Documents"
    if request_type == "Missing Information":
        return "Reply for Details"
    return "Review Case"


def _operations_control_reason_for_row(row) -> str:
    level = _safe_str(row.get("control_level", "") if hasattr(row, "get") else "") or _operations_control_level_for_row(row)
    request_type = _safe_str(row.get("request_type_clean", "") if hasattr(row, "get") else "")
    confidence_label = _operations_confidence_label(row.get("confidence_score", 0) if hasattr(row, "get") else 0)

    if level == "Needs Review":
        return f"{confidence_label}; needs human routing"
    if level == "Level 2 - Business Communications":
        return f"{request_type or 'Business'} routed by department"
    if level == "Level 3 - No Action / Archive":
        return f"{request_type or 'FYI'} should not become dispatcher work"
    return f"{request_type or 'Operational'} work item"


def _operations_queue_labels_for_level(level: str, level_df: pd.DataFrame) -> list[str]:
    if level == "Level 1 - Operational Cases":
        return ["Action Required", "New Orders", "Existing Loads", "Waiting", "Documents"]
    if level == "Level 2 - Business Communications":
        preferred = ["Accounting", "Management", "Sales", "Safety", "Customer Service"]
        dynamic = sorted([value for value in level_df.get("department_lane", pd.Series(dtype=str)).dropna().astype(str).unique() if value])
        return [label for label in preferred if label in dynamic] + [label for label in dynamic if label not in preferred] or ["Business"]
    if level == "Level 3 - No Action / Archive":
        preferred = ["Archive / FYI", "Spam", "Duplicates"]
        dynamic = sorted([value for value in level_df.get("department_lane", pd.Series(dtype=str)).dropna().astype(str).unique() if value])
        return [label for label in preferred if label in dynamic] + [label for label in dynamic if label not in preferred] or ["Archive / FYI"]
    return ["Needs Review"]


def _operations_queue_mask_for_level(level_df: pd.DataFrame, level: str, queue_label: str) -> pd.Series:
    if level == "Level 1 - Operational Cases":
        return level_df["work_queue"].eq(queue_label)
    if level in {"Level 2 - Business Communications", "Level 3 - No Action / Archive"}:
        return level_df["department_lane"].eq(queue_label)
    return pd.Series(True, index=level_df.index)


def _collapse_operations_inbox_to_cases(inbox_df: pd.DataFrame) -> pd.DataFrame:
    if inbox_df.empty:
        return inbox_df

    collapsed = inbox_df.copy()
    collapsed["case_row_key"] = collapsed.apply(
        lambda row: f"case-{int(float(row.get('case_id')))}"
        if _int_or_none(row.get("case_id")) is not None
        else f"intake-{int(row.get('id'))}",
        axis=1,
    )
    collapsed["_case_sort_time"] = pd.to_datetime(
        collapsed.get("case_last_message_at", collapsed.get("source_received_at")),
        errors="coerce",
    )
    fallback_sort_time = pd.to_datetime(collapsed.get("source_received_at"), errors="coerce").fillna(
        pd.to_datetime(collapsed.get("created_at"), errors="coerce")
    )
    collapsed["_case_sort_time"] = collapsed["_case_sort_time"].fillna(fallback_sort_time)

    collapsed = (
        collapsed.sort_values(["case_row_key", "_case_sort_time", "id"])
        .drop_duplicates("case_row_key", keep="last")
        .sort_values("_case_sort_time", ascending=False)
        .drop(columns=["_case_sort_time"], errors="ignore")
    )
    return collapsed


def _rescan_operations_request_attachments(record, limit: int = 250) -> dict:
    record_dict = record.to_dict() if hasattr(record, "to_dict") else dict(record or {})
    result = {
        "checked": 0,
        "matched": 0,
        "source_attachment_count": 0,
        "saved": 0,
        "attachment_names": [],
    }

    message_id = _safe_str(record_dict.get("source_message_id", ""))
    emails = []
    if message_id and callable(fetch_operations_email_by_message_id):
        try:
            emails.extend(fetch_operations_email_by_message_id(message_id, limit=5))
        except Exception:
            pass
    exact_matches = [item for item in emails if _email_item_matches_operations_record(item, record_dict)]
    if exact_matches:
        emails = exact_matches
    else:
        emails = fetch_operations_email_sync(limit=limit)
    result["checked"] = len(emails)

    for item in emails:
        if not _email_item_matches_operations_record(item, record_dict):
            continue
        result["matched"] += 1
        attachments = item.get("attachments", []) or []
        result["source_attachment_count"] += len(attachments)
        result["attachment_names"].extend(
            [_safe_str(attachment.get("filename", "")) for attachment in attachments if _safe_str(attachment.get("filename", ""))]
        )
        message_id = _safe_str(item.get("message_id") or item.get("id") or record_dict.get("source_message_id", ""))
        result["saved"] += _backfill_operations_email_attachments(
            existing_record=record_dict,
            email_item=item,
            message_id=message_id or f"intake-{record_dict.get('id')}",
        )

    result["attachment_names"] = sorted(set(result["attachment_names"]))
    return result


def _operations_inbox_status_counts() -> pd.DataFrame:
    try:
        return read_df(
            f"""
            select
                coalesce(review_status, 'Open') as review_status,
                count(*) as email_count
            from order_intake
            where {_operations_email_source_filter()}
            group by coalesce(review_status, 'Open')
            order by email_count desc, review_status
            """
        )
    except Exception:
        return pd.DataFrame()


def _operations_email_sync_metrics() -> dict:
    metrics = {
        "inbound": 0,
        "outbound": 0,
        "threads": 0,
        "last_sync": "",
    }

    try:
        sync_df = read_df(
            f"""
            select
                coalesce(email_direction, 'inbound') as email_direction,
                count(*) as email_count,
                count(distinct nullif(email_thread_id, '')) as thread_count,
                max(email_synced_at) as last_sync
            from order_intake
            where {_operations_email_source_filter()}
            group by coalesce(email_direction, 'inbound')
            """
        )
    except Exception:
        return metrics

    if sync_df.empty:
        return metrics

    metrics["threads"] = int(sync_df["thread_count"].fillna(0).sum())
    last_sync = pd.to_datetime(sync_df["last_sync"], errors="coerce").max()
    if pd.notna(last_sync):
        metrics["last_sync"] = last_sync.strftime("%Y-%m-%d %I:%M %p")

    for _, row in sync_df.iterrows():
        direction = _safe_str(row.get("email_direction", "")).lower() or "inbound"
        if direction in ["inbound", "outbound"]:
            metrics[direction] = int(row.get("email_count", 0) or 0)

    return metrics


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

    if request_type == "Billing":
        if matched_load_id is None:
            return "Billing question needs booking, invoice, container, or reference before billing can respond."
        return "Billing request matched to a load; review invoice/POD status and route to Billing."

    if request_type == "Business Communication":
        return "Route to the responsible business department and keep it out of dispatcher load queues."

    if request_type == "No Action / FYI":
        return "No shipment action needed. Archive after review so the message remains searchable."

    if request_type == "Driver Issue":
        if matched_load_id is None:
            return "Driver issue needs booking, container, truck, or reference before dispatch can resolve it."
        return "Driver issue matched to a load; dispatch should review driver, truck, appointment, and status."

    if request_type == "Port Issue":
        if matched_load_id is None:
            return "Port or terminal issue needs booking, container, or reference before dispatch can resolve it."
        return "Port issue matched to a load; review terminal/hold/gate details and update the customer."

    if request_type == "Spam/Marketing":
        return "Marketing or non-operational email; close unless it needs management review."

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

    if request_type in ["Appointment Update", "Booking Update", "Cancellation", "POD Request", "Billing", "Driver Issue", "Port Issue"]:
        return 75 if _has_reference_details(tokens, parsed) else 55

    if request_type == "Missing Information":
        return 75

    if request_type == "Spam/Marketing":
        return 90

    if request_type in {"Business Communication", "No Action / FYI"}:
        return 80

    return max(match_confidence, 50)


def _build_operations_email_classification(
    subject: str,
    body: str,
    parsed: dict | None = None,
    fallback_key: str = "",
    sender: str = "",
) -> dict:
    parsed = _coerce_parsed_for_classification(subject, body, parsed)
    intent_scores = _operations_intent_scores(subject, body, parsed)
    detected_type = classify_customer_request(subject, body, parsed)
    tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
    load_match_candidates = find_load_match_candidates(tokens, parsed=parsed, subject=subject, body=body, limit=5)
    matched_load_id, match_confidence = find_matching_load(tokens, parsed=parsed, subject=subject, body=body)
    confidence = _classification_confidence(
        detected_type,
        subject,
        body,
        parsed,
        tokens,
        matched_load_id,
        match_confidence,
    )
    if intent_scores:
        top_score = int(max(intent_scores.values()))
        confidence = max(confidence, min(95, top_score))
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

    classification = {
        "request_type": detected_type,
        "tokens": tokens,
        "matched_load_id": matched_load_id,
        "confidence_score": confidence,
        "conversation_key": conversation_key,
        "action_required": action_required,
        "intent_scores": intent_scores,
        "load_match_candidates": load_match_candidates,
    }
    return _apply_learned_classification_adjustment(classification, subject, body, parsed, sender=sender)


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
            sender=_safe_str(record.get("source_sender", "")),
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


OPERATIONS_LEARNING_TOKEN_STOPWORDS = {
    "about",
    "after",
    "before",
    "booking",
    "confirmation",
    "customer",
    "delivery",
    "dispatch",
    "email",
    "from",
    "hello",
    "load",
    "need",
    "order",
    "please",
    "request",
    "status",
    "thanks",
    "trucking",
    "update",
}


def _feedback_sender_domain(sender: str) -> str:
    email = _extract_email_address(sender).lower()
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1]


def _subject_learning_tokens(*parts: str) -> set[str]:
    text = " ".join([str(part or "") for part in parts]).lower()
    tokens = set()
    for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text):
        clean_token = token.strip("_-")
        if len(clean_token) < 4:
            continue
        if clean_token in OPERATIONS_LEARNING_TOKEN_STOPWORDS:
            continue
        tokens.add(clean_token)
    return tokens


@st.cache_data(show_spinner=False, ttl=300)
def _recent_operations_learning_rules(limit: int = 250) -> list[dict]:
    try:
        _ensure_operations_ai_feedback_table()
        feedback_df = read_df(
            """
            select
                source_subject,
                source_sender,
                ai_request_type,
                final_request_type,
                correction_type,
                feedback_notes,
                created_at
            from operations_ai_feedback
            where coalesce(final_request_type, '') <> ''
            order by created_at desc
            limit :limit
            """,
            {"limit": int(limit)},
        )
    except Exception:
        return []

    rules = []
    for _, row in feedback_df.iterrows():
        final_type = _safe_str(row.get("final_request_type", ""))
        if final_type not in REQUEST_TYPES:
            continue
        rules.append(
            {
                "source_subject": _safe_str(row.get("source_subject", "")),
                "source_sender": _safe_str(row.get("source_sender", "")),
                "sender_domain": _feedback_sender_domain(row.get("source_sender", "")),
                "subject_tokens": _subject_learning_tokens(row.get("source_subject", "")),
                "ai_request_type": _safe_str(row.get("ai_request_type", "")),
                "final_request_type": final_type,
                "correction_type": _safe_str(row.get("correction_type", "")),
                "feedback_notes": _safe_str(row.get("feedback_notes", "")),
            }
        )
    return rules


def _apply_learned_classification_adjustment(
    classification: dict,
    subject: str,
    body: str,
    parsed: dict,
    sender: str = "",
) -> dict:
    learning_rules = _recent_operations_learning_rules()
    if not learning_rules:
        return classification

    sender_domain = _feedback_sender_domain(sender)
    current_tokens = _subject_learning_tokens(subject, body, json.dumps(parsed, default=str))
    votes: dict[str, dict[str, int]] = {}

    for rule in learning_rules:
        final_type = rule.get("final_request_type", "")
        if final_type not in REQUEST_TYPES:
            continue

        score = 0
        if sender_domain and sender_domain == rule.get("sender_domain", ""):
            score += 3

        overlap = current_tokens.intersection(rule.get("subject_tokens", set()))
        if overlap:
            score += min(5, len(overlap) * 2)

        correction_type = _safe_str(rule.get("correction_type", ""))
        if correction_type and correction_type not in {"classification_confirmed", "classification_accepted"}:
            score += 1

        if score < 4:
            continue

        vote = votes.setdefault(final_type, {"score": 0, "count": 0})
        vote["score"] += score
        vote["count"] += 1

    if not votes:
        return classification

    learned_type, learned_vote = max(
        votes.items(),
        key=lambda item: (item[1]["score"], item[1]["count"]),
    )
    current_type = _safe_str(classification.get("request_type", ""))
    if learned_type == current_type:
        updated = dict(classification)
        updated["learning_applied"] = True
        updated["learning_reason"] = f"Confirmed by {learned_vote['count']} recent dispatcher correction(s)."
        return updated

    updated = dict(classification)
    updated["request_type"] = learned_type
    updated["confidence_score"] = max(
        int(updated.get("confidence_score", 0) or 0),
        min(88, 72 + int(learned_vote["score"])),
    )
    updated["action_required"] = _action_required_for_request(
        learned_type,
        parsed,
        body,
        subject=subject,
        tokens=updated.get("tokens"),
        matched_load_id=updated.get("matched_load_id"),
    )
    updated["learning_applied"] = True
    updated["learning_reason"] = (
        f"Routed as {learned_type} from {learned_vote['count']} recent dispatcher correction(s) "
        "for this sender/topic pattern."
    )
    return updated


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
    suggestion = ai_suggestion if ai_suggestion and ai_suggestion.get("success") else {}

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
                "ai_request_type": suggestion.get("request_type", ""),
                "final_request_type": final_request_type,
                "ai_confidence_score": int(suggestion.get("confidence_score", 0) or 0),
                "ai_priority": suggestion.get("priority", ""),
                "ai_action_required": suggestion.get("action_required", ""),
                "final_action_required": final_action_required,
                "ai_reply_body": suggestion.get("reply_body", ""),
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
        "intent_scores": classification.get("intent_scores", {}),
        "load_match_candidates": classification.get("load_match_candidates", []),
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


def _email_sync_metadata(item: dict) -> dict:
    direction = _safe_str(item.get("direction", "inbound")).lower() or "inbound"
    if direction not in {"inbound", "outbound"}:
        direction = "inbound"

    references = item.get("references") or []
    if not isinstance(references, list):
        references = [_safe_str(references)] if _safe_str(references) else []

    return {
        "direction": direction,
        "mailbox": _safe_str(item.get("mailbox", "")),
        "mailbox_account": _safe_str(item.get("mailbox_account", "")),
        "mailbox_folder": _safe_str(item.get("mailbox_folder", "")),
        "thread_id": _safe_str(item.get("thread_id", "")),
        "conversation_key": _safe_str(item.get("conversation_key", "")) or _safe_str(item.get("thread_id", "")),
        "normalized_subject": _safe_str(item.get("normalized_subject", "")),
        "in_reply_to": _safe_str(item.get("in_reply_to", "")),
        "references": [str(value) for value in references if _safe_str(value)],
    }


def _update_existing_operations_email_sync_metadata(existing_record: dict, item: dict, message_id: str) -> None:
    metadata = _email_sync_metadata(item)
    email_sync_metadata = {
        "direction": metadata["direction"],
        "mailbox": metadata["mailbox"],
        "mailbox_account": metadata["mailbox_account"],
        "mailbox_folder": metadata["mailbox_folder"],
        "thread_id": metadata["thread_id"],
        "conversation_key": metadata["conversation_key"],
        "normalized_subject": metadata["normalized_subject"],
        "in_reply_to": metadata["in_reply_to"],
        "references": metadata["references"],
        "to": _safe_str(item.get("to", "")),
        "cc": _safe_str(item.get("cc", "")),
    }
    try:
        execute(
            """
            update order_intake
            set email_direction = :email_direction,
                email_mailbox = coalesce(nullif(email_mailbox, ''), :email_mailbox),
                email_thread_id = coalesce(nullif(email_thread_id, ''), :email_thread_id),
                email_normalized_subject = coalesce(nullif(email_normalized_subject, ''), :email_normalized_subject),
                conversation_key = coalesce(nullif(conversation_key, ''), :conversation_key),
                email_in_reply_to = coalesce(nullif(email_in_reply_to, ''), :email_in_reply_to),
                email_references = case
                    when email_references is null or email_references = '[]'::jsonb
                        then cast(:email_references as jsonb)
                    else email_references
                end,
                parsed_data = jsonb_set(
                    case
                        when jsonb_typeof(coalesce(parsed_data, '{}'::jsonb)) = 'object'
                            then coalesce(parsed_data, '{}'::jsonb)
                        else '{}'::jsonb
                    end,
                    '{_email_sync}',
                    case
                        when jsonb_typeof(parsed_data -> '_email_sync') = 'object'
                            then parsed_data -> '_email_sync'
                        else '{}'::jsonb
                    end || cast(:email_sync_metadata as jsonb),
                    true
                ),
                email_synced_at = now(),
                source_message_id = coalesce(nullif(source_message_id, ''), :source_message_id)
            where id = :intake_id
            """,
            {
                "intake_id": int(existing_record["id"]),
                "email_direction": metadata["direction"],
                "email_mailbox": metadata["mailbox"] or None,
                "email_thread_id": metadata["thread_id"] or None,
                "email_normalized_subject": metadata["normalized_subject"] or None,
                "conversation_key": metadata["conversation_key"] or None,
                "email_in_reply_to": metadata["in_reply_to"] or None,
                "email_references": json.dumps(metadata["references"]),
                "email_sync_metadata": _json_dump(email_sync_metadata),
                "source_message_id": message_id or None,
            },
        )
    except Exception:
        pass


def sync_operations_email_engine(limit: int = 50) -> dict:
    sync_started_at = datetime.now()
    _ensure_operations_email_sync_schema()
    emails = fetch_operations_email_sync(limit=limit)
    existing_lookup = _load_existing_operations_email_lookup()
    imported = 0
    skipped = 0
    pdf_updated = 0
    fetched = len(emails)
    inbound_fetched = 0
    outbound_fetched = 0
    inbound_imported = 0
    outbound_imported = 0
    cases_touched = set()
    synced_threads = set()
    synced_accounts = set()
    seen_message_ids = set()

    for item in emails:
        subject = str(item.get("subject", "") or "")
        sender = str(item.get("from", "") or "")
        body = str(item.get("body", "") or "")
        latest_body = extract_latest_email_body(body)
        message_id = str(item.get("message_id", "") or item.get("id", "") or "")
        received_at = item.get("received_at")
        metadata = _email_sync_metadata(item)
        direction = metadata["direction"]
        if metadata.get("mailbox_account"):
            synced_accounts.add(metadata["mailbox_account"])
        if direction == "outbound":
            outbound_fetched += 1
        else:
            inbound_fetched += 1
        if metadata["thread_id"]:
            synced_threads.add(metadata["thread_id"])

        if message_id and message_id in seen_message_ids:
            skipped += 1
            continue
        if message_id:
            seen_message_ids.add(message_id)

        thread_context = _conversation_context_from_lookup(existing_lookup, metadata)
        thread_conversation_key = (
            _safe_str(thread_context.get("conversation_key", ""))
            or metadata["conversation_key"]
            or metadata["thread_id"]
            or message_id
        )

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
            _update_existing_operations_email_sync_metadata(existing_record, item, message_id)
            existing_case_id = _int_or_none(existing_record.get("case_id"))
            if existing_case_id is not None:
                cases_touched.add(existing_case_id)
                _sync_operations_case_summary(existing_case_id)
            _sync_conversation_status(thread_conversation_key or _safe_str(existing_record.get("conversation_key", "")))
            pdf_updated += _backfill_operations_pdf_attachments(
                existing_record=existing_record,
                email_item=item,
                message_id=message_id or f"email-{skipped + 1}",
            )
            skipped += 1
            continue

        try:
            body_parsed = parse_email_text(subject, latest_body or body, _safe_str(item.get("from", "")))
        except Exception:
            body_parsed = {}
            from ai_agents.operations_email_intake_agent import ai_intake_agent

            ai_result = ai_intake_agent(
                subject=subject,
                body=latest_body or body,
                sender=_safe_str(item.get("from", "")),
            )

            body_parsed["_ai_intake"] = ai_result

        saved_attachments = []
        for attachment_index, attachment in enumerate(item.get("attachments", []) or [], start=1):
            filename = _safe_str(attachment.get("filename", ""))
            content = attachment.get("content") or b""
            content_type = _safe_str(attachment.get("content_type", ""))
            if not filename or not content:
                continue
            saved_attachments.append(
                _save_operations_attachment(
                    content=content,
                    filename=filename,
                    message_id=message_id or f"operations-{imported + 1}",
                    attachment_index=attachment_index,
                    content_type=content_type,
                )
            )

        parsed = dict(body_parsed)
        if saved_attachments:
            parsed = _merge_saved_attachment_fields(parsed, saved_attachments)

        parsed["_email_sync"] = {
            "direction": direction,
            "mailbox": metadata["mailbox"],
            "mailbox_account": metadata["mailbox_account"],
            "mailbox_folder": metadata["mailbox_folder"],
            "thread_id": metadata["thread_id"],
            "conversation_key": thread_conversation_key,
            "normalized_subject": metadata["normalized_subject"],
            "in_reply_to": metadata["in_reply_to"],
            "references": metadata["references"],
            "to": _safe_str(item.get("to", "")),
            "cc": _safe_str(item.get("cc", "")),
            "source_attachment_count": len(item.get("attachments", []) or []),
            "source_attachment_names": [
                _safe_str(attachment.get("filename", ""))
                for attachment in (item.get("attachments", []) or [])
                if _safe_str(attachment.get("filename", ""))
            ],
        }

        if direction == "outbound":
            classification = {
                "request_type": "Customer Request",
                "conversation_key": thread_conversation_key,
                "matched_load_id": thread_context.get("matched_load_id"),
                "confidence_score": 100,
                "action_required": "Synced outbound email; no dispatcher action required.",
            }
            review_status = "Closed"
            intake_status = "Synced"
            source = "operations_email_sent"
            conversation_status = "Answered Outside TMS"
        else:
            classification = _build_operations_email_classification(
                subject,
                latest_body or body,
                parsed,
                fallback_key=thread_conversation_key or f"email-{imported + 1}",
                sender=sender,
            )
            classification["conversation_key"] = thread_conversation_key
            if thread_context.get("matched_load_id") and classification.get("matched_load_id") is None:
                classification["matched_load_id"] = thread_context.get("matched_load_id")
                classification["confidence_score"] = max(int(classification.get("confidence_score", 0) or 0), 90)
            if thread_context.get("request_type") and classification.get("request_type") == "Customer Request":
                classification["request_type"] = thread_context["request_type"]
            review_status = "Open"
            intake_status = "Needs Review"
            source = "operations_email"
            conversation_status = "New Conversation" if not thread_context else "Waiting Dispatcher"
            if classification.get("request_type") == "Spam/Marketing":
                review_status = "Closed"
                intake_status = "Closed"
                conversation_status = "Closed"

        if direction == "inbound" and is_operations_ai_auto_classify_enabled():
            load_context, load_candidates = _build_ai_load_context(classification, parsed)
            ai_suggestion = generate_operations_ai_suggestion(
                subject=subject,
                sender=sender,
                body=latest_body or body,
                parsed=parsed,
                rule_classification=_operations_ai_rule_context(classification, parsed, subject, latest_body or body),
                load_context=load_context,
                load_candidates=load_candidates,
                feedback_examples=_recent_operations_ai_feedback_examples(),
                response_language=_resolve_reply_language("Auto", subject, body),
                reply_tone="Professional",
                company_name=_get_app_setting("COMPANY_NAME", "CaliTrans"),
            )
            classification = _apply_ai_suggestion_to_classification(classification, ai_suggestion, load_candidates)
            classification["conversation_key"] = thread_conversation_key

        operations_case = {}
        case_id = None

        execute(
            """
            insert into order_intake (
                source,
                source_subject,
                source_sender,
                source_received_at,
                source_message_id,
                email_direction,
                email_mailbox,
                email_thread_id,
                email_normalized_subject,
                email_in_reply_to,
                email_references,
                conversation_status,
                email_synced_at,
                filename,
                file_path,
                parsed_data,
                raw_text,
                intake_status,
                review_status,
                request_type,
                conversation_key,
                matched_load_id,
                case_id,
                confidence_score,
                action_required
            )
            values (
                :source,
                :source_subject,
                :source_sender,
                :source_received_at,
                :source_message_id,
                :email_direction,
                :email_mailbox,
                :email_thread_id,
                :email_normalized_subject,
                :email_in_reply_to,
                cast(:email_references as jsonb),
                :conversation_status,
                now(),
                :filename,
                :file_path,
                cast(:parsed_data as jsonb),
                :raw_text,
                :intake_status,
                :review_status,
                :request_type,
                :conversation_key,
                :matched_load_id,
                :case_id,
                :confidence_score,
                :action_required
            )
            """,
            {
                "source": source,
                "source_subject": subject,
                "source_sender": sender,
                "source_received_at": received_at,
                "source_message_id": message_id or None,
                "email_direction": direction,
                "email_mailbox": metadata["mailbox"] or None,
                "email_thread_id": metadata["thread_id"] or None,
                "email_normalized_subject": metadata["normalized_subject"] or None,
                "email_in_reply_to": metadata["in_reply_to"] or None,
                "email_references": json.dumps(metadata["references"]),
                "conversation_status": conversation_status,
                "filename": saved_attachments[0].get("filename") if saved_attachments else None,
                "file_path": saved_attachments[0].get("file_path") if saved_attachments else None,
                "parsed_data": _json_dump(parsed),
                "raw_text": latest_body or body,
                "intake_status": intake_status,
                "review_status": review_status,
                "request_type": classification["request_type"],
                "conversation_key": classification["conversation_key"],
                "matched_load_id": classification["matched_load_id"],
                "case_id": case_id,
                "confidence_score": classification["confidence_score"],
                "action_required": classification["action_required"],
            },
        )
        if case_id is not None:
            cases_touched.add(case_id)
            _log_operations_case_event(
                case_id,
                "email_received" if direction == "inbound" else "replied",
                "Customer email received" if direction == "inbound" else "Outbound email synced",
                subject,
                actor="customer" if direction == "inbound" else "dispatcher",
                department=_safe_str(operations_case.get("owner", "")),
            )
            _sync_operations_case_summary(case_id)
        _sync_conversation_status(thread_conversation_key)
        imported += 1
        if direction == "outbound":
            outbound_imported += 1
        else:
            inbound_imported += 1

    return {
        "fetched": fetched,
        "imported": imported,
        "skipped": skipped,
        "pdf_updated": pdf_updated,
        "inbound_fetched": inbound_fetched,
        "outbound_fetched": outbound_fetched,
        "inbound_imported": inbound_imported,
        "outbound_imported": outbound_imported,
        "threads_synced": len(synced_threads),
        "cases_touched": len(cases_touched),
        "accounts_synced": len(synced_accounts),
        "elapsed_seconds": round((datetime.now() - sync_started_at).total_seconds(), 1),
    }


def import_recent_operations_emails(limit: int = 50) -> tuple[int, int, int, int]:
    result = sync_operations_email_engine(limit=limit)
    return (
        int(result.get("imported", 0)),
        int(result.get("skipped", 0)),
        int(result.get("fetched", 0)),
        int(result.get("pdf_updated", 0)),
    )


def _default_operations_reply_subject(subject: str, request_type: str) -> str:
    clean_subject = str(subject or "").strip()
    if clean_subject.lower().startswith("re:"):
        return clean_subject
    if clean_subject:
        return f"Re: {clean_subject}"
    return f"Re: {request_type}"


def _default_operations_action_subject(subject: str, request_type: str, action_mode: str) -> str:
    clean_subject = str(subject or "").strip()
    if action_mode == "Forward":
        if clean_subject.lower().startswith(("fw:", "fwd:")):
            return clean_subject
        return f"Fwd: {clean_subject}" if clean_subject else f"Fwd: {request_type}"
    return _default_operations_reply_subject(subject, request_type)


def _apply_operations_reply_tone(body: str, tone: str, language: str) -> str:
    tone = _safe_str(tone) or "Professional"
    if tone in ["Professional", "Concise"]:
        return body

    language = _safe_str(language)
    if tone == "Friendly":
        english_line = "Thank you for reaching out. "
        spanish_line = "Gracias por comunicarse con nosotros. "
    else:
        english_line = "We apologize for the delay and are reviewing this now. "
        spanish_line = "Disculpe la demora; estamos revisando esto ahora. "

    updated = body
    if language in ["English", "Bilingual"] and "Hello,\n\n" in updated:
        updated = updated.replace("Hello,\n\n", f"Hello,\n\n{english_line}", 1)
    if language in ["Spanish", "Bilingual"] and "Hola,\n\n" in updated:
        updated = updated.replace("Hola,\n\n", f"Hola,\n\n{spanish_line}", 1)
    return updated


def _default_operations_reply_body(
    request_type: str,
    parsed: dict,
    matched_load_id,
    subject: str = "",
    body: str = "",
    reply_language: str = "Auto",
    reply_tone: str = "Professional",
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
        return _apply_operations_reply_tone(english_reply, reply_tone, "English")

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
        return _apply_operations_reply_tone(spanish_reply, reply_tone, "Spanish")

    return _apply_operations_reply_tone(f"{english_reply}\n\n---\n\n{spanish_reply}", reply_tone, "Bilingual")


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

    if request_type == "Billing":
        return (
            "Hello,\n\n"
            f"We received your billing request for {reference}. "
            "We are routing it to Billing and will follow up with invoice, POD, or charge details once reviewed.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "Driver Issue":
        return (
            "Hello,\n\n"
            f"We received the driver issue for {reference}. "
            "Dispatch is reviewing the driver, truck, appointment, and current load status now and will update you shortly.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "Port Issue":
        return (
            "Hello,\n\n"
            f"We received the port or terminal issue for {reference}. "
            "Dispatch is checking terminal status, holds, gate activity, and appointment impact now.\n\n"
            f"Thank you,\n{company_name} Dispatch"
        )

    if request_type == "Spam/Marketing":
        return (
            "Hello,\n\n"
            "Thank you for the information. At this time, no dispatch action is required.\n\n"
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

    if request_type == "Billing":
        return (
            "Hola,\n\n"
            f"Recibimos su solicitud de facturacion para {reference}. "
            "La enviaremos al equipo de Billing y le daremos seguimiento con detalles de factura, POD o cargos cuando este revisado.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "Driver Issue":
        return (
            "Hola,\n\n"
            f"Recibimos el asunto del conductor para {reference}. "
            "Despacho esta revisando el conductor, camion, cita y estado actual de la carga. Le actualizaremos en breve.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "Port Issue":
        return (
            "Hola,\n\n"
            f"Recibimos el asunto de puerto o terminal para {reference}. "
            "Despacho esta revisando el estado de terminal, retenciones, actividad de gate e impacto en la cita.\n\n"
            f"Gracias,\n{company_name} Dispatch"
        )

    if request_type == "Spam/Marketing":
        return (
            "Hola,\n\n"
            "Gracias por la informacion. Por el momento no se requiere accion de despacho.\n\n"
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
    case_id=None,
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
            case_id,
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
            :case_id,
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
            "case_id": _int_or_none(case_id),
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "status": status,
            "error_message": error_message or None,
        },
    )


def _insert_operations_thread_reply_record(
    *,
    intake_id: int,
    record,
    reply_from: str,
    reply_to: str,
    reply_subject: str,
    reply_body: str,
    reply_cc: str = "",
    request_type: str,
    conversation_key: str,
    matched_load_id,
    case_id=None,
    create_case_if_missing: bool = False,
) -> None:
    source_message_id = _safe_str(record.get("source_message_id", "")) if hasattr(record, "get") else ""
    email_thread_id = _safe_str(record.get("email_thread_id", "")) if hasattr(record, "get") else ""
    normalized_subject = _safe_str(record.get("email_normalized_subject", "")) if hasattr(record, "get") else ""
    outbound_message_id = f"tms-reply-{intake_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    references = [value for value in [source_message_id, email_thread_id] if value]
    case_id = _int_or_none(case_id)
    if case_id is None and create_case_if_missing:
        operations_case = _get_or_create_operations_case(
            conversation_key=conversation_key,
            subject=reply_subject,
            sender=reply_to,
            request_type=request_type,
            matched_load_id=matched_load_id,
            direction="outbound",
            next_action="Reply sent; waiting on customer response.",
            body=reply_body,
        )
        case_id = _int_or_none(operations_case.get("id"))

    execute(
        """
        insert into order_intake (
            source,
            source_subject,
            source_sender,
            source_received_at,
            source_message_id,
            email_direction,
            email_mailbox,
            email_thread_id,
            email_normalized_subject,
            email_in_reply_to,
            email_references,
            conversation_status,
            email_synced_at,
            parsed_data,
            raw_text,
            intake_status,
            review_status,
            request_type,
            conversation_key,
            matched_load_id,
            case_id,
            confidence_score,
            action_required
        )
        values (
            'operations_email_sent',
            :source_subject,
            :source_sender,
            now(),
            :source_message_id,
            'outbound',
            'TMS',
            :email_thread_id,
            :email_normalized_subject,
            :email_in_reply_to,
            cast(:email_references as jsonb),
            'Waiting Customer',
            now(),
            cast(:parsed_data as jsonb),
            :raw_text,
            'Synced',
            'Closed',
            :request_type,
            :conversation_key,
            :matched_load_id,
            :case_id,
            100,
            'Dispatcher reply sent from TMS.'
        )
        """,
        {
            "source_subject": reply_subject,
            "source_sender": reply_from or "TMS",
            "source_message_id": outbound_message_id,
            "email_thread_id": email_thread_id or conversation_key or source_message_id or outbound_message_id,
            "email_normalized_subject": normalized_subject or _safe_str(reply_subject).lower(),
            "email_in_reply_to": source_message_id or None,
            "email_references": json.dumps(references),
            "parsed_data": _json_dump(
                {
                    "_email_sync": {
                        "direction": "outbound",
                        "mailbox": f"{reply_from}:TMS" if reply_from else "TMS",
                        "mailbox_account": reply_from,
                        "mailbox_folder": "TMS",
                        "thread_id": email_thread_id,
                        "conversation_key": conversation_key,
                        "in_reply_to": source_message_id,
                        "references": references,
                        "from": reply_from,
                        "to": reply_to,
                        "cc": reply_cc,
                    }
                }
            ),
            "raw_text": reply_body,
            "request_type": request_type,
            "conversation_key": conversation_key,
            "matched_load_id": matched_load_id,
            "case_id": case_id,
        },
    )
    _sync_conversation_status(conversation_key)
    if case_id is not None:
        execute(
            """
            update operations_cases
            set first_response_at = coalesce(first_response_at, now())
            where id = :case_id
            """,
            {"case_id": case_id},
        )
        _log_operations_case_event(
            case_id,
            "replied",
            "Customer reply sent",
            reply_subject,
            actor="dispatcher",
        )
        _set_operations_case_status(case_id, "Waiting Customer", "Reply sent; waiting on customer response.")
        _sync_operations_case_summary(case_id)


def auto_classify_open_inbox_items(inbox_df: pd.DataFrame) -> int:
    updated_count = 0

    for _, row in inbox_df.iterrows():
        current_type = str(row.get("request_type", "") or "").strip()
        if current_type == "Spam/Marketing":
            execute(
                """
                update order_intake
                set review_status = 'Closed',
                    intake_status = 'Closed',
                    conversation_status = 'Closed',
                    action_required = coalesce(nullif(action_required, ''), 'Marketing or non-operational email auto-archived.')
                where id = :intake_id
                """,
                {"intake_id": int(row["id"])},
            )
            updated_count += 1
            continue
        existing_match = row.get("matched_load_id")
        existing_has_match = pd.notna(existing_match) and _safe_str(existing_match) != ""
        existing_confidence = pd.to_numeric(row.get("confidence_score", 0), errors="coerce")
        if pd.isna(existing_confidence):
            existing_confidence = 0
        subject_for_classification = _safe_str(row.get("source_subject", ""))
        latest_text_for_classification = extract_latest_email_body(_safe_str(row.get("raw_text", ""))) or _safe_str(row.get("raw_text", ""))

        current_is_action_type = current_type in [
            "New Booking",
            "Booking Update",
            "Appointment Update",
            "Quote Request",
            "Cancellation",
            "POD Request",
            "Billing",
            "Driver Issue",
            "Port Issue",
        ]
        needs_classification = current_type in ["", "Needs Classification", "Other"]
        needs_correction_check = current_is_action_type and not existing_has_match and existing_confidence < 70
        obvious_info_new_booking = (
            current_type == "New Booking"
            and _is_information_update(f"{subject_for_classification}\n{latest_text_for_classification}")
            and not _has_order_placement_signal(f"{subject_for_classification}\n{latest_text_for_classification}")
        )

        if not needs_classification and not needs_correction_check and not obvious_info_new_booking:
            continue

        intake_id = int(row["id"])
        subject = subject_for_classification
        body = latest_text_for_classification
        parsed = _coerce_json_dict(row.get("parsed_data"))
        intake_id = int(row["id"])
        subject = subject_for_classification
        body = latest_text_for_classification
        parsed = _coerce_json_dict(row.get("parsed_data"))

        sender = _safe_str(row.get("source_sender", ""))
        thread_conversation_key = _row_conversation_join_key(row)

        intent_result = intent_agent.analyze(
            subject=subject,
            body=body,
            sender=sender,
        )

        classification = _build_operations_email_classification(
            subject,
            body,
            parsed,
            fallback_key=thread_conversation_key or f"intake-{intake_id}",
            sender=sender,
        )

        classification["conversation_key"] = thread_conversation_key

        parsed["_intent_agent"] = intent_result

        parser_result = operations_parser_agent.analyze(
            subject=subject,
            body=body,
            intent_result=intent_result,
            existing_load=None,
        )

        parsed["_operations_parser_agent"] = parser_result

        tokens = _extract_reference_tokens(f"{subject}\n{body}\n{parsed}")
        matched_load_id = classification.get("matched_load_id")

        load_candidates = find_load_match_candidates(
            tokens,
            parsed=parsed,
            subject=subject,
            body=body,
            limit=5,
        )

        load_intelligence_result = load_intelligence_agent.analyze(
            intent_result=intent_result,
            parser_result=parser_result,
            load_candidates=load_candidates,
            conversation_context={
                "matched_load_id": matched_load_id,
                "conversation_key": thread_conversation_key,
            },
        )

        parsed["_load_intelligence_agent"] = load_intelligence_result

        if load_intelligence_result.get("matched_load_id"):
            matched_load_id = load_intelligence_result["matched_load_id"]
            classification["matched_load_id"] = matched_load_id

        workflow_result = workflow_agent.analyze(
            intent_result=intent_result,
            parser_result=parser_result,
            load_intelligence_result=load_intelligence_result,
            existing_case=None,
        )

        parsed["_workflow_agent"] = workflow_result
            response_result = response_agent.analyze(
            subject=subject,
            body=body,
            sender=sender,
            intent_result=intent_result,
            parser_result=parser_result,
            load_intelligence_result=load_intelligence_result,
            workflow_result=workflow_result,
            existing_load=None,
            company_memory={},
        )

        parsed["_response_agent"] = response_result
         # Agent 2 enrichment
        classification["ai_intent"] = intent_result["primary_intent"]
        classification["ai_language"] = intent_result["language"]
        classification["ai_department_owner"] = intent_result["department_owner"]
        classification["ai_confidence"] = intent_result["confidence"]
        classification["ai_recommended_action"] = intent_result["recommended_action"]
        classification["ai_reason"] = intent_result["reason"]

        detected_type = classification["request_type"]
        matched_load_id = classification["matched_load_id"]
        confidence = classification["confidence_score"]

        should_update = needs_classification or obvious_info_new_booking or (
            needs_correction_check and detected_type in ["Customer Request", "Booking Update", "Missing Information"]
        )
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
        if detected_type == "Spam/Marketing":
            execute(
                """
                update order_intake
                set review_status = 'Closed',
                    intake_status = 'Closed',
                    conversation_status = 'Closed'
                where id = :intake_id
                """,
                {"intake_id": intake_id},
            )
        updated_count += 1

    return updated_count


def sync_operations_case_links_for_inbox_items(inbox_df: pd.DataFrame) -> int:
    relinked_count = 0
    for _, row in inbox_df.iterrows():
        try:
            old_case_id = _int_or_none(row.get("case_id"))
            case = _sync_operations_case_for_intake_record(row)
            new_case_id = _int_or_none(case.get("id"))
            if new_case_id is not None and new_case_id != old_case_id:
                relinked_count += 1
        except Exception:
            continue
    return relinked_count


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
    missing = []
    if not (_safe_str(parsed.get("Booking Number", "")) or _safe_str(parsed.get("Reference Number", ""))):
        missing.append("Booking or Reference Number")
    if not _safe_str(parsed.get("Customer", "")):
        missing.append("Customer")
    if not (_safe_str(parsed.get("Warehouse", "")) or _safe_str(parsed.get("Address", ""))):
        missing.append("Warehouse or Address")
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
        sender="",
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

def _attach_saved_operations_file_to_load(load_id: int, filename: str, file_path: str, source: str = "operations_inbox_attachment") -> None:
    document_type = "load_order" if _safe_str(filename).lower().endswith(".pdf") else "operations_attachment"
    execute(
        """
        insert into documents (load_id, document_type, filename, file_path, source)
        select :load_id, :document_type, :filename, :file_path, :source
        where not exists (
            select 1
            from documents
            where load_id = :load_id
              and file_path = :file_path
        )
        """,
        {
            "load_id": int(load_id),
            "document_type": document_type,
            "filename": filename,
            "file_path": file_path,
            "source": source,
        },
    )


def _attach_saved_pdf_to_load(load_id: int, filename: str, file_path: str, source: str = "operations_inbox_pdf") -> None:
    _attach_saved_operations_file_to_load(load_id, filename, file_path, source)


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


def _import_uploaded_operations_attachment(intake_id: int, parsed: dict, uploaded_file) -> dict:
    content = uploaded_file.getvalue()
    attachment = _save_operations_attachment(
        content=content,
        filename=uploaded_file.name,
        message_id=f"intake-{intake_id}",
        attachment_index=len(_extract_operations_attachments(parsed)) + 1,
        content_type=getattr(uploaded_file, "type", "") or "",
    )

    updated_parsed = dict(parsed)
    attachments = _extract_operations_attachments(updated_parsed)
    attachments.append(attachment)
    updated_parsed = _merge_saved_attachment_fields(updated_parsed, attachments)

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


def _import_uploaded_pdf_to_operations_request(intake_id: int, parsed: dict, uploaded_file) -> dict:
    return _import_uploaded_operations_attachment(intake_id, parsed, uploaded_file)


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
    attachments = _extract_operations_attachments(parsed, record)
    case_id = _int_or_none(record.get("case_id")) if hasattr(record, "get") else None

    with st.expander("Email Attachments / Order Documents", expanded=bool(attachments)):
        uploaded_pdf = st.file_uploader(
            "Add document to this request",
            type=["pdf", "docx", "txt", "csv", "png", "jpg", "jpeg"],
            key=f"operations_pdf_upload_{selected_id}",
        )
        if uploaded_pdf is not None:
            if st.button(
                "Import Uploaded Document",
                key=f"operations_pdf_import_upload_{selected_id}",
                use_container_width=True,
            ):
                attachment = _import_uploaded_operations_attachment(int(selected_id), parsed, uploaded_pdf)
                st.success(f"Imported document: {attachment.get('filename', uploaded_pdf.name)}")
                refresh_data()
                st.rerun()

        if not attachments:
            if _message_mentions_attachment(subject, body):
                st.warning("This email mentions an attachment or order document, but no files are saved on this request yet.")
            else:
                st.info("No email attachments were saved with this inbox request yet.")

            if st.button(
                "Rescan This Email for Attachments",
                key=f"operations_attachment_rescan_{selected_id}",
                use_container_width=True,
            ):
                with st.spinner("Checking the mailbox for this email's attachments..."):
                    try:
                        rescan_result = _rescan_operations_request_attachments(record)
                    except Exception as exc:
                        st.error(f"Attachment rescan failed: {exc}")
                        return

                saved_count = int(rescan_result.get("saved", 0) or 0)
                found_count = int(rescan_result.get("source_attachment_count", 0) or 0)
                matched_count = int(rescan_result.get("matched", 0) or 0)
                names = rescan_result.get("attachment_names") or []
                if saved_count:
                    st.success(f"Saved {saved_count} attachment(s): {', '.join(names) if names else 'document files'}.")
                    refresh_data()
                    st.rerun()
                elif matched_count and found_count:
                    st.info("The mailbox found this email and attachment metadata, but no new files needed to be saved.")
                elif matched_count:
                    st.warning("The mailbox found this email, but it did not include downloadable attachments in the scanned message.")
                else:
                    st.warning("No matching email was found in the recent mailbox scan. Try increasing the email sync window or import the document manually here.")
            return

        labels = []
        for idx, attachment in enumerate(attachments):
            filename = _safe_str(attachment.get("filename", f"attachment_{idx + 1}"))
            fields_found = int(attachment.get("fields_found", 0) or 0)
            content_type = _safe_str(attachment.get("content_type", ""))
            doc_kind = "PDF" if _is_pdf_filename(filename, content_type) else (content_type or "document")
            labels.append(f"{idx + 1}. {filename} | {doc_kind} | {fields_found} field(s) found")

        selected_label = st.selectbox(
            "Select Attachment",
            labels,
            key=f"operations_pdf_select_{selected_id}",
        )
        selected_index = labels.index(selected_label)
        attachment = attachments[selected_index]
        filename = _safe_str(attachment.get("filename", f"attachment_{selected_index + 1}"))
        file_path = _safe_str(attachment.get("file_path", ""))
        content_type = _safe_str(attachment.get("content_type", "application/octet-stream")) or "application/octet-stream"
        is_pdf = _is_pdf_filename(filename, content_type) or bool(attachment.get("is_pdf"))

        if not file_path or not Path(file_path).exists():
            st.warning("The saved attachment file could not be found on disk.")
            return

        try:
            content = _read_operations_attachment_bytes(file_path)
        except Exception as exc:
            st.error(f"Could not read saved attachment: {exc}")
            return

        d1, d2, d3 = st.columns([1, 1, 2])
        with d1:
            st.download_button(
                "Download Attachment",
                data=content,
                file_name=filename,
                mime=content_type,
                key=f"operations_pdf_download_{selected_id}_{selected_index}",
                use_container_width=True,
            )
        with d2:
            show_preview = st.checkbox(
                "View File",
                value=False,
                key=f"operations_pdf_preview_{selected_id}_{selected_index}",
            )
        with d3:
            st.caption(f"Saved file: {filename}")

        if show_preview:
            if is_pdf:
                _render_pdf_preview(content, filename)
            elif content_type.startswith("image/"):
                st.image(content, caption=filename, use_container_width=True)
            else:
                st.info("Preview is available for PDFs and images. Download this document to view it.")

        try:
            document_text, document_parsed = _parse_saved_operations_attachment(file_path, filename, content_type)
            parse_error = ""
        except Exception as exc:
            document_text = _safe_str(attachment.get("text_preview", ""))
            document_parsed = attachment.get("parsed_data") or {}
            parse_error = str(exc)

        if parse_error:
            st.warning(f"Document text parse needs review: {parse_error}")

        try:
            body_parsed = parse_email_text(subject, body, sender)
        except Exception:
            body_parsed = {}

        base_parsed = {}
        for field in OPERATIONS_ORDER_FIELDS:
            base_parsed[field] = _safe_str(body_parsed.get(field, "")) or _safe_str(parsed.get(field, ""))

        final_data, comparison_rows, conflicts = _merge_operations_order_fields(base_parsed, document_parsed)
        final_data[OPERATIONS_ATTACHMENTS_KEY] = attachments
        pdf_attachments = [
            item
            for item in attachments
            if _is_pdf_filename(item.get("filename", ""), item.get("content_type", "")) or bool(item.get("is_pdf"))
        ]
        if pdf_attachments:
            final_data[OPERATIONS_PDF_ATTACHMENTS_KEY] = pdf_attachments

        c1, c2, c3 = st.columns(3)
        c1.metric("Email Fields", _field_count(body_parsed))
        c2.metric("Document Fields", _field_count(document_parsed))
        c3.metric("Mismatches", len(conflicts))

        if conflicts:
            st.warning("Review mismatched fields before creating or updating a load: " + ", ".join(conflicts))

        st.dataframe(pd.DataFrame(comparison_rows), use_container_width=True, hide_index=True)

        with st.expander("Extracted Document Text", expanded=False):
            st.text_area(
                "Document Text",
                value=document_text or "No text was extracted from this document.",
                height=220,
                disabled=True,
                key=f"operations_pdf_text_{selected_id}_{selected_index}",
            )

        st.caption("Use document data here to update the parsed request, then complete order, quote, or close actions in the Order / Quote Actions section below.")

        b1, b2 = st.columns(2)
        with b1:
            if st.button("Use Document Data", key=f"use_pdf_data_{selected_id}_{selected_index}", use_container_width=True):
                _save_pdf_data_to_operations_request(
                    intake_id=int(selected_id),
                    subject=subject,
                    body=body,
                    parsed_data=final_data,
                    filename=filename,
                    file_path=file_path,
                    pdf_text=document_text,
                )
                st.success("Document data saved to this Operations request.")
                refresh_data()
                st.rerun()

        with b2:
            if st.button(
                "Attach Document",
                key=f"attach_pdf_to_load_{selected_id}_{selected_index}",
                use_container_width=True,
                disabled=matched_load_id is None,
            ):
                _attach_saved_operations_file_to_load(int(matched_load_id), filename, file_path)
                save_load_communication(
                    matched_load_id,
                    int(selected_id),
                    conversation_key,
                    "Document Attachment",
                    subject,
                    sender,
                    f"Attached document from Operations Inbox: {filename}",
                    case_id=case_id,
                )
                if case_id is not None:
                    _update_operations_case(
                        case_id=case_id,
                        status="Attached to Load",
                        owner=_safe_str(record.get("case_owner", "Dispatch")) or "Dispatch",
                        priority=_safe_str(record.get("case_priority", "Normal")) or "Normal",
                        linked_load_id=matched_load_id,
                        next_action=f"Document {filename} attached to load {matched_load_id}.",
                    )
                st.success("Document attached to the matched load.")
                st.rerun()


@st.cache_data(show_spinner=False, ttl=30)
def _load_operations_case_email_summary(case_id) -> dict:
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


def _format_short_timestamp(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%m/%d %I:%M %p")


def _format_relative_timestamp(value) -> str:
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


def _format_case_sla_label(operations_case: dict) -> str:
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


def _render_operations_case_summary_header(
    *,
    operations_case: dict,
    record,
    parsed: dict,
    tokens: dict,
    matched_load_id,
) -> None:
    case_id = _int_or_none(operations_case.get("id"))
    if case_id is None:
        return

    summary = _load_operations_case_email_summary(case_id)
    customer = (
        _safe_str(operations_case.get("customer", ""))
        or _safe_str(parsed.get("Customer", ""))
        or _case_customer_from_sender(record.get("source_sender", "") if hasattr(record, "get") else "")
    )
    booking = _safe_str(parsed.get("Booking Number", "")) or _safe_str(tokens.get("booking_number", "")) or "Pending"
    container = _safe_str(parsed.get("Container Number", "")) or _safe_str(tokens.get("container_number", "")) or "Pending"
    linked_load_id = _int_or_none(operations_case.get("linked_load_id")) or _int_or_none(matched_load_id)
    last_reply_by = _safe_str(summary.get("last_reply_by", ""))
    last_reply_mailbox = _safe_str(summary.get("last_reply_mailbox", ""))
    last_reply_time = _format_short_timestamp(summary.get("last_reply_at"))
    if last_reply_by and last_reply_time:
        reply_method = "TMS" if last_reply_mailbox.lower() == "tms" else "Yahoo Mail"
        last_reply = f"{_case_customer_from_sender(last_reply_by)} - {last_reply_time} ({reply_method})"
    else:
        last_reply = "-"

    st.markdown("### Operations Case Workspace")
    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    r1c1.metric("Case #", _safe_str(operations_case.get("case_number", "")) or f"Case {case_id}")
    r1c2.metric("Customer", customer or "-")
    r1c3.metric("Booking", booking)
    r1c4.metric("Container", container)

    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    r2c1.metric("Load", linked_load_id or "Not Created")
    r2c2.metric("Priority", _safe_str(operations_case.get("priority", "")) or "-")
    r2c3.metric("Owner", _safe_str(operations_case.get("owner", "")) or "Unassigned")
    r2c4.metric("Status", _safe_str(operations_case.get("status", "")) or "-")

    r3c1, r3c2, r3c3, r3c4 = st.columns(4)
    r3c1.metric("Last Reply", last_reply)
    r3c2.metric("Last Customer Email", _format_relative_timestamp(summary.get("last_customer_email_at")))
    r3c3.metric("Total Messages", int(summary.get("total_messages", 0) or operations_case.get("message_count", 0) or 0))
    r3c4.metric("SLA", _format_case_sla_label(operations_case))


def _render_operations_case_panel(
    *,
    selected_id: int,
    operations_case: dict,
    matched_load_id,
    show_timeline: bool = True,
) -> None:
    case_id = _int_or_none(operations_case.get("id"))
    if case_id is None:
        st.warning("No Operations Case is linked yet. Save classification or refresh this request to create one.")
        return

    case_number = _safe_str(operations_case.get("case_number", "")) or f"Case #{case_id}"
    case_status = _safe_str(operations_case.get("status", "New")) or "New"
    case_owner = _safe_str(operations_case.get("owner", "Unassigned")) or "Unassigned"
    case_priority = _safe_str(operations_case.get("priority", "Normal")) or "Normal"
    case_sla_status = _safe_str(operations_case.get("sla_status", "On Track")) or "On Track"
    linked_load_id = _int_or_none(operations_case.get("linked_load_id")) or _int_or_none(matched_load_id)

    with st.expander(f"Operations Case - {case_number}", expanded=True):
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Case", case_number)
        c2.metric("Status", case_status)
        c3.metric("Owner", case_owner)
        c4.metric("Priority", case_priority)
        c5.metric("Linked Load", linked_load_id or "-")
        c6.metric("SLA", case_sla_status)
        due1, due2 = st.columns(2)
        due1.caption(f"First response due: {_safe_str(operations_case.get('first_response_due_at', '')) or '-'}")
        due2.caption(f"Resolution due: {_safe_str(operations_case.get('resolution_due_at', '')) or '-'}")

        with st.form(f"operations_case_update_{case_id}_{selected_id}"):
            f1, f2, f3, f4 = st.columns([1, 1, 1, 1])
            status_options = list(OPERATIONS_CASE_STATUSES)
            if case_status not in status_options:
                status_options.insert(0, case_status)
            owner_options = list(OPERATIONS_CASE_OWNERS)
            case_owner_is_known = case_owner in owner_options
            if case_owner not in owner_options:
                owner_options.insert(0, case_owner)
            priority_options = list(OPERATIONS_CASE_PRIORITIES)
            if case_priority not in priority_options:
                priority_options.insert(0, case_priority)

            new_status = f1.selectbox(
                "Case Status",
                status_options,
                index=status_options.index(case_status),
                key=f"case_status_{case_id}_{selected_id}",
            )
            new_owner = f2.selectbox(
                "Owner",
                owner_options,
                index=owner_options.index(case_owner),
                key=f"case_owner_{case_id}_{selected_id}",
            )
            new_priority = f3.selectbox(
                "Priority",
                priority_options,
                index=priority_options.index(case_priority),
                key=f"case_priority_{case_id}_{selected_id}",
            )
            new_linked_load_id = f4.number_input(
                "Linked Load ID",
                min_value=0,
                value=int(linked_load_id or 0),
                step=1,
                key=f"case_linked_load_{case_id}_{selected_id}",
            )
            custom_owner = st.text_input(
                "Custom Owner",
                value="" if case_owner_is_known else case_owner,
                placeholder="Optional dispatcher or manager name",
                key=f"case_custom_owner_{case_id}_{selected_id}",
            )
            next_action = st.text_area(
                "Next Action",
                value=_safe_str(operations_case.get("next_action", "")),
                height=80,
                key=f"case_next_action_{case_id}_{selected_id}",
            )
            if st.form_submit_button("Save Case"):
                final_owner = _safe_str(custom_owner) or new_owner
                _update_operations_case(
                    case_id=case_id,
                    status=new_status,
                    owner=final_owner,
                    priority=new_priority,
                    linked_load_id=new_linked_load_id or None,
                    next_action=next_action,
                )
                refresh_data()
                st.success("Operations Case updated.")
                st.rerun()

        q1, q2, q3, q4 = st.columns(4)
        with q1:
            if st.button("Waiting Customer", key=f"case_waiting_customer_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Customer", "Waiting on customer response.")
                refresh_data()
                st.rerun()
        with q2:
            if st.button("Waiting Dispatcher", key=f"case_waiting_dispatcher_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Dispatcher", "Dispatcher needs to review and respond.")
                refresh_data()
                st.rerun()
        with q3:
            if st.button("Close Case", key=f"case_close_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Closed", "Case closed by operations.")
                execute(
                    """
                    update order_intake
                    set review_status = 'Closed'
                    where case_id = :case_id
                       or id = :intake_id
                    """,
                    {"case_id": case_id, "intake_id": int(selected_id)},
                )
                refresh_data()
                st.rerun()
        with q4:
            if st.button("Reopen Case", key=f"case_reopen_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Reopened", "Case reopened by operations.")
                execute(
                    """
                    update order_intake
                    set review_status = 'Open'
                    where case_id = :case_id
                       or id = :intake_id
                    """,
                    {"case_id": case_id, "intake_id": int(selected_id)},
                )
                refresh_data()
                st.rerun()

        w1, w2, w3, w4 = st.columns(4)
        with w1:
            if st.button("Waiting Manager", key=f"case_waiting_manager_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Manager", "Waiting on manager review.")
                refresh_data()
                st.rerun()
        with w2:
            if st.button("Waiting Driver", key=f"case_waiting_driver_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Driver", "Waiting on driver update.")
                refresh_data()
                st.rerun()
        with w3:
            if st.button("Waiting Port", key=f"case_waiting_port_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Port", "Waiting on port or terminal response.")
                refresh_data()
                st.rerun()
        with w4:
            if st.button("Waiting Warehouse", key=f"case_waiting_warehouse_{case_id}_{selected_id}", use_container_width=True):
                _set_operations_case_status(case_id, "Waiting Warehouse", "Waiting on warehouse response.")
                refresh_data()
                st.rerun()

        note_body = st.text_area(
            "Internal Note",
            value="",
            height=90,
            key=f"case_note_{case_id}_{selected_id}",
            placeholder="Internal notes stay inside Operations and do not go to the customer.",
        )
        if st.button("Add Internal Note", key=f"case_add_note_{case_id}_{selected_id}", use_container_width=True):
            if not note_body.strip():
                st.error("Internal note is blank.")
            else:
                _add_operations_case_note(case_id, note_body.strip())
                refresh_data()
                st.success("Internal note added.")
                st.rerun()

        if show_timeline:
            timeline_df = _load_operations_case_timeline(case_id)
            if timeline_df.empty:
                st.info("Case timeline will appear after emails, notes, replies, or load actions are linked.")
            else:
                timeline_display = timeline_df.copy()
                timeline_display["event_time"] = pd.to_datetime(
                    timeline_display["event_at"],
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")
                timeline_display = timeline_display[
                    ["event_time", "event_type", "actor", "title", "details"]
                ].rename(
                    columns={
                        "event_time": "Time",
                        "event_type": "Type",
                        "actor": "Actor",
                        "title": "Title",
                        "details": "Details",
                    }
                )
                st.dataframe(timeline_display, use_container_width=True, hide_index=True)

        owner_history_df = _load_operations_case_owner_history(case_id)
        if not owner_history_df.empty:
            with st.expander("Ownership History", expanded=False):
                history_display = owner_history_df.copy()
                history_display["changed_at"] = pd.to_datetime(
                    history_display["changed_at"],
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")
                st.dataframe(history_display, use_container_width=True, hide_index=True)

        recent_cases = _load_recent_operations_cases(case_id)
        if not recent_cases.empty:
            st.caption("Duplicate Case Merge")
            case_options = [None] + recent_cases.to_dict("records")

            def _case_merge_label(option) -> str:
                if option is None:
                    return "Select target case"
                return (
                    f"{option.get('case_number')} | {option.get('status')} | "
                    f"{option.get('customer') or '-'} | {option.get('source_subject') or '-'}"
                )

            target_case = st.selectbox(
                "Merge this case into",
                case_options,
                format_func=_case_merge_label,
                key=f"case_merge_target_{case_id}_{selected_id}",
            )
            if st.button(
                "Merge Duplicate Case",
                key=f"case_merge_{case_id}_{selected_id}",
                use_container_width=True,
                disabled=target_case is None,
            ):
                if _merge_operations_cases(case_id, target_case.get("id")):
                    refresh_data()
                    st.success(f"Merged {case_number} into {target_case.get('case_number')}.")
                    st.rerun()
                else:
                    st.error("Could not merge the selected cases.")


def _render_ops_metric_card(label: str, value, subtext: str = "") -> None:
    st.markdown(
        f"""
        <div class="ops-metric-card">
            <div class="ops-metric-label">{escape(str(label))}</div>
            <div class="ops-metric-value">{escape(str(value))}</div>
            {f'<div class="ops-metric-sub">{escape(str(subtext))}</div>' if subtext else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_operations_inbox() -> None:
    st.markdown(
        """
        <div class="ops-header">
            <div class="ops-kicker">Operations</div>
            <div class="ops-title">Operations Control Center</div>
            <div class="ops-subtitle">
                Manage work, not just email: triage operational cases, business communications, and archive/no-action messages from one dispatch workspace.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    try:
        _ensure_operations_email_sync_schema()
    except Exception as exc:
        st.warning(f"Email sync schema is not ready yet: {exc}")
        st.caption(f"Database config source: {get_config_source('DATABASE_URL')}")

    c1, c2, c3 = st.columns([1, 1, 3])

    with c1:
        if st.button("Refresh Inbox", use_container_width=True):
            refresh_data()
            st.rerun()

    with c2:
        if st.button("Sync Email Engine", use_container_width=True):
            try:
                st.session_state["operations_email_import_result"] = sync_operations_email_engine(limit=50)
                refresh_data()
                st.rerun()
            except Exception as exc:
                st.error(f"Could not synchronize email: {exc}")

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
                pdf_note = f", updated {pdf_updated} email attachment(s)" if pdf_updated else ""
                inbound = int(result.get("inbound_fetched", 0))
                outbound = int(result.get("outbound_fetched", 0))
                threads = int(result.get("threads_synced", 0))
                cases = int(result.get("cases_touched", 0))
                accounts = int(result.get("accounts_synced", 0))
                elapsed = result.get("elapsed_seconds")
                account_note = f" across {accounts} account(s)" if accounts else ""
                elapsed_note = f" in {elapsed}s" if elapsed is not None else ""
                st.success(
                    f"Email sync fetched {fetched} message(s) "
                    f"({inbound} inbox, {outbound} sent){account_note}{elapsed_note}, "
                    f"imported {imported}, skipped {skipped}, "
                    f"threaded {threads} conversation(s), updated {cases} case(s){pdf_note}."
                )
        else:
            st.markdown(
                """
                <div class="ops-alert">
                    Use Sync Email Engine to import Inbox and Sent mail with Message-ID, References, thread IDs, timestamps, attachments, and deduplication.
                </div>
                """,
                unsafe_allow_html=True,
            )

    sync_metrics = _operations_email_sync_metrics()
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        _render_ops_metric_card("Synced Inbox", int(sync_metrics.get("inbound", 0)))
    with s2:
        _render_ops_metric_card("Synced Sent", int(sync_metrics.get("outbound", 0)))
    with s3:
        _render_ops_metric_card("Email Threads", int(sync_metrics.get("threads", 0)))
    with s4:
        _render_ops_metric_card("Last Sync", sync_metrics.get("last_sync") or "-")

    case_metrics = _operations_case_metrics()
    cm1, cm2, cm3, cm4 = st.columns(4)
    with cm1:
        _render_ops_metric_card("Open Cases", int(case_metrics.get("open", 0)))
    with cm2:
        _render_ops_metric_card("Waiting Dispatch", int(case_metrics.get("waiting_dispatch", 0)))
    with cm3:
        _render_ops_metric_card("Waiting Customer", int(case_metrics.get("waiting_customer", 0)))
    with cm4:
        _render_ops_metric_card("Closed Cases", int(case_metrics.get("closed", 0)))
            
    try:
        where_clause = _inbox_review_where_clause()
        inbox_df = _load_operations_inbox_df(where_clause)
    except Exception as exc:
        st.error(f"Could not load Operations Inbox: {exc}")
        st.caption(f"Database config source: {get_config_source('DATABASE_URL')}")
        st.info("If this is the first time using Operations Inbox email, run database/operations_email_workflow_migration.sql in Supabase.")
        return

    if inbox_df.empty:
        st.success("No open customer requests.")
        _render_no_open_inbox_explanation()
        return

    if "raw_text_preview" in inbox_df.columns:
        inbox_df["raw_text_preview"] = inbox_df["raw_text_preview"].fillna("").astype(str).apply(extract_latest_email_body)

    inbox_df["conversation_join_key"] = inbox_df.apply(_row_conversation_join_key, axis=1)
    conversation_summary = _load_operations_conversation_summary_df()
    if not conversation_summary.empty:
        inbox_df = inbox_df.merge(
            conversation_summary,
            on="conversation_join_key",
            how="left",
        )
    for column in ["latest_direction", "latest_conversation_status", "conversation_message_count"]:
        if column not in inbox_df.columns:
            inbox_df[column] = ""
    inbox_df["latest_direction"] = inbox_df["latest_direction"].fillna(inbox_df["email_direction"].fillna("inbound"))
    inbox_df["reply_status"] = inbox_df["latest_conversation_status"].fillna(inbox_df["conversation_status"].fillna("New Conversation"))
    inbox_df["conversation_message_count"] = pd.to_numeric(
        inbox_df["conversation_message_count"],
        errors="coerce",
    ).fillna(1).astype(int)
    inbox_df["last_message_at"] = pd.to_datetime(
        inbox_df.get("last_message_at", pd.Series(dtype=str)),
        errors="coerce",
    ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")

    inbox_df["request_type_clean"] = (
        inbox_df["request_type"]
                .fillna("Needs Classification")
                .astype(str)
                .str.strip()
        )
    inbox_df["request_type_clean"] = inbox_df.apply(_effective_operations_request_type_for_row, axis=1)
    inbox_df["request_type"] = inbox_df["request_type_clean"]
    inbox_df["confidence_score"] = pd.to_numeric(
        inbox_df["confidence_score"],
        errors="coerce",
    ).fillna(0).astype(int)
    attachment_count_source = inbox_df["attachment_count"] if "attachment_count" in inbox_df.columns else pd.Series(0, index=inbox_df.index)
    source_attachment_count_source = (
        inbox_df["source_attachment_count"] if "source_attachment_count" in inbox_df.columns else pd.Series(0, index=inbox_df.index)
    )
    inbox_df["attachment_count"] = pd.to_numeric(
        attachment_count_source,
        errors="coerce",
    ).fillna(0).astype(int)
    inbox_df["source_attachment_count"] = pd.to_numeric(
        source_attachment_count_source,
        errors="coerce",
    ).fillna(0).astype(int)
    inbox_df["attachment_status"] = inbox_df.apply(_operations_attachment_status_for_row, axis=1)

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
        range_match = re.search(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:-|to)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
            text,
            re.I,
        )
        if range_match:
            return _safe_str(range_match.group(1))
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
    inbox_df["owner_label"] = inbox_df.apply(_operations_owner_label_for_row, axis=1)
    inbox_df["priority_label"] = inbox_df.apply(_operations_priority_label_for_row, axis=1)
    inbox_df["status_label"] = inbox_df.apply(_operations_status_label_for_row, axis=1)
    inbox_df["work_queue"] = inbox_df.apply(_operations_work_queue_for_row, axis=1)
    inbox_df["case_total_messages"] = pd.to_numeric(
        inbox_df.get("case_message_count", inbox_df.get("conversation_message_count", 1)),
        errors="coerce",
    ).fillna(inbox_df["conversation_message_count"]).fillna(1).astype(int)
    wait_started = pd.to_datetime(
        inbox_df["case_customer_wait_started_at"].fillna(inbox_df["case_department_wait_started_at"])
        if "case_customer_wait_started_at" in inbox_df.columns and "case_department_wait_started_at" in inbox_df.columns
        else pd.Series(pd.NaT, index=inbox_df.index),
        errors="coerce",
        utc=True,
    )
    now_utc = pd.Timestamp.now(tz="UTC")
    inbox_df["waiting_hours"] = ((now_utc - wait_started).dt.total_seconds() / 3600).fillna(0).round(1)
    inbox_df = _collapse_operations_inbox_to_cases(inbox_df)
    if not inbox_df.empty:
        inbox_df["control_level"] = inbox_df.apply(_operations_control_level_for_row, axis=1)
        inbox_df["department_lane"] = inbox_df.apply(_operations_department_lane_for_row, axis=1)
        inbox_df["work_item"] = inbox_df.apply(_operations_work_item_for_row, axis=1)
        inbox_df["recommended_action"] = inbox_df.apply(_operations_recommended_action_for_row, axis=1)
        inbox_df["control_reason"] = inbox_df.apply(_operations_control_reason_for_row, axis=1)
        inbox_df["confidence_label"] = inbox_df["confidence_score"].apply(_operations_confidence_label)
        identifier_hints = inbox_df.apply(_operations_identifier_hints_for_row, axis=1)
        inbox_df["booking_hint"] = identifier_hints.apply(lambda value: value.get("booking", "") if isinstance(value, dict) else "")
        inbox_df["container_hint"] = identifier_hints.apply(lambda value: value.get("container", "") if isinstance(value, dict) else "")
        inbox_df["customer_hint"] = identifier_hints.apply(lambda value: value.get("customer", "") if isinstance(value, dict) else "")
        inbox_df["warehouse_hint"] = identifier_hints.apply(lambda value: value.get("warehouse", "") if isinstance(value, dict) else "")
        inbox_df["port_hint"] = identifier_hints.apply(lambda value: value.get("port", "") if isinstance(value, dict) else "")
        inbox_df["delivery_hint"] = identifier_hints.apply(lambda value: value.get("delivery", "") if isinstance(value, dict) else "")
        inbox_df["load_hint"] = inbox_df["matched_load_id"].fillna("").astype(str).replace({"nan": "", "None": ""})

    st.markdown("#### Control Center Snapshot")
    level_counts = inbox_df["control_level"].value_counts() if "control_level" in inbox_df.columns else pd.Series(dtype=int)
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        _render_ops_metric_card("Operational Cases", int(level_counts.get("Level 1 - Operational Cases", 0)), "Level 1")
    with m2:
        _render_ops_metric_card("Business Work", int(level_counts.get("Level 2 - Business Communications", 0)), "Level 2")
    with m3:
        _render_ops_metric_card("Archive / No Action", int(level_counts.get("Level 3 - No Action / Archive", 0)), "Level 3")
    with m4:
        _render_ops_metric_card("Needs Review", int(level_counts.get("Needs Review", 0)), "Human decision")
    with m5:
        _render_ops_metric_card("Critical", int(inbox_df["priority_label"].eq("Critical").sum()), "Escalations")

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Action Required", int(inbox_df["work_queue"].eq("Action Required").sum()))
    k2.metric("New Orders", int(inbox_df["work_queue"].eq("New Orders").sum()))
    k3.metric("Existing Loads", int(inbox_df["work_queue"].eq("Existing Loads").sum()))
    k4.metric("Documents", int(inbox_df["work_queue"].eq("Documents").sum()))
    k5.metric("Waiting", int(inbox_df["work_queue"].eq("Waiting").sum()))
    k6.metric("Billing / Business", int(inbox_df["control_level"].eq("Level 2 - Business Communications").sum()))

    smart_group_result = st.session_state.pop("operations_smart_group_update_result", None)
    if smart_group_result is not None:
        if isinstance(smart_group_result, dict):
            st.success(
                f"Smart groups updated {int(smart_group_result.get('classified', 0))} item(s) "
                f"without opening new case records."
            )
        else:
            st.success(f"Smart groups updated {int(smart_group_result)} item(s).")

    c_update, c_note = st.columns([1, 4])
    with c_update:
        if st.button("Recheck Groups", key="operations_recheck_smart_groups", use_container_width=True):
            with st.spinner("Updating smart groups..."):
                full_inbox_df = _load_operations_inbox_record_set(where_clause)
                update_mask = _operations_items_needing_smart_group_update(full_inbox_df)
                classified_count = auto_classify_open_inbox_items(full_inbox_df[update_mask].copy())
                st.session_state["operations_smart_group_update_result"] = {
                    "classified": classified_count,
                    "cases": 0,
                }
                refresh_data()
                st.rerun()
    with c_note:
        st.caption("Routine inbox clicks stay fast. Use Recheck Groups when older messages need regrouping; it will not open Operations Cases.")

    with st.expander("Operations Inbox Process Feedback", expanded=False):
        st.markdown(
            """
- Level 1 is dispatcher work: new bookings, booking revisions, appointment changes, driver issues, port issues, PODs, documents, and customer load questions.
- Level 2 is important business communication: billing, insurance, legal, vendor, sales, HR, safety, and management work that should not distract dispatch.
- Level 3 is no-action or archive work: spam, marketing, newsletters, duplicates, FYI messages, and other mail that should stay searchable but not become a load.
- `Needs Review` is only for uncertainty. Once a dispatcher classifies a sender/topic, future messages should route faster.
- Select any row to open the case, timeline, parsed details, documents, reply tools, and order/quote actions.
            """.strip()
        )

    st.markdown("#### Control Filters")
    p1, p2 = st.columns([1, 2])
    with p1:
        perspective_filter = st.selectbox(
            "Perspective",
            ["Operations Control Center", "Dispatch", "Accounting", "Management", "Customer Service"],
            index=0,
            key="operations_case_perspective_filter",
        )
    with p2:
        manager_focus = st.selectbox(
            "Manager Focus",
            ["All Open Cases", "High Priority", "Escalated / Overdue", "Waiting >24 Hours", "Unassigned"],
            index=0,
            key="operations_manager_focus_filter",
            disabled=perspective_filter != "Management",
        )

    f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
    with f1:
        search_filter = st.text_input(
            "Search",
            value="",
            placeholder="Booking, container, customer, sender, subject, or reference",
            key="operations_inbox_search_filter",
        )
    with f2:
        owner_values = pd.concat(
            [
                inbox_df["owner_label"].dropna().astype(str),
                inbox_df["department_lane"].dropna().astype(str),
            ],
            ignore_index=True,
        )
        owner_options = ["All Owners"] + sorted(
            [value for value in owner_values.unique() if value]
        )
        owner_filter = st.selectbox(
            "Owner / Department",
            owner_options,
            index=0,
            key="operations_owner_queue_filter",
        )
    with f3:
        priority_options = ["All Priorities", "Critical", "High", "Medium", "Low"]
        priority_filter = st.selectbox(
            "Priority",
            priority_options,
            index=0,
            key="operations_priority_filter",
        )
    with f4:
        attachment_filter = st.selectbox(
            "Attachments",
            ["All Attachments", "Saved", "Mailbox", "Mentioned", "None"],
            index=0,
            key="operations_attachment_filter",
        )

    s1, s2 = st.columns(2)
    with s1:
        status_options = ["All Statuses"] + sorted(
            [value for value in inbox_df["status_label"].dropna().astype(str).unique() if value]
        )
        status_filter = st.selectbox(
            "Status",
            status_options,
            index=0,
            key="operations_status_filter",
        )
    with s2:
        type_options = ["All Request Types"] + sorted(
            [value for value in inbox_df["request_type_clean"].dropna().astype(str).unique() if value]
        )
        request_type_filter = st.selectbox(
            "Request Type",
            type_options,
            index=0,
            key="operations_request_type_filter",
        )

    filtered_df = inbox_df.copy()
    if perspective_filter == "Dispatch":
        filtered_df = filtered_df[
            filtered_df["control_level"].isin(["Level 1 - Operational Cases", "Needs Review"])
            & filtered_df["department_lane"].isin(["Dispatch", "Operations", "Customer Service", "Human Review"])
        ].copy()
    elif perspective_filter == "Accounting":
        filtered_df = filtered_df[
            filtered_df["department_lane"].eq("Accounting")
            | filtered_df["owner_label"].eq("Billing")
            | filtered_df["request_type_clean"].eq("Billing")
        ].copy()
    elif perspective_filter == "Customer Service":
        filtered_df = filtered_df[
            filtered_df["department_lane"].eq("Customer Service")
            | filtered_df["owner_label"].eq("Customer")
            | filtered_df["request_type_clean"].isin(["Customer Request", "Missing Information"])
        ].copy()
    elif perspective_filter == "Management":
        if manager_focus == "High Priority":
            filtered_df = filtered_df[filtered_df["priority_label"].isin(["Critical", "High"])].copy()
        elif manager_focus == "Escalated / Overdue":
            filtered_df = filtered_df[
                filtered_df["priority_label"].eq("Critical")
                | filtered_df["case_sla_status"].fillna("").astype(str).str.contains("Overdue|Warning", case=False, na=False)
            ].copy()
        elif manager_focus == "Waiting >24 Hours":
            filtered_df = filtered_df[filtered_df["waiting_hours"].ge(24)].copy()
        elif manager_focus == "Unassigned":
            filtered_df = filtered_df[
                filtered_df["owner_label"].isin(["", "Unassigned"])
                | filtered_df["case_owner"].fillna("").astype(str).isin(["", "Unassigned"])
            ].copy()

    search_filter = _safe_str(search_filter).lower()
    if search_filter:
        searchable_columns = [
            "id",
            "control_level",
            "department_lane",
            "work_queue",
            "work_item",
            "recommended_action",
            "client_name",
            "customer_hint",
            "booking_hint",
            "container_hint",
            "source_sender",
            "source_subject",
            "raw_text_preview",
            "reference_hint",
            "warehouse_hint",
            "port_hint",
            "delivery_hint",
            "conversation_key",
            "email_thread_id",
            "case_number",
            "matched_load_id",
            "action_required",
        ]
        available_search_columns = [column for column in searchable_columns if column in filtered_df.columns]
        search_blob = filtered_df[available_search_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        search_mask = pd.Series(True, index=filtered_df.index)
        for term in [part for part in re.split(r"\s+", search_filter) if part]:
            search_mask &= search_blob.str.contains(re.escape(term), na=False)
        filtered_df = filtered_df[search_mask].copy()
    if owner_filter != "All Owners":
        filtered_df = filtered_df[
            filtered_df["owner_label"].eq(owner_filter) | filtered_df["department_lane"].eq(owner_filter)
        ].copy()
    if priority_filter != "All Priorities":
        filtered_df = filtered_df[filtered_df["priority_label"].eq(priority_filter)].copy()
    if attachment_filter != "All Attachments":
        if attachment_filter == "None":
            filtered_df = filtered_df[filtered_df["attachment_status"].eq("None")].copy()
        else:
            filtered_df = filtered_df[filtered_df["attachment_status"].str.startswith(attachment_filter, na=False)].copy()
    if status_filter != "All Statuses":
        filtered_df = filtered_df[filtered_df["status_label"].eq(status_filter)].copy()
    if request_type_filter != "All Request Types":
        filtered_df = filtered_df[filtered_df["request_type_clean"].eq(request_type_filter)].copy()

    if filtered_df.empty:
        st.info("No Operations Inbox items match the current filters.")

    display_column_labels = {
        "id": "Request",
        "email_received": "Received",
        "department_lane": "Department",
        "work_queue": "Queue",
        "work_item": "Work Item",
        "recommended_action": "Next Action",
        "priority_label": "Priority",
        "status_label": "Status",
        "confidence_label": "AI Match",
        "customer_hint": "Customer",
        "booking_hint": "Booking",
        "container_hint": "Container",
        "reference_hint": "Reference",
        "warehouse_hint": "Warehouse",
        "port_hint": "Port",
        "delivery_hint": "Need / LFD",
        "attachment_status": "Files",
        "reply_status": "Thread",
        "case_total_messages": "Msgs",
        "case_number": "Case",
        "load_hint": "Load",
        "source_subject": "Subject",
        "action_required": "Action Detail",
        "control_reason": "Routing Reason",
    }
    shared_display_cols = [
        "id",
        "email_received",
        "department_lane",
        "work_queue",
        "work_item",
        "recommended_action",
        "priority_label",
        "status_label",
        "confidence_label",
        "customer_hint",
        "booking_hint",
        "container_hint",
        "reference_hint",
        "warehouse_hint",
        "port_hint",
        "delivery_hint",
        "attachment_status",
        "case_number",
        "load_hint",
        "source_subject",
        "action_required",
    ]

    level_titles = {
        level: f"{level} ({int(filtered_df['control_level'].eq(level).sum())})"
        for level in OPERATIONS_CONTROL_LEVELS
    }
    level_label_to_value = {label: level for level, label in level_titles.items()}
    selected_level_label = st.radio(
        "Work Level",
        list(level_label_to_value.keys()),
        horizontal=True,
        key="operations_active_work_level",
        label_visibility="collapsed",
    )
    selected_level = level_label_to_value[selected_level_label]
    level_df = filtered_df[filtered_df["control_level"].eq(selected_level)].copy()
    st.caption(OPERATIONS_CONTROL_LEVEL_DESCRIPTIONS.get(selected_level, ""))

    visible_review_ids: set[int] = set()
    selected_queue = ""

    if level_df.empty:
        st.info(f"No {selected_level.lower()} work items match the current filters.")
    else:
        queue_labels = _operations_queue_labels_for_level(selected_level, level_df)
        queue_titles = {
            label: f"{label} ({int(_operations_queue_mask_for_level(level_df, selected_level, label).sum())})"
            for label in queue_labels
        }
        queue_label_to_value = {title: label for label, title in queue_titles.items()}
        selected_queue_label = st.radio(
            "Queue",
            list(queue_label_to_value.keys()),
            horizontal=True,
            key=f"operations_active_queue_{re.sub(r'[^a-z0-9]+', '_', selected_level.lower()).strip('_')}",
            label_visibility="collapsed",
        )
        selected_queue = queue_label_to_value[selected_queue_label]
        tab_df = level_df[_operations_queue_mask_for_level(level_df, selected_level, selected_queue)].copy()
        visible_review_ids = set(tab_df["id"].dropna().astype(int).tolist()) if "id" in tab_df.columns else set()
        active_display_cols = [column for column in shared_display_cols if column in tab_df.columns]
        if selected_level != "Level 1 - Operational Cases":
            active_display_cols = [column for column in active_display_cols if column not in {"case_number", "load_hint"}]
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

        st.caption(f"{len(tab_df)} work item(s)")

        if tab_df.empty:
            st.info(f"No {selected_queue.lower()} work items.")
        else:
            display_df = tab_df[active_display_cols].rename(columns=display_column_labels)
            safe_level = re.sub(r"[^a-z0-9]+", "_", selected_level.lower()).strip("_")
            safe_queue = re.sub(r"[^a-z0-9]+", "_", selected_queue.lower()).strip("_")
            event = st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                selection_mode="single-row",
                on_select="rerun",
                key=f"operations_control_table_{safe_level}_{safe_queue}",
            )

            selected_rows = event.selection.rows
            if selected_rows:
                row_id = int(tab_df.iloc[selected_rows[0]]["id"])
                st.session_state["selected_operations_request_id"] = row_id
                st.session_state["selected_operations_tab"] = f"{selected_level} / {selected_queue}"

    st.divider()

    selected_id = st.session_state.get("selected_operations_request_id")
    selected_tab_name = st.session_state.get("selected_operations_tab")

    if selected_id is None:
        st.info("Select a work item row to review the email, routing, reply options, and available actions.")
        return

    if int(selected_id) not in visible_review_ids:
        st.info("Select a work item row in the current queue to review it.")
        return

    selected_tab_name = f"{selected_level} / {selected_queue}" if selected_queue else selected_level

    record_df = _load_operations_inbox_record(int(selected_id))

    if record_df.empty:
        st.warning("Selected request was not found.")
        return

    st.markdown(f"### Review Work Item - {selected_tab_name} - Request #{selected_id}")
    st.divider()

    record = record_df.iloc[0]
    parsed = _coerce_json_dict(record.get("parsed_data"))
    record["request_type_clean"] = _effective_operations_request_type_for_row(record)
    record["review_status_clean"] = _safe_str(record.get("review_status", "Open")) or "Open"
    record["owner_label"] = _operations_owner_label_for_row(record)
    record["priority_label"] = _operations_priority_label_for_row(record)
    record["status_label"] = _operations_status_label_for_row(record)
    record["work_queue"] = _operations_work_queue_for_row(record)
    record["control_level"] = _operations_control_level_for_row(record)
    record["department_lane"] = _operations_department_lane_for_row(record)
    record["work_item"] = _operations_work_item_for_row(record)
    record["recommended_action"] = _operations_recommended_action_for_row(record)

    subject = str(record.get("source_subject", "") or "")
    sender = str(record.get("source_sender", "") or "")
    body = str(record.get("raw_text", "") or "")
    body = extract_latest_email_body(body) or body
    try:
        body_reparsed = parse_email_text(subject, body, sender)
        parsed, parsed_changed = _merge_operations_body_parsed_fields(parsed, body_reparsed)
        if parsed_changed:
            _store_operations_parsed_data(
                int(selected_id),
                parsed,
                action_required=_order_action_required_from_parsed(parsed),
            )
    except Exception:
        pass

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

    can_open_operations_case = _operations_can_open_case(record, detected_type)
    operations_case = _load_operations_case_by_id(record.get("case_id")) if can_open_operations_case else {}
    if not can_open_operations_case:
        for record_field in [
            "case_id",
            "case_number",
            "case_status",
            "case_owner",
            "case_priority",
            "case_linked_load_id",
            "case_next_action",
            "case_sla_status",
            "case_first_response_due_at",
            "case_resolution_due_at",
        ]:
            record[record_field] = None
    if operations_case:
        for case_field, record_field in [
            ("id", "case_id"),
            ("case_number", "case_number"),
            ("status", "case_status"),
            ("owner", "case_owner"),
            ("priority", "case_priority"),
            ("linked_load_id", "case_linked_load_id"),
            ("next_action", "case_next_action"),
            ("sla_status", "case_sla_status"),
            ("first_response_due_at", "case_first_response_due_at"),
            ("resolution_due_at", "case_resolution_due_at"),
        ]:
            record[record_field] = operations_case.get(case_field)
        case_load_id = _int_or_none(operations_case.get("linked_load_id"))
        if matched_load_id is None and case_load_id is not None:
            matched_load_id = case_load_id
            classification["matched_load_id"] = matched_load_id
        viewed_key = f"operations_case_viewed_{operations_case.get('id')}_{selected_id}"
        if not st.session_state.get(viewed_key):
            _log_operations_case_event(
                operations_case.get("id"),
                "viewed",
                "Request viewed",
                f"Operations request #{selected_id} opened for review.",
                actor="dispatcher",
                department=_safe_str(operations_case.get("owner", "")),
            )
            st.session_state[viewed_key] = True

    load_context_key = f"operations_load_context_{selected_id}_{matched_load_id or 'none'}"
    cached_load_context = st.session_state.get(load_context_key) or {}
    load_context = cached_load_context.get("load_context", {})
    load_candidates = cached_load_context.get("load_candidates", [])

    st.caption("Selected email classification")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Work Level", _safe_str(record.get("control_level", "")).replace("Level ", "L") or "-")
    c2.metric("Department", _safe_str(record.get("department_lane", "")) or "-")
    c3.metric("Detected Type", detected_type)
    c4.metric("AI Match", _operations_confidence_label(confidence), f"{confidence}%")
    c5.metric("Matched Load", matched_load_id or "-")
    c6.metric("Case", _safe_str(operations_case.get("case_number", "")) if operations_case else "-")
    st.caption(f"Conversation: {conversation_key}")
    if classification.get("learning_applied"):
        st.caption(classification.get("learning_reason", "Routing used recent dispatcher learning."))

    if can_open_operations_case:
        if operations_case:
            _render_operations_case_summary_header(
                operations_case=operations_case,
                record=record,
                parsed=parsed,
                tokens=tokens,
                matched_load_id=matched_load_id,
            )
        else:
            st.info("No Operations Case is open for this order email yet. Open one only when this needs managed order follow-up.")
            if st.button("Open Operations Case", key=f"open_operations_case_{selected_id}", use_container_width=True):
                created_case = _sync_operations_case_for_intake_record(record)
                created_case_id = _int_or_none(created_case.get("id"))
                if created_case_id is not None:
                    _log_operations_case_event(
                        created_case_id,
                        "opened",
                        "Operations Case opened",
                        f"Dispatcher opened a case from Operations request #{selected_id}.",
                        actor="dispatcher",
                    )
                refresh_data()
                st.success("Operations Case opened for this order email.")
                st.rerun()
    else:
        st.info("This work item is for email review, routing, reply, and learning only. No Operations Case is opened for this level.")

    selected_conversation_key = _row_conversation_join_key(record)
    with st.expander("Email Synchronization Metadata", expanded=False):
        st.write(f"**Direction:** {_safe_str(record.get('email_direction', 'inbound')) or 'inbound'}")
        st.write(f"**Mailbox:** {_safe_str(record.get('email_mailbox', '')) or '-'}")
        st.write(f"**Message ID:** {_safe_str(record.get('source_message_id', '')) or '-'}")
        st.write(f"**Thread ID:** {_safe_str(record.get('email_thread_id', '')) or '-'}")
        st.write(f"**Conversation Key:** {selected_conversation_key or '-'}")
        st.write(f"**In Reply To:** {_safe_str(record.get('email_in_reply_to', '')) or '-'}")
        references = record.get("email_references")
        if isinstance(references, str):
            try:
                references = json.loads(references)
            except Exception:
                references = []
        st.write("**References:** " + (", ".join(references or []) if references else "-"))

    timeline_title = "Case Conversation Timeline" if operations_case else "Email Conversation Timeline"
    with st.expander(timeline_title, expanded=True):
        current_case_id = _int_or_none(operations_case.get("id"))
        if current_case_id is not None:
            timeline_df = _load_operations_case_timeline(current_case_id)
            if timeline_df.empty:
                st.info("No messages, notes, replies, or case events are linked yet.")
            else:
                timeline_df = timeline_df.copy()
                timeline_df["event_time"] = pd.to_datetime(
                    timeline_df["event_at"],
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")
                display_timeline = timeline_df[
                    [
                        "event_time",
                        "event_type",
                        "actor",
                        "title",
                        "details",
                    ]
                ].rename(
                    columns={
                        "event_time": "Time",
                        "event_type": "Type",
                        "actor": "Actor",
                        "title": "Title",
                        "details": "Preview / Notes",
                    }
                )
                latest_actor = _safe_str(timeline_df.iloc[-1].get("actor", ""))
                latest_type = _safe_str(timeline_df.iloc[-1].get("event_type", ""))
                t1, t2, t3 = st.columns(3)
                t1.metric("Messages / Events", len(timeline_df))
                t2.metric("Latest Actor", _case_customer_from_sender(latest_actor) if latest_actor else "-")
                t3.metric("Latest Event", latest_type or "-")
                st.dataframe(display_timeline, use_container_width=True, hide_index=True)
        else:
            timeline_df = _load_operations_conversation_timeline(selected_conversation_key)
            unfiltered_timeline_count = len(timeline_df)
            timeline_df = _filter_operations_timeline_for_record(timeline_df, record, tokens, subject, body)
            if timeline_df.empty:
                st.info("No additional messages found for this conversation yet.")
            else:
                timeline_df = timeline_df.copy()
                timeline_df["message_time"] = pd.to_datetime(
                    timeline_df["source_received_at"].fillna(timeline_df["created_at"]),
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d %I:%M %p").fillna("")
                display_timeline = timeline_df[
                    [
                        "message_time",
                        "email_direction",
                        "conversation_status",
                        "review_status",
                        "source_sender",
                        "source_subject",
                        "message_preview",
                    ]
                ].rename(
                    columns={
                        "message_time": "Time",
                        "email_direction": "Direction",
                        "conversation_status": "Thread Status",
                        "review_status": "Request Status",
                        "source_sender": "From",
                        "source_subject": "Subject",
                        "message_preview": "Preview",
                    }
                )
                latest_direction = _safe_str(timeline_df.iloc[-1].get("email_direction", "inbound")).title()
                latest_status = _safe_str(timeline_df.iloc[-1].get("conversation_status", "New Conversation"))
                t1, t2, t3 = st.columns(3)
                t1.metric("Messages", len(timeline_df))
                t2.metric("Latest Direction", latest_direction or "-")
                t3.metric("Thread Status", latest_status or "-")
                if unfiltered_timeline_count > len(timeline_df):
                    st.caption(f"Filtered {unfiltered_timeline_count - len(timeline_df)} broader thread message(s) that did not match this booking/reference/topic.")
                st.dataframe(display_timeline, use_container_width=True, hide_index=True)

    if operations_case:
        _render_operations_case_panel(
            selected_id=int(selected_id),
            operations_case=operations_case,
            matched_load_id=matched_load_id,
            show_timeline=False,
        )

    saved_request_type = str(record.get("request_type", "") or "").strip()
    effective_saved_request_type = _effective_operations_request_type_for_row(record)
    if effective_saved_request_type != saved_request_type and effective_saved_request_type in REQUEST_TYPES:
        saved_request_type = effective_saved_request_type
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
    reply_tone = st.selectbox(
        "Reply Tone",
        REPLY_TONE_OPTIONS,
        index=0,
        key=f"operations_reply_tone_{selected_id}",
    )
    resolved_reply_language = _resolve_reply_language(reply_language, subject, body)
    st.caption(f"Reply draft language: {resolved_reply_language}; tone: {reply_tone}")
    selected_action_required = _action_required_for_request(
        request_type,
        parsed,
        body,
        subject=subject,
        tokens=tokens,
        matched_load_id=matched_load_id,
    )

    with st.expander("Learning / Routing Notes", expanded=False):
        st.caption("Optional notes are saved with the classification so future routing can learn by sender and topic.")
        st.text_area(
            "Dispatcher Learning Notes",
            value="",
            placeholder="Example: this sender's PRE-ALERT emails are updates for existing loads, not new bookings.",
            height=80,
            key=f"operations_manual_learning_notes_{selected_id}",
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

    load_match_candidates = classification.get("load_match_candidates") or find_load_match_candidates(
        tokens,
        parsed=parsed,
        subject=subject,
        body=body,
        limit=5,
    )
    with st.expander("Load Match Suggestions", expanded=matched_load_id is None and bool(load_match_candidates)):
        if matched_load_id is not None:
            st.success(f"This request is linked to load {matched_load_id}.")
        if not load_match_candidates:
            st.info("No load match candidates found from booking, container, reference, customer/date, or vessel details.")
        else:
            st.dataframe(pd.DataFrame(load_match_candidates), use_container_width=True, hide_index=True)
            candidate_options = [None] + load_match_candidates

            def _load_match_label(option) -> str:
                if option is None:
                    return "Select load candidate"
                return (
                    f"Load {option.get('Load ID')} | {option.get('Match Score')}% | "
                    f"{option.get('Booking Number') or option.get('Container Number') or option.get('Reference Number') or '-'} | "
                    f"{option.get('Customer') or '-'}"
                )

            selected_candidate = st.selectbox(
                "Candidate Load",
                candidate_options,
                format_func=_load_match_label,
                key=f"operations_load_match_candidate_{selected_id}",
            )
            c_accept, c_reject = st.columns(2)
            with c_accept:
                if st.button(
                    "Accept Load Match",
                    key=f"operations_accept_load_match_{selected_id}",
                    use_container_width=True,
                    disabled=selected_candidate is None,
                ):
                    accepted_load_id = int(selected_candidate["Load ID"])
                    accepted_key = _conversation_key_from_candidate(selected_candidate, conversation_key)
                    update_intake_classification(
                        int(selected_id),
                        request_type,
                        accepted_key,
                        accepted_load_id,
                        int(selected_candidate.get("Match Score", confidence) or confidence),
                        f"Dispatcher accepted load match {accepted_load_id}: {selected_candidate.get('Match Reason', '')}",
                    )
                    current_case_id = _int_or_none(record.get("case_id")) or _int_or_none(operations_case.get("id"))
                    if current_case_id is not None:
                        _update_operations_case(
                            case_id=current_case_id,
                            status="Attached to Load",
                            owner=_safe_str(operations_case.get("owner", "Dispatch")) or "Dispatch",
                            priority=_safe_str(operations_case.get("priority", "Normal")) or "Normal",
                            linked_load_id=accepted_load_id,
                            next_action=f"Load match accepted for load {accepted_load_id}.",
                        )
                    refresh_data()
                    st.success(f"Accepted load match {accepted_load_id}.")
                    st.rerun()
            with c_reject:
                if st.button(
                    "Reject Suggested Match",
                    key=f"operations_reject_load_match_{selected_id}",
                    use_container_width=True,
                    disabled=matched_load_id is None and selected_candidate is None,
                ):
                    execute(
                        """
                        update order_intake
                        set matched_load_id = null,
                            confidence_score = least(confidence_score, 60),
                            action_required = 'Dispatcher rejected suggested load match; review manually.'
                        where id = :intake_id
                        """,
                        {"intake_id": int(selected_id)},
                    )
                    current_case_id = _int_or_none(record.get("case_id")) or _int_or_none(operations_case.get("id"))
                    if current_case_id is not None:
                        _add_operations_case_note(current_case_id, "Dispatcher rejected suggested load match; manual review needed.")
                    refresh_data()
                    st.warning("Suggested load match rejected.")
                    st.rerun()

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
                        reply_tone=reply_tone,
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
        manual_feedback_notes = _safe_str(st.session_state.get(f"operations_manual_learning_notes_{selected_id}", ""))
        ai_feedback_notes = _safe_str(st.session_state.get(f"operations_ai_feedback_notes_{selected_id}", ""))
        feedback_notes = ai_feedback_notes or manual_feedback_notes
        if ai_suggestion and ai_suggestion.get("success"):
            ai_request_type = _safe_str(ai_suggestion.get("request_type", ""))
            ai_action_required = _safe_str(ai_suggestion.get("action_required", ""))
            if request_type != ai_request_type:
                correction_type = "classification_corrected"
            elif selected_action_required != ai_action_required:
                correction_type = "action_corrected"
            else:
                correction_type = "classification_confirmed"
        else:
            if request_type != default_request_type:
                correction_type = "manual_classification_corrected"
            else:
                correction_type = "manual_classification_saved"

        _save_operations_ai_feedback(
            intake_id=int(selected_id),
            load_id=matched_load_id,
            source_subject=subject,
            source_sender=sender,
            ai_suggestion=ai_suggestion if ai_suggestion and ai_suggestion.get("success") else None,
            final_request_type=request_type,
            final_action_required=selected_action_required,
            correction_type=correction_type,
            feedback_notes=feedback_notes,
        )
        st.success("Classification saved.")
        refresh_data()
        st.rerun()

    st.markdown("### Email Action Center")
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
        reply_tone=reply_tone,
    )
    reply_key_seed = f"{request_type}_{resolved_reply_language}_{st.session_state.get(ai_version_key, 'rule')}"
    reply_key_suffix = re.sub(r"[^a-z0-9]+", "_", reply_key_seed.lower()).strip("_")
    reply_sender_options = _operations_reply_sender_options()
    suggested_reply_from = _suggested_operations_reply_sender(request_type, operations_case)
    if suggested_reply_from not in reply_sender_options:
        reply_sender_options.insert(0, suggested_reply_from)
    default_reply_from_index = reply_sender_options.index(suggested_reply_from) if suggested_reply_from in reply_sender_options else 0
    action_mode = st.selectbox(
        "Action",
        ["Reply to Customer", "Reply All", "Forward"],
        index=0,
        key=f"operations_action_mode_{selected_id}",
    )
    reply_from = st.selectbox(
        "Reply From",
        reply_sender_options,
        index=default_reply_from_index,
        key=f"operations_reply_from_{selected_id}",
    )
    action_key_suffix = re.sub(r"[^a-z0-9]+", "_", action_mode.lower()).strip("_")
    reply_from_key_suffix = re.sub(r"[^a-z0-9]+", "_", reply_from.lower()).strip("_")
    with st.form(f"operations_email_reply_{selected_id}"):
        default_reply_to = "" if action_mode == "Forward" else _extract_email_address(sender)
        reply_to = st.text_input(
            "To",
            value=default_reply_to,
            key=f"operations_reply_to_{selected_id}_{action_key_suffix}",
        )
        reply_cc_default = _reply_all_cc_from_record(parsed, reply_from, default_reply_to) if action_mode == "Reply All" else ""
        reply_cc = st.text_input(
            "CC",
            value=reply_cc_default,
            key=f"operations_reply_cc_{selected_id}_{action_key_suffix}_{reply_from_key_suffix}",
        )
        reply_subject = st.text_input(
            "Subject",
            value=_default_operations_action_subject(subject, request_type, action_mode),
            key=f"operations_reply_subject_{selected_id}_{action_key_suffix}",
        )
        reply_body = st.text_area(
            "Message",
            value=reply_body_default,
            height=220,
            key=f"operations_reply_body_{selected_id}_{reply_key_suffix}",
        )
        mark_waiting = st.checkbox(
            "Mark waiting on customer after sending",
            value=action_mode != "Forward",
            key=f"operations_reply_waiting_{selected_id}_{action_key_suffix}",
        )
        send_reply = st.form_submit_button("Send / Record Action")

    if send_reply:
        if not reply_to.strip():
            st.error("Reply recipient is required.")
        elif not reply_subject.strip() or not reply_body.strip():
            st.error("Subject and message are required.")
        else:
            current_case_id = _int_or_none(record.get("case_id")) or _int_or_none(operations_case.get("id"))
            try:
                _send_smtp_email(
                    reply_to.strip(),
                    reply_subject.strip(),
                    reply_body.strip(),
                    from_email=reply_from,
                    cc_email=reply_cc.strip(),
                )
                save_operations_email_reply(
                    intake_id=int(selected_id),
                    load_id=matched_load_id,
                    case_id=current_case_id,
                    recipient=reply_to.strip(),
                    subject=reply_subject.strip(),
                    body=reply_body.strip(),
                    status="sent",
                )
                _insert_operations_thread_reply_record(
                    intake_id=int(selected_id),
                    record=record,
                    reply_from=reply_from,
                    reply_to=reply_to.strip(),
                    reply_cc=reply_cc.strip(),
                    reply_subject=reply_subject.strip(),
                    reply_body=reply_body.strip(),
                    request_type=request_type,
                    conversation_key=selected_conversation_key or conversation_key,
                    matched_load_id=matched_load_id,
                    case_id=current_case_id,
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
                        reply_from,
                        reply_body.strip(),
                        direction="outbound",
                        case_id=current_case_id,
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
                    if current_case_id is not None:
                        _set_operations_case_status(
                            current_case_id,
                            "Waiting Customer",
                            "Reply sent; waiting on customer response.",
                        )

                st.success(f"Email sent from {reply_from} to {reply_to.strip()}.")
                refresh_data()
                st.rerun()
            except Exception as exc:
                try:
                    save_operations_email_reply(
                        intake_id=int(selected_id),
                        load_id=matched_load_id,
                        case_id=current_case_id,
                        recipient=reply_to.strip(),
                        subject=reply_subject.strip(),
                        body=reply_body.strip(),
                        status="failed",
                        error_message=str(exc),
                    )
                except Exception:
                    pass
                st.error(f"Email was not sent: {exc}")

    if not can_open_operations_case:
        st.markdown("### Routing Actions")
        action_record = record.copy()
        action_record["request_type_clean"] = request_type
        action_record["control_level"] = _operations_control_level_for_row(action_record)
        action_record["department_lane"] = _operations_department_lane_for_row(action_record)
        selected_control_level = _safe_str(action_record.get("control_level", ""))
        selected_department_lane = _safe_str(action_record.get("department_lane", ""))
        can_route_business = selected_control_level == "Level 2 - Business Communications" or request_type in BUSINESS_REQUEST_TYPES

        route_cols = st.columns(3)
        with route_cols[0]:
            if st.button("Route Business Email", use_container_width=True, disabled=not can_route_business):
                business_lane = selected_department_lane or "Management"
                business_request_type = "Billing" if business_lane == "Accounting" else "Business Communication"
                business_note = f"Business communication routed to {business_lane}."
                execute(
                    """
                    update order_intake
                    set review_status = 'Open',
                        request_type = :request_type,
                        action_required = :action_required,
                        conversation_key = :conversation_key
                    where id = :intake_id
                    """,
                    {
                        "intake_id": int(selected_id),
                        "request_type": business_request_type,
                        "action_required": business_note,
                        "conversation_key": conversation_key,
                    },
                )
                refresh_data()
                st.success(business_note)
                st.rerun()
        with route_cols[1]:
            close_label = "Archive / No Action" if selected_control_level == "Level 3 - No Action / Archive" else "Close / No Action"
            if st.button(close_label, use_container_width=True):
                execute(
                    """
                    update order_intake
                    set review_status = 'Closed',
                        request_type = case
                            when :control_level = 'Level 3 - No Action / Archive' then 'No Action / FYI'
                            else request_type
                        end,
                        action_required = case
                            when :control_level = 'Level 3 - No Action / Archive' then 'Archived as no-action / FYI email.'
                            else coalesce(nullif(action_required, ''), 'Closed from email review.')
                        end
                    where id = :intake_id
                    """,
                    {"intake_id": int(selected_id), "control_level": selected_control_level},
                )
                refresh_data()
                st.info("Request closed.")
                st.rerun()
        with route_cols[2]:
            if st.button("Keep for Review", use_container_width=True):
                execute(
                    """
                    update order_intake
                    set review_status = 'Open',
                        action_required = coalesce(nullif(action_required, ''), 'Dispatcher review required.')
                    where id = :intake_id
                    """,
                    {"intake_id": int(selected_id)},
                )
                refresh_data()
                st.success("Request kept in review.")
                st.rerun()
        return

    st.markdown("### Work Item Actions")
    st.caption("Create or update shipment work for Level 1, route business communication for Level 2, or close archive/no-action messages for Level 3.")

    current_case_id = _int_or_none(record.get("case_id")) or _int_or_none(operations_case.get("id"))
    message_text = f"{subject or ''} {body or ''}"
    order_identifier = (
        _safe_str(parsed.get("Booking Number", ""))
        or _safe_str(tokens.get("booking_number", ""))
        or _safe_str(parsed.get("Reference Number", ""))
        or _safe_str(tokens.get("reference_number", ""))
        or _safe_str(parsed.get("Container Number", ""))
        or _safe_str(tokens.get("container_number", ""))
    )
    order_customer = _safe_str(parsed.get("Customer", "")) or _safe_str(sender).split("<")[0].strip()
    parsed_has_new_order_details = _has_new_order_details(message_text, parsed, tokens)
    can_create_order = bool(order_identifier and order_customer) and (
        request_type == "New Booking" or parsed_has_new_order_details
    )
    can_create_quote = request_type == "Quote Request" and _has_quote_details(message_text, parsed, tokens)
    fill_order_blanks = st.checkbox(
        "When updating an existing order, fill blank order fields from parsed email/document data",
        value=True,
        key=f"operations_update_order_fill_blanks_{selected_id}",
    )

    action_record = record.copy()
    action_record["request_type_clean"] = request_type
    action_record["control_level"] = _operations_control_level_for_row(action_record)
    action_record["department_lane"] = _operations_department_lane_for_row(action_record)
    selected_control_level = _safe_str(action_record.get("control_level", ""))
    selected_department_lane = _safe_str(action_record.get("department_lane", ""))
    can_route_business = selected_control_level == "Level 2 - Business Communications" or request_type in BUSINESS_REQUEST_TYPES

    a1, a2, a3, a4, a5, a6 = st.columns(6)

    with a1:
        if st.button("Create Order / Load", use_container_width=True, disabled=not can_create_order):
            booking = _safe_str(parsed.get("Booking Number", "")) or _safe_str(tokens.get("booking_number", "")) or order_identifier
            customer = order_customer
            parsed_notes = _safe_str(parsed.get("Dispatcher Notes", ""))
            creation_notes = f"Created from Operations Inbox request #{selected_id}"
            if parsed_notes:
                creation_notes = f"{creation_notes}\n{parsed_notes}"

            if not order_identifier or not customer:
                st.error("Customer plus booking, reference, or container is required.")
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
                        "Warehouse": parsed.get("Warehouse", "") or parsed.get("Address", ""),
                        "Address": parsed.get("Address", ""),
                        "Document Cutoff": parsed.get("Document Cutoff", ""),
                        "Delivery Need Date": parsed.get("Delivery Need Date", ""),
                        "LFD": parsed.get("LFD", ""),
                        "Size": parsed.get("Size", ""),
                        "Status": "New",
                        "Dispatcher Notes": creation_notes,
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
                if current_case_id is not None:
                    _update_operations_case(
                        case_id=current_case_id,
                        status="Attached to Load",
                        owner="Dispatch",
                        priority=_safe_str(operations_case.get("priority", "Normal")) or "Normal",
                        linked_load_id=load_id,
                        next_action=f"New load {load_id} created from Operations Inbox.",
                    )
                    _add_operations_case_note(current_case_id, f"Created new load {load_id} from request #{selected_id}.")

                refresh_data()
                st.success(f"Created order/load ID {load_id}.")
                st.rerun()

    with a2:
        if st.button("Update Existing Order", use_container_width=True, disabled=matched_load_id is None):
            updates = _update_load_from_operations_pdf(
                int(matched_load_id),
                parsed,
                fill_blank_only=fill_order_blanks,
            )
            save_load_communication(
                matched_load_id,
                int(selected_id),
                conversation_key,
                request_type,
                subject,
                sender,
                body,
                case_id=current_case_id,
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
            if current_case_id is not None:
                next_action = f"Request attached to load {matched_load_id}."
                if updates:
                    next_action += " Updated fields: " + ", ".join(updates.keys())
                _update_operations_case(
                    case_id=current_case_id,
                    status="Attached to Load",
                    owner=_safe_str(operations_case.get("owner", "Dispatch")) or "Dispatch",
                    priority=_safe_str(operations_case.get("priority", "Normal")) or "Normal",
                    linked_load_id=matched_load_id,
                    next_action=next_action,
                )

            refresh_data()
            if updates:
                st.success("Request attached and updated fields: " + ", ".join(updates.keys()))
            else:
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
            if current_case_id is not None:
                _set_operations_case_status(current_case_id, "In Review", "Quote request created; waiting for pricing follow-up.")
                _add_operations_case_note(current_case_id, f"Quote request created from Operations Inbox request #{selected_id}.")

            refresh_data()
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
            if current_case_id is not None:
                _set_operations_case_status(current_case_id, "Closed", f"Load {matched_load_id} cancelled.")
                _add_operations_case_note(current_case_id, f"Cancelled matched load {matched_load_id}.")

            refresh_data()
            st.warning("Matched order was cancelled.")
            st.rerun()

    with a5:
        if st.button("Route Business Case", use_container_width=True, disabled=not can_route_business):
            business_lane = selected_department_lane or "Management"
            business_request_type = "Billing" if business_lane == "Accounting" else "Business Communication"
            business_owner = {
                "Accounting": "Billing",
                "Management": "Manager",
                "Sales": "Customer Service",
                "Safety": "Safety",
                "Customer Service": "Customer Service",
            }.get(business_lane, "Manager")
            business_note = f"Business communication routed to {business_lane}."

            execute(
                """
                update order_intake
                set review_status = 'Open',
                    request_type = :request_type,
                    action_required = :action_required,
                    conversation_key = :conversation_key
                where id = :intake_id
                """,
                {
                    "intake_id": int(selected_id),
                    "request_type": business_request_type,
                    "action_required": business_note,
                    "conversation_key": conversation_key,
                },
            )
            if current_case_id is not None:
                _update_operations_case(
                    case_id=current_case_id,
                    status="Waiting Billing" if business_owner == "Billing" else "In Review",
                    owner=business_owner,
                    priority=_safe_str(operations_case.get("priority", "Normal")) or "Normal",
                    linked_load_id=matched_load_id,
                    next_action=business_note,
                )
                _add_operations_case_note(current_case_id, business_note)

            refresh_data()
            st.success(business_note)
            st.rerun()

    with a6:
        close_label = "Close Spam" if request_type == "Spam/Marketing" else "Archive / No Action" if selected_control_level == "Level 3 - No Action / Archive" else "Close / No Action"
        if st.button(close_label, use_container_width=True):
            execute(
                """
                update order_intake
                set review_status = 'Closed',
                    request_type = case
                        when :request_type = 'Spam/Marketing' then 'Spam/Marketing'
                        when :control_level = 'Level 3 - No Action / Archive' then 'No Action / FYI'
                        else request_type
                    end,
                    action_required = case
                        when :request_type = 'Spam/Marketing' then 'Closed as spam / non-operational email.'
                        when :control_level = 'Level 3 - No Action / Archive' then 'Archived as no-action / FYI email.'
                        else action_required
                    end
                where id = :intake_id
                """,
                {"intake_id": int(selected_id), "request_type": request_type, "control_level": selected_control_level},
            )
            if current_case_id is not None:
                close_note = (
                    "Closed as spam / non-operational email."
                    if request_type == "Spam/Marketing"
                    else "Archived as no-action / FYI email."
                    if selected_control_level == "Level 3 - No Action / Archive"
                    else "Closed from Operations Inbox with no further action."
                )
                _set_operations_case_status(current_case_id, "Closed", close_note)

            refresh_data()
            st.info("Request closed.")
            st.rerun()  
def render_booking_review(df: pd.DataFrame) -> None:
    st.markdown("### Booking Review")
    st.caption("Complete missing booking information here. Verified bookings move to the load workspace for Port Sync / PIN.")

    review_statuses = ["New", "Hold/Need Info", "Booking Verified"]
    review_df = df[df["Status"].isin(review_statuses)].copy()
    review_df = _add_booking_verification_columns(review_df)

    if review_df.empty:
        st.success("No bookings require review.")
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Needs Review", int(review_df["Status"].eq("New").sum()))
    k2.metric("Missing Info", int(review_df["Status"].eq("Hold/Need Info").sum()))
    k3.metric("Booking Verified", int(review_df["Status"].eq("Booking Verified").sum()))
    k4.metric("Complete", int(review_df["Readiness %"].eq(100).sum()))

    q1, q2, q3, q4 = st.tabs(
        ["New Orders", "Missing Information", "Booking Verified", "All Review"]
    )

    with q1:
        _render_booking_verification_table(
            review_df[review_df["Status"].eq("New")].copy(),
            "New Orders",
        )

    with q2:
        missing_df = review_df[review_df["Readiness %"].lt(100)].copy()
        _render_booking_verification_table(missing_df, "Missing Information")

    with q3:
        _render_booking_verification_table(
            review_df[review_df["Status"].eq("Booking Verified")].copy(),
            "Booking Verified",
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
            review_status_options = list(ORDER_MANAGEMENT_STATUSES)
            current_review_status = _safe_str(selected_load.get("Status", "New"))
            if current_review_status and current_review_status not in review_status_options:
                review_status_options.insert(0, current_review_status)
            status = st.selectbox(
                "Review Status",
                review_status_options,
                index=review_status_options.index(current_review_status)
                if current_review_status in review_status_options
                else 0,
                format_func=lambda value: ORDER_MANAGEMENT_STATUS_LABELS.get(value, value),
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
        ready_disabled = readiness_score < 100
        if st.button(
            "Mark Booking Verified",
            key=f"review_booking_verified_{selected_row_id}",
            use_container_width=True,
            disabled=ready_disabled,
        ):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Booking Verified",
                    "Dispatcher Notes": "Booking completed and verified. Next action: verify booking with Port Houston.",
                },
            )
            refresh_data()
            st.success("Booking marked verified. Open the load workspace for Port Sync / PIN.")
            st.rerun()

    with a3:
        if st.button("Save Notes", key=f"review_save_notes_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {"Dispatcher Notes": notes.strip()},
            )
            refresh_data()
            st.success("Booking notes saved.")
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
        st.info("Mark Booking Verified is disabled until all required fields are complete.")

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


def _load_status_rank(status: str) -> int:
    status = _safe_str(status)
    try:
        return LOAD_STATUS_FLOW.index(status)
    except ValueError:
        return -1


def _status_at_or_after(status: str, milestone: str) -> bool:
    status_rank = _load_status_rank(status)
    milestone_rank = _load_status_rank(milestone)
    return status_rank >= milestone_rank >= 0


def _load_has_driver(load) -> bool:
    return bool(_first_present(load, ["Driver Name", "driver_name"], ""))


def _load_has_truck(load) -> bool:
    return bool(_first_present(load, ["Truck Assigned", "truck_assigned"], ""))


def _load_has_pin_or_appointment(load) -> bool:
    status = _first_present(load, ["Status", "status"], "")
    if _status_at_or_after(status, "PIN Received"):
        return True
    notes = _first_present(load, ["Dispatcher Notes", "dispatcher_notes"], "").lower()
    return bool(
        _first_present(
            load,
            [
                "pickup_appointment",
                "Pickup Appointment",
                "delivery_appointment",
                "Delivery Appointment",
                "pickup_reference",
                "Pickup Reference",
                "delivery_reference",
                "Delivery Reference",
            ],
            "",
        )
        or "pin received" in notes
        or "appointment confirmation" in notes
        or "express pass" in notes
    )


def _load_port_verified(load) -> bool:
    status = _first_present(load, ["Status", "status"], "")
    if _status_at_or_after(status, "Port Verified"):
        return True
    notes = _first_present(load, ["Dispatcher Notes", "dispatcher_notes"], "").lower()
    return bool(
        _first_present(load, ["terminal", "Terminal", "empty_return_location", "Empty Return Location", "current_location"], "")
        or "port houston evp update" in notes
        or "container lookup complete" in notes
        or "booking lookup complete" in notes
    )


def _load_document_count(load_id: int | None, documents_df: pd.DataFrame | None = None) -> int:
    if documents_df is not None:
        return len(documents_df)
    if load_id is None:
        return 0
    return len(_read_documents_for_load(int(load_id)))


def _load_readiness_details(load, documents_df: pd.DataFrame | None = None, include_documents: bool = True) -> dict:
    status = _first_present(load, ["Status", "status"], "New")
    move_type = _normalize_load_type(load)
    requires_port = _load_requires_port_type(move_type)
    load_id = _int_or_none(load.get("_row_id") if hasattr(load, "get") else None)
    has_docs = _load_document_count(load_id, documents_df) > 0 if include_documents else True
    has_driver = _load_has_driver(load)
    has_truck = _load_has_truck(load)
    has_pin = _load_has_pin_or_appointment(load)
    port_verified = _load_port_verified(load) if requires_port else True

    checks = [
        ("Customer", bool(_first_present(load, ["Customer", "customer"], ""))),
        ("Order / booking #", bool(_first_present(load, ["Booking Number", "booking_number", "Reference Number", "Load ID"], ""))),
        ("Container #", bool(_first_present(load, ["Container Number", "container_number"], ""))),
        ("Warehouse", bool(_first_present(load, ["Warehouse", "warehouse"], ""))),
        ("Delivery need date", bool(_first_present(load, ["Delivery Need Date", "delivery_need_date"], ""))),
        ("Size", bool(_first_present(load, ["Size", "size"], ""))),
        ("Import / Export / Local", bool(_first_present(load, ["TYPE", "type"], ""))),
    ]
    if requires_port:
        checks.extend(
            [
                ("Steamship line", bool(_first_present(load, ["steamship_line", "Steamship Line"], ""))),
                ("Port / terminal", bool(_first_present(load, ["Port", "port", "terminal", "Terminal"], ""))),
            ]
        )
    if include_documents:
        checks.append(("Documents attached", has_docs))
    if requires_port:
        checks.append(("Port verified", port_verified))
    checks.extend([("Driver assigned", has_driver), ("Truck assigned", has_truck)])
    if requires_port:
        checks.append(("PIN / appointment", has_pin))

    missing = [label for label, is_ready in checks if not is_ready]
    completed = len(checks) - len(missing)
    score = int(round((completed / len(checks)) * 100)) if checks else 0

    exceptions = []
    lfd_date = pd.to_datetime(_first_present(load, ["LFD", "lfd"], ""), errors="coerce")
    delivery_date = pd.to_datetime(_first_present(load, ["Delivery Need Date", "delivery_need_date"], ""), errors="coerce")
    today = pd.Timestamp(date.today()).normalize()
    if pd.notna(lfd_date) and lfd_date.normalize() <= today and status not in ["Delivered", "POD Received", "Ready for ProfitTools", "Invoiced", "Closed", "Cancelled"]:
        exceptions.append("LFD today")
    if pd.notna(delivery_date) and delivery_date.normalize() < today and status not in ["Delivered", "POD Received", "Ready for ProfitTools", "Invoiced", "Closed", "Cancelled"]:
        exceptions.append("Late appointment")
    if status in ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch", "Driver Assigned", "Assigned"] and not has_driver:
        exceptions.append("No driver assigned")
    if requires_port and status in ["Ready for Appointment / PIN", "Ready for Port PIN", "Ready to Dispatch", "Driver Assigned", "Assigned"] and not has_pin:
        exceptions.append("No PIN / appointment")
    if status in ["Delivered"] and not has_docs:
        exceptions.append("No POD")
    notes = _first_present(load, ["Dispatcher Notes", "dispatcher_notes"], "").lower()
    if requires_port and any(term in notes for term in ["hold", "customs hold", "line hold", "exam", "x-ray"]):
        exceptions.append("Container hold")

    verification_missing = [
        "Customer",
        "Order / booking #",
        "Container #",
        "Warehouse",
        "Delivery need date",
        "Size",
        "Import / Export / Local",
    ]
    if requires_port:
        verification_missing.extend(["Steamship line", "Port / terminal"])

    if any(item in missing for item in verification_missing):
        next_action = "Complete missing order details"
    elif "Documents attached" in missing:
        next_action = "Attach load documents"
    elif not _status_at_or_after(status, "Booking Verified"):
        next_action = "Verify booking"
    elif requires_port and not port_verified:
        next_action = "Verify booking with Port Houston"
    elif not has_driver or not has_truck:
        next_action = "Assign driver and truck"
    elif requires_port and not has_pin:
        next_action = "Request PIN / appointment"
    elif status in ["PIN Received", "Driver Assigned", "Assigned", "Ready to Dispatch"]:
        next_action = "Send dispatch packet"
    elif status == "Delivered":
        next_action = "Upload POD"
    elif status in ["POD Received", "Ready for ProfitTools"]:
        next_action = "Move to billing"
    else:
        next_action = _next_status_goal(status)

    return {
        "score": score,
        "missing": missing,
        "next_action": next_action,
        "exceptions": exceptions,
        "port_verified": port_verified,
        "pin_ready": has_pin,
        "requires_port": requires_port,
        "move_type": move_type,
        "dispatchable": not missing and port_verified and has_driver and has_truck and (has_pin or not requires_port),
    }


def _readiness_label(details: dict) -> str:
    missing = details.get("missing") or []
    if not missing:
        return f"{details.get('score', 0)}% Ready"
    return f"{details.get('score', 0)}% Ready - Missing: {', '.join(missing[:3])}{'...' if len(missing) > 3 else ''}"


def _load_department_queue(load) -> str:
    status = _first_present(load, ["Status", "status"], "New")
    readiness = _load_readiness_details(load, include_documents=False)
    exceptions = readiness.get("exceptions") or []
    if status in ["POD Received", "Ready for ProfitTools"]:
        return "Accounting - Ready for ProfitTools"
    if status in ["Invoiced", "Closed"]:
        return "Accounting - Closed / Invoiced"
    if any(term in _first_present(load, ["Dispatcher Notes", "dispatcher_notes"], "").lower() for term in ["detention", "demurrage", "invoice", "billing"]):
        return "Accounting - Detention / Demurrage"
    if not _load_has_driver(load) and status in ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch"]:
        return "Manager - Unassigned"
    if exceptions:
        return "Manager - Critical / Exceptions"
    if status in ["New", "Order Created", "Needs Review"]:
        return "Dispatcher - New Orders"
    if status in ["Hold/Need Info"]:
        return "Dispatcher - Need Info"
    if status in ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN"]:
        return "Dispatcher - Ready for PIN"
    if status in ["PIN Received", "Ready to Dispatch"]:
        return "Dispatcher - Ready to Dispatch"
    if status in ACTIVE_DRIVER_STATUSES or status in ["At Port", "Loaded / Picked Up", "Delivered"]:
        return "Dispatcher - Active Loads"
    return "Dispatcher - Exceptions"


def _load_exception_summary(df: pd.DataFrame) -> dict[str, int]:
    work_df = df.copy()
    if work_df.empty or "Status" not in work_df.columns:
        return {}
    open_df = work_df[~work_df["Status"].isin(CLOSED_STATUSES)].copy()
    open_df["Dispatch Move Type"] = open_df.get("TYPE", pd.Series("", index=open_df.index)).apply(_normalize_load_type_value)
    port_required = open_df["Dispatch Move Type"].isin(["Import", "Export"])
    today = pd.Timestamp(date.today()).normalize()
    lfd_dates = pd.to_datetime(open_df.get("LFD", ""), errors="coerce")
    delivery_dates = pd.to_datetime(open_df.get("Delivery Need Date", ""), errors="coerce")
    no_driver_statuses = ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch"]
    notes = open_df.get("Dispatcher Notes", pd.Series("", index=open_df.index)).fillna("").astype(str).str.lower()
    no_driver_mask = open_df["Status"].isin(no_driver_statuses) & open_df["Driver Name"].astype(str).str.strip().isin(["", "None", "nan", "Unassigned"])
    no_pin_mask = (
        port_required
        & open_df["Status"].isin(["Ready for Appointment / PIN", "Ready for Port PIN", "Ready to Dispatch", "Driver Assigned", "Assigned"])
        & ~open_df.apply(_load_has_pin_or_appointment, axis=1)
    )
    port_hold_mask = port_required & notes.str.contains("customs hold|line hold|x-ray|exam|hold", regex=True, na=False)
    return {
        "LFD today": int((lfd_dates.notna() & lfd_dates.dt.normalize().le(today) & ~open_df["Status"].isin(["Delivered", "POD Received", "Ready for ProfitTools"])).sum()),
        "Late appointment": int((delivery_dates.notna() & delivery_dates.dt.normalize().lt(today) & ~open_df["Status"].isin(["Delivered", "POD Received", "Ready for ProfitTools", "Invoiced", "Closed", "Cancelled"])).sum()),
        "No driver assigned": int(no_driver_mask.sum()),
        "Waiting driver": int(no_driver_mask.sum()),
        "No PIN": int(no_pin_mask.sum()),
        "Appointment missing": int((port_required & open_df["Status"].isin(["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN"]) & ~open_df.apply(_load_has_pin_or_appointment, axis=1)).sum()),
        "Customer waiting": int(open_df["Status"].eq("Hold/Need Info").sum()),
        "Container hold": int(port_hold_mask.sum()),
        "Port hold": int(port_hold_mask.sum()),
        "No POD": int(open_df["Status"].eq("Delivered").sum()),
        "Ready for billing": int(open_df["Status"].isin(["POD Received", "Ready for ProfitTools"]).sum()),
    }


def _dispatch_workflow_for_type(move_type: str) -> dict:
    normalized_type = _normalize_load_type_value(move_type)
    return DISPATCH_ACTION_WORKFLOWS.get(normalized_type, DISPATCH_ACTION_WORKFLOWS["Other"])


def _dispatch_action_labels(move_type: str) -> dict[str, tuple[str, str, int, int]]:
    labels = {}
    workflow = _dispatch_workflow_for_type(move_type)
    for lane_idx, (lane_name, actions) in enumerate(workflow.items()):
        for action_idx, (action_key, action_label) in enumerate(actions):
            labels[action_key] = (lane_name, action_label, lane_idx, action_idx)
    return labels


def _dispatch_action_metadata(load, readiness: dict | None = None) -> dict:
    status = _first_present(load, ["Status", "status"], "New")
    move_type = _normalize_load_type(load)
    requires_port = _load_requires_port_type(move_type)
    readiness = readiness or _load_readiness_details(load, include_documents=False)
    labels = _dispatch_action_labels(move_type)

    def choose(action_key: str, fallback_lane: str, hint: str) -> dict:
        if action_key not in labels:
            workflow = _dispatch_workflow_for_type(move_type)
            fallback_actions = workflow.get(fallback_lane) or next(iter(workflow.values()))
            action_key = fallback_actions[0][0]
        lane_name, action_label, lane_idx, action_idx = labels[action_key]
        return {
            "lane": lane_name,
            "action": action_key,
            "label": action_label,
            "hint": hint,
            "lane_sort": lane_idx,
            "action_sort": action_idx,
        }

    missing = set(readiness.get("missing") or [])
    verification_missing = missing - {"Documents attached", "Port verified", "Driver assigned", "Truck assigned", "PIN / appointment"}
    has_driver = _load_has_driver(load)
    has_truck = _load_has_truck(load)
    has_pin = _load_has_pin_or_appointment(load)
    port_verified = bool(readiness.get("port_verified", True))

    if status in ["Closed", "Invoiced", "Exported to ProfitTools"]:
        return choose("completed", "Completion", "Load complete or exported.")
    if status in ["Cancelled"]:
        return choose("completed", "Completion", "Cancelled load.")
    if status in ["POD Received", "Ready for ProfitTools"]:
        return choose("ready_billing", "Completion", "Move to billing / ProfitTools.")
    if status == "Delivered":
        return choose("delivered", "Completion", "Collect POD and clear billing handoff.")
    if status == "Returning Empty":
        return choose("empty_return", "Completion", "Confirm empty or chassis return.")

    if status in ["New Email", "Order Created", "New"]:
        return choose("new_orders", "Verification", "Review order and confirm core details.")
    if status in ["Needs Review", "Hold/Need Info"] or verification_missing:
        return choose("needs_verification", "Verification", "Fix missing or questionable load details.")
    if "Documents attached" in missing:
        return choose("documents", "Verification", "Attach or review required load documents.")

    if requires_port and not port_verified:
        return choose("sync_port", "Planning", "Sync Port Houston data before dispatch.")
    if not has_driver or not has_truck:
        return choose("assign_driver", "Planning", "Assign driver, truck, and chassis.")
    if requires_port and not has_pin:
        return choose("appointment_needed", "Planning", "Book appointment or request PIN before dispatch.")
    if move_type == "Export Local" and status in ["Booking Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch", "Driver Assigned", "Assigned"]:
        return choose("appointment_needed", "Planning", "Confirm empty pickup before sending the final packet.")
    if status in ["Booking Verified", "Port Verified", "Ready for Appointment / PIN", "Ready for Port PIN", "PIN Received", "Ready to Dispatch", "Driver Assigned", "Assigned"]:
        return choose("send_packet", "Planning", "Send dispatch packet when load is ready.")

    if status in ["Dispatched", "En Route to Pickup"]:
        return choose("enroute_pickup", "Execution", "Track movement toward pickup.")
    if status == "At Port":
        return choose("at_port", "Execution", "Track terminal activity.")
    if status == "At Pickup":
        return choose("at_pickup", "Execution", "Track pickup / customer arrival.")
    if status in ["Loaded / Picked Up", "Loaded"]:
        return choose("loaded", "Execution", "Track load after pickup.")
    if status == "En Route To Delivery":
        return choose("enroute_delivery", "Execution", "Track delivery ETA.")

    return choose("needs_verification", "Verification", "Review load status and next step.")


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
    return get_secret(name, default)


def _get_first_app_setting(names: list[str], default=None):
    for name in names:
        value = _get_app_setting(name)
        if value not in [None, ""]:
            return value
    return default


OPERATIONS_REPLY_MAILBOXES = [
    "dispatch@calitranscorp.com",
    "margiea@calitranscorp.com",
    "accounting@calitranscorp.com",
]

NAMED_EMAIL_ACCOUNTS = {
    "DISPATCH": "dispatch@calitranscorp.com",
    "MARGIE": "margiea@calitranscorp.com",
    "ACCOUNTING": "accounting@calitranscorp.com",
}


def _setting_suffix_for_email(email_address: str) -> str:
    local_part = _safe_str(email_address).split("@", 1)[0]
    return re.sub(r"[^A-Za-z0-9]+", "_", local_part).strip("_").upper()


def _unique_setting_names(names: list[str]) -> list[str]:
    result = []
    seen = set()
    for name in names:
        name = _safe_str(name)
        normalized = name.upper()
        if not name or normalized in seen:
            continue
        seen.add(normalized)
        result.append(name)
    return result


def _email_account_aliases(email_address: str) -> list[str]:
    normalized_email = _safe_str(email_address).lower()
    aliases = [_setting_suffix_for_email(email_address)]
    for alias, default_email in NAMED_EMAIL_ACCOUNTS.items():
        configured_email = _get_first_app_setting(
            [f"{alias}_YAHOO_EMAIL", f"{alias}_EMAIL", f"YAHOO_EMAIL_{alias}"],
            default_email,
        )
        if normalized_email == _safe_str(configured_email).lower():
            aliases.append(alias)
            if alias == "MARGIE":
                aliases.append("MARGIEA")
    return _unique_setting_names(aliases)


def _setting_candidates_for_aliases(aliases: list[str], templates: list[str]) -> list[str]:
    return _unique_setting_names([template.format(alias=alias) for alias in aliases for template in templates])


def _split_email_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def _operations_reply_sender_options() -> list[str]:
    configured = _split_email_list(
        _get_first_app_setting(
            ["OPERATIONS_REPLY_FROM_ADDRESSES", "OPERATIONS_CASE_MAILBOXES", "OPERATIONS_EMAIL_ACCOUNTS"],
            ",".join(OPERATIONS_REPLY_MAILBOXES),
        )
    )
    for alias, default_email in NAMED_EMAIL_ACCOUNTS.items():
        configured_email = _get_first_app_setting(
            [f"{alias}_YAHOO_EMAIL", f"{alias}_EMAIL", f"YAHOO_EMAIL_{alias}"],
            default_email,
        )
        if configured_email not in configured:
            configured.append(configured_email)
    for email_address in OPERATIONS_REPLY_MAILBOXES:
        if email_address not in configured:
            configured.append(email_address)

    seen = set()
    options = []
    for email_address in configured:
        normalized = email_address.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        options.append(email_address)
    return options


def _suggested_operations_reply_sender(request_type: str, operations_case: dict) -> str:
    request_type = _safe_str(request_type)
    owner = _safe_str(operations_case.get("owner", "") if isinstance(operations_case, dict) else "").lower()
    status = _safe_str(operations_case.get("status", "") if isinstance(operations_case, dict) else "").lower()
    if request_type == "Billing" or owner == "billing":
        return "accounting@calitranscorp.com"
    if "manager" in owner or "manager" in status or _safe_str(operations_case.get("priority", "")).lower() in {"critical", "urgent"}:
        return "margiea@calitranscorp.com"
    return "dispatch@calitranscorp.com"


def _split_email_addresses(value: str) -> list[str]:
    addresses = []
    for _, address in getaddresses([str(value or "").replace(";", ",")]):
        clean_address = _safe_str(address)
        if clean_address:
            addresses.append(clean_address)
    return addresses


def _reply_all_cc_from_record(parsed: dict, reply_from: str, reply_to: str) -> str:
    sync_meta = parsed.get("_email_sync", {}) if isinstance(parsed, dict) else {}
    candidates = []
    candidates.extend(_split_email_addresses(sync_meta.get("to", "")))
    candidates.extend(_split_email_addresses(sync_meta.get("cc", "")))
    excluded = {
        _safe_str(reply_from).lower(),
        _safe_str(reply_to).lower(),
    }
    cleaned = []
    seen = set()
    for address in candidates:
        normalized = address.lower()
        if not normalized or normalized in excluded or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(address)
    return ", ".join(cleaned)


def _smtp_credentials_for_sender(from_email: str) -> tuple[str, str, str]:
    smtp_user_default = _get_first_app_setting(["SMTP_USER", "DISPATCH_YAHOO_EMAIL", "YAHOO_EMAIL", "EMAIL_ADDRESS"])
    smtp_password_default = _get_first_app_setting(
        ["SMTP_PASSWORD", "DISPATCH_YAHOO_APP_PASSWORD", "YAHOO_APP_PASSWORD", "EMAIL_APP_PASSWORD"]
    )
    from_email = _safe_str(from_email) or _get_first_app_setting(["DISPATCH_EMAIL", "YAHOO_EMAIL", "EMAIL_ADDRESS"], smtp_user_default)
    aliases = _email_account_aliases(from_email)
    smtp_user = _get_first_app_setting(
        _setting_candidates_for_aliases(
            aliases,
            [
                "{alias}_SMTP_USER",
                "{alias}_YAHOO_EMAIL",
                "{alias}_EMAIL_ADDRESS",
                "SMTP_USER_{alias}",
                "YAHOO_EMAIL_{alias}",
                "EMAIL_ADDRESS_{alias}",
            ],
        ),
        from_email or smtp_user_default,
    )
    smtp_password = _get_first_app_setting(
        _setting_candidates_for_aliases(
            aliases,
            [
                "{alias}_SMTP_PASSWORD",
                "{alias}_YAHOO_APP_PASSWORD",
                "{alias}_EMAIL_APP_PASSWORD",
                "SMTP_PASSWORD_{alias}",
                "YAHOO_APP_PASSWORD_{alias}",
                "EMAIL_APP_PASSWORD_{alias}",
            ],
        ),
        smtp_password_default if _safe_str(smtp_user).lower() == _safe_str(smtp_user_default).lower() else None,
    )
    return from_email, smtp_user, smtp_password


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


def _normalize_load_type_value(value: str) -> str:
    text = _safe_str(value)
    lower = re.sub(r"\s+", " ", text.replace("_", " ").replace("-", " ")).strip().lower()
    if not lower:
        return "Other"
    is_local = "local" in lower
    if "export" in lower:
        return "Export Local" if is_local else "Export"
    if "import" in lower:
        return "Import Local" if is_local else "Import"
    for load_type in LOAD_TYPE_TABS:
        if lower == load_type.lower():
            return load_type
    return "Other"


def _normalize_load_type(load) -> str:
    return _normalize_load_type_value(_first_present(load, ["TYPE", "type", "Load Type", "load_type"], ""))


def _load_requires_port_type(move_type: str) -> bool:
    return _normalize_load_type_value(move_type) in {"Import", "Export"}


def _load_requires_port(load) -> bool:
    return _load_requires_port_type(_normalize_load_type(load))


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


def _load_pin_display(load) -> str:
    return _first_present(
        load,
        [
            "pickup_reference",
            "Pickup Reference",
            "delivery_reference",
            "Delivery Reference",
            "pickup_appointment",
            "Pickup Appointment",
            "delivery_appointment",
            "Delivery Appointment",
        ],
        "-",
    )


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


def _send_smtp_email(to_email: str, subject: str, body: str, from_email: str = "", cc_email: str = "") -> None:
    smtp_host = _get_app_setting("SMTP_HOST", "smtp.mail.yahoo.com")
    smtp_port = int(_get_app_setting("SMTP_PORT", 465))
    dispatch_email, smtp_user, smtp_password = _smtp_credentials_for_sender(from_email)
    to_recipients = _split_email_addresses(to_email)
    cc_recipients = _split_email_addresses(cc_email)

    if not to_recipients:
        raise ValueError("Missing customer email address on this load.")
    if not smtp_host or not smtp_user or not smtp_password:
        raise ValueError("Missing email settings. Add YAHOO_EMAIL and YAHOO_APP_PASSWORD, or SMTP_HOST, SMTP_USER, and SMTP_PASSWORD.")

    msg = MIMEMultipart()
    msg["From"] = dispatch_email
    msg["To"] = ", ".join(to_recipients)
    if cc_recipients:
        msg["Cc"] = ", ".join(cc_recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    recipients = []
    seen = set()
    for address in to_recipients + cc_recipients:
        normalized = address.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        recipients.append(address)
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(dispatch_email, recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(dispatch_email, recipients, msg.as_string())

def _clean_display_value(value, fallback: str = "-") -> str:
    value_str = str(value or "").strip()
    if value_str.lower() in {"nan", "none", "nat", ""}:
        return fallback
    return value_str


def _generate_driver_dispatch_message(selected_load) -> str:
    booking = _clean_display_value(selected_load.get("Booking Number", ""))
    container = _clean_display_value(selected_load.get("Container Number", ""))
    customer = _clean_display_value(selected_load.get("Customer", ""))
    pickup = _clean_display_value(selected_load.get("Port", "") or selected_load.get("terminal", ""))
    terminal = _clean_display_value(selected_load.get("terminal", "") or selected_load.get("Port", ""))
    delivery = _clean_display_value(selected_load.get("Warehouse", ""))
    address = _clean_display_value(selected_load.get("Address", ""))
    delivery_need = _clean_display_value(selected_load.get("Delivery Need Date", ""))
    lfd = _clean_display_value(selected_load.get("LFD", ""))
    chassis = _clean_display_value(selected_load.get("Chassis", ""))
    chassis_provider = _clean_display_value(selected_load.get("chassis_provider", ""), "")
    size = _clean_display_value(selected_load.get("Size", ""))
    pickup_appt = _clean_display_value(selected_load.get("pickup_appointment", ""), "")
    delivery_appt = _clean_display_value(selected_load.get("delivery_appointment", ""), "")
    empty_return = _clean_display_value(selected_load.get("empty_return_location", ""), "")
    empty_return_date = _clean_display_value(selected_load.get("empty_return_date", ""), "")
    pin_or_appt = _load_pin_display(selected_load)
    notes = _clean_display_value(selected_load.get("Dispatcher Notes", ""), "")

    message = f"""LOAD ASSIGNMENT

Booking: {booking}
Container: {container}
Customer: {customer}
Size: {size}

TERMINAL / PICKUP
Terminal: {terminal}
Pickup Location: {pickup}
PIN / Appointment: {pin_or_appt}
Pickup Appointment: {pickup_appt or "-"}

DELIVERY
Warehouse: {delivery}
Address: {address}
Delivery Appointment: {delivery_appt or "-"}

Delivery Need Date: {delivery_need}
LFD: {lfd}
Chassis: {chassis}
Chassis Provider: {chassis_provider or "-"}
Empty Return: {empty_return or "-"}
Empty Return Date: {empty_return_date or "-"}

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
    load_documents_df = _read_documents_for_load(load_id)
    readiness = _load_readiness_details(selected_load, documents_df=load_documents_df)

    st.markdown("---")
    st.markdown(f"## Load Workspace: {booking}")
    st.caption(f"{customer} · Container {container}")

    top = st.columns(6)
    top[0].metric("Status", str(selected_load.get("Status", "") or "-"))
    top[1].metric("Readiness", f"{readiness['score']}%")
    top[2].metric("Next Action", readiness["next_action"])
    top[3].metric("Driver", str(selected_load.get("Driver Name", "") or "Unassigned"))
    top[4].metric("Truck", str(selected_load.get("Truck Assigned", "") or "-"))
    top[5].metric("LFD", str(selected_load.get("LFD", "") or "-"))

    if readiness["missing"]:
        st.warning("Missing before dispatch: " + ", ".join(readiness["missing"]))
    else:
        st.success("Load readiness checklist is complete.")
    if readiness["exceptions"]:
        st.error("Exceptions: " + ", ".join(readiness["exceptions"]))

    dispatch_tab, port_tab, status_tab, timeline_tab, driver_tab, customer_tab, docs_tab, billing_tab = st.tabs(
        ["Dispatch Details", "Port Sync / PIN", "Status Update", "Timeline", "Driver Notes/Text", "Customer Notes", "Documents", "Billing"]
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

    with port_tab:
        _render_load_port_houston_panel(selected_load, readiness)

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
            if (
                new_status in ["Ready to Dispatch", "Dispatched"]
                and new_status != current_status
                and not readiness.get("dispatchable")
            ):
                st.error("This load cannot be marked Ready to Dispatch or Dispatched until order details, port verification, driver, truck, and PIN/appointment are complete.")
                return
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
        packet_ready = bool(readiness.get("dispatchable"))
        if not packet_ready:
            st.warning("Driver packet is locked until customer/order, port verification, driver, truck, and PIN/appointment are complete.")

        edited_message = st.text_area(
            "Dispatch Message",
            value=generated_message,
            height=260,
            key=f"generated_dispatch_msg_{load_id}",
        )

        action_cols = st.columns(4)

        with action_cols[0]:
            if st.button(
                "Save Message",
                key=f"save_generated_driver_msg_{load_id}",
                use_container_width=True,
                disabled=not packet_ready,
            ):
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
                disabled=not packet_ready,
            )

        with action_cols[2]:
            if st.button(
                "Copy/Paste Ready",
                key=f"copy_ready_{load_id}",
                use_container_width=True,
                disabled=not packet_ready,
            ):
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

        move_type = _normalize_load_type(selected_load)
        if move_type == "Import":
            quick_statuses = [
                ("En Route to Pickup", "Driver en route to port/terminal."),
                ("At Port", "Driver arrived at terminal."),
                ("Loaded", "Container picked up and loaded."),
                ("En Route To Delivery", "Driver en route to warehouse/customer."),
                ("Delivered", "Delivery completed. Awaiting POD if not received."),
                ("Returning Empty", "Driver returning empty container/chassis."),
                ("POD Received", "POD received and saved for billing."),
            ]
        elif move_type == "Export":
            quick_statuses = [
                ("En Route to Pickup", "Driver en route to empty yard or shipper."),
                ("At Pickup", "Driver arrived at empty yard or shipper."),
                ("Loaded", "Export load picked up and loaded."),
                ("En Route To Delivery", "Driver en route to port."),
                ("At Port", "Driver arrived at terminal."),
                ("Delivered", "Export delivered to port. Awaiting POD if not received."),
                ("POD Received", "POD received and saved for billing."),
            ]
        else:
            quick_statuses = [
                ("En Route to Pickup", "Driver en route to pickup."),
                ("At Pickup", "Driver arrived at pickup."),
                ("Loaded", "Load picked up and loaded."),
                ("En Route To Delivery", "Driver en route to delivery."),
                ("Delivered", "Delivery completed. Awaiting POD if not received."),
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


def _render_dispatch_action_card(row, action_label: str, card_key_prefix: str) -> None:
    row_id = _int_or_none(row.get("_row_id")) or 0
    status = _clean_display_value(row.get("Status", ""), "New")
    move_type = _clean_display_value(row.get("Dispatch Move Type", ""), _normalize_load_type(row))
    booking = _clean_display_value(row.get("Booking Number", ""), "-")
    load_ref = _clean_display_value(row.get("Load ID", ""), "-")
    container = _clean_display_value(row.get("Container Number", ""), "-")
    customer = _clean_display_value(row.get("Customer", ""), "-")
    pickup = _clean_display_value(row.get("Port", ""), "Pickup pending")
    delivery = _clean_display_value(row.get("Warehouse", ""), "Delivery pending")
    driver = _clean_display_value(row.get("Driver Name", ""), "Unassigned")
    truck = _clean_display_value(row.get("Truck Assigned", ""), "-")
    need_date = _clean_display_value(row.get("Delivery Need Date", ""), "-")
    lfd = _clean_display_value(row.get("LFD", ""), "-")
    readiness = int(row.get("Readiness %", 0) or 0)
    next_action = _clean_display_value(row.get("Next Action", ""), action_label)
    exceptions = [item.strip() for item in _safe_str(row.get("Exceptions", "")).split(",") if item.strip()]

    status_color = _get_status_color(status)
    border_color = _get_status_border_color(status)
    exception_html = "".join(
        f'<span style="display:inline-block;background:#fee2e2;color:#991b1b;border:1px solid #fecaca;border-radius:999px;padding:2px 6px;margin:2px 4px 0 0;font-size:10px;font-weight:700;">{escape(item)}</span>'
        for item in exceptions[:3]
    )
    if len(exceptions) > 3:
        exception_html += f'<span style="font-size:10px;color:#991b1b;">+{len(exceptions) - 3} more</span>'

    st.markdown(
        f"""
        <div style="
            background:{status_color};
            border:1px solid #cbd5e1;
            border-left:5px solid {border_color};
            border-radius:8px;
            padding:8px;
            margin-bottom:6px;
            min-height:178px;
            box-shadow:0 1px 2px rgba(15,23,42,.06);
        ">
            <div style="font-size:10px;font-weight:800;color:#334155;text-transform:uppercase;">{escape(action_label)}</div>
            <div style="font-size:13px;font-weight:800;color:#0f172a;line-height:1.2;margin-top:3px;">{escape(booking)}</div>
            <div style="font-size:11px;color:#334155;line-height:1.25;">{escape(container)} | {escape(customer)}</div>
            <div style="font-size:10px;color:#475569;margin-top:5px;">{escape(move_type)} | {escape(status)}</div>
            <div style="height:6px;background:#e2e8f0;border-radius:999px;overflow:hidden;margin:7px 0 4px 0;">
                <div style="height:6px;width:{max(0, min(readiness, 100))}%;background:{border_color};"></div>
            </div>
            <div style="font-size:10px;color:#475569;">{readiness}% ready | {escape(next_action)}</div>
            <div style="font-size:10px;color:#475569;margin-top:6px;">
                <b>From:</b> {escape(pickup)}<br>
                <b>To:</b> {escape(delivery)}
            </div>
            <div style="font-size:10px;color:#475569;margin-top:6px;">
                <b>Driver:</b> {escape(driver)} | <b>Truck:</b> {escape(truck)}<br>
                <b>Need:</b> {escape(need_date)} | <b>LFD:</b> {escape(lfd)}
            </div>
            <div style="margin-top:5px;">{exception_html}</div>
            <div style="font-size:9px;color:#64748b;margin-top:5px;">Load {escape(load_ref)} | Row {row_id}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Work Load", key=f"dispatch_card_{card_key_prefix}_{row_id}", use_container_width=True):
        st.session_state["dispatch_board_selected_row_id"] = row_id
        st.rerun()


def render_dispatch_board_focused(df: pd.DataFrame) -> None:
    st.subheader("Dispatch Board")
    st.caption("Action board by move type. Port/PIN work appears only for port imports and exports.")

    if df.empty:
        st.info("No load data is available for Dispatch Board.")
        return

    board_df = df.copy()
    board_df["Status"] = board_df.get("Status", pd.Series("New", index=board_df.index)).fillna("New").astype(str).str.strip()
    board_df["TYPE"] = board_df.get("TYPE", pd.Series("", index=board_df.index)).fillna("").astype(str).str.strip()
    board_df["Dispatch Move Type"] = board_df["TYPE"].apply(_normalize_load_type_value)
    board_df["Delivery Date Parsed"] = pd.to_datetime(
        board_df.get("Delivery Need Date", pd.Series("", index=board_df.index)).astype(str).str.strip(),
        errors="coerce",
    )
    board_df["LFD Parsed"] = pd.to_datetime(
        board_df.get("LFD", pd.Series("", index=board_df.index)).astype(str).str.strip(),
        errors="coerce",
    )

    readiness_rows = []
    action_rows = []
    for _, row in board_df.iterrows():
        readiness = _load_readiness_details(row, include_documents=False)
        action = _dispatch_action_metadata(row, readiness)
        readiness_rows.append(readiness)
        action_rows.append(action)

    board_df["Readiness %"] = [int(item.get("score", 0)) for item in readiness_rows]
    board_df["Next Action"] = [item.get("next_action", "") for item in readiness_rows]
    board_df["Exceptions"] = [", ".join(item.get("exceptions", [])) for item in readiness_rows]
    board_df["Dispatch Lane"] = [item.get("lane", "") for item in action_rows]
    board_df["Dispatch Action"] = [item.get("action", "") for item in action_rows]
    board_df["Dispatch Action Label"] = [item.get("label", "") for item in action_rows]
    board_df["Dispatch Hint"] = [item.get("hint", "") for item in action_rows]
    board_df["Dispatch Lane Sort"] = [int(item.get("lane_sort", 0)) for item in action_rows]
    board_df["Dispatch Action Sort"] = [int(item.get("action_sort", 0)) for item in action_rows]
    board_df["Exception Count"] = board_df["Exceptions"].apply(lambda value: len([item for item in _safe_str(value).split(",") if item.strip()]))

    today = pd.Timestamp(date.today()).normalize()
    tomorrow = today + pd.Timedelta(days=1)

    controls = st.columns([1.3, 1.3, 1, 2.4])
    with controls[0]:
        selected_scope = st.radio(
            "Board Scope",
            ["All Active", "Due Today / Late", "Tomorrow", "Future Pipeline"],
            horizontal=False,
            key="dispatch_board_scope",
        )
    type_counts = board_df["Dispatch Move Type"].value_counts().to_dict()
    type_options = [move_type for move_type in DISPATCH_MOVE_TYPES if move_type == "Other" or type_counts.get(move_type, 0) > 0]
    if not type_options:
        type_options = DISPATCH_MOVE_TYPES
    with controls[1]:
        type_key = f"dispatch_board_move_type_{selected_scope}"
        if st.session_state.get(type_key) not in [None, *type_options]:
            st.session_state[type_key] = type_options[0]
        selected_type = st.radio(
            "Move Type",
            type_options,
            horizontal=False,
            key=type_key,
            format_func=lambda value: f"{value} ({type_counts.get(value, 0)})",
        )
    with controls[2]:
        exception_only = st.checkbox("Exceptions only", value=False, key="dispatch_board_exception_only")
    with controls[3]:
        search_filter = st.text_input(
            "Search",
            value="",
            placeholder="Booking, load, container, customer, driver, truck, port, warehouse",
            key="dispatch_board_search",
        )

    scope_df = board_df[~board_df["Status"].isin(CLOSED_STATUSES)].copy()
    if selected_scope == "Due Today / Late":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().le(today)
        ].copy()
    elif selected_scope == "Tomorrow":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().eq(tomorrow)
        ].copy()
    elif selected_scope == "Future Pipeline":
        scope_df = scope_df[
            scope_df["Delivery Date Parsed"].notna()
            & scope_df["Delivery Date Parsed"].dt.normalize().gt(tomorrow)
        ].copy()

    scope_df = scope_df[scope_df["Dispatch Move Type"].eq(selected_type)].copy()

    if exception_only:
        scope_df = scope_df[scope_df["Exception Count"].gt(0)].copy()

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
            "Dispatch Action Label",
            "Next Action",
            "Dispatcher Notes",
        ]
        available_columns = [column for column in searchable_columns if column in scope_df.columns]
        search_blob = scope_df[available_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        for term in [part for part in re.split(r"\s+", search_filter) if part]:
            mask = search_blob.str.contains(re.escape(term), na=False)
            scope_df = scope_df[mask].copy()
            search_blob = search_blob[mask]

    metric_cols = st.columns(6)
    metric_cols[0].metric("Visible Loads", len(scope_df))
    metric_cols[1].metric("Verification", int(scope_df["Dispatch Lane"].eq("Verification").sum()))
    metric_cols[2].metric("Planning", int(scope_df["Dispatch Lane"].eq("Planning").sum()))
    metric_cols[3].metric("Execution", int(scope_df["Dispatch Lane"].eq("Execution").sum()))
    metric_cols[4].metric("Exceptions", int(scope_df["Exception Count"].gt(0).sum()))
    metric_cols[5].metric("Billing Ready", int(scope_df["Status"].isin(["POD Received", "Ready for ProfitTools"]).sum()))

    exception_counts = _load_exception_summary(scope_df)
    exception_labels = ["Late appointment", "No PIN", "Customer waiting", "Waiting driver", "Port hold", "Ready for billing"]
    exception_cols = st.columns(len(exception_labels))
    for idx, label in enumerate(exception_labels):
        exception_cols[idx].metric(label, int(exception_counts.get(label, 0)))

    if scope_df.empty:
        st.info(f"No {selected_type} loads match the current Dispatch Board filters.")
    else:
        workflow = _dispatch_workflow_for_type(selected_type)
        sorted_df = scope_df.sort_values(
            ["Dispatch Lane Sort", "Dispatch Action Sort", "Exception Count", "Delivery Date Parsed", "LFD Parsed", "_row_id"],
            ascending=[True, True, False, True, True, True],
            na_position="last",
        )
        for lane_name, actions in workflow.items():
            lane_df = sorted_df[sorted_df["Dispatch Lane"].eq(lane_name)].copy()
            st.markdown(f"### {lane_name}")
            lane_cols = st.columns(len(actions), gap="small")
            for idx, (action_key, action_label) in enumerate(actions):
                with lane_cols[idx]:
                    action_df = lane_df[lane_df["Dispatch Action"].eq(action_key)].copy()
                    st.markdown(
                        f"""
                        <div style="
                            background:#f8fafc;
                            border:1px solid #cbd5e1;
                            border-radius:8px;
                            padding:8px;
                            margin-bottom:8px;
                            text-align:center;
                            min-height:58px;
                        ">
                            <div style="font-size:12px;font-weight:800;color:#0f172a;">{escape(action_label)}</div>
                            <div style="font-size:20px;font-weight:900;color:#0f172a;">{len(action_df)}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if action_df.empty:
                        st.caption("No loads")
                    else:
                        for card_idx, (_, row) in enumerate(action_df.head(30).iterrows()):
                            _render_dispatch_action_card(row, action_label, f"{lane_name}_{action_key}_{card_idx}")

    selected_row_id = st.session_state.get("dispatch_board_selected_row_id")
    if selected_row_id is None:
        st.caption("Open any load card to review dispatch details, sync port data, request PIN, update status, or send the driver packet.")
        return

    selected_df = board_df[board_df["_row_id"].astype(int).eq(int(selected_row_id))].copy() if "_row_id" in board_df.columns else pd.DataFrame()
    if selected_df.empty:
        st.warning("The selected load is no longer available.")
        if st.button("Clear Dispatch Selection", use_container_width=True):
            st.session_state.pop("dispatch_board_selected_row_id", None)
            st.rerun()
        return

    clear_cols = st.columns([4, 1])
    with clear_cols[0]:
        st.markdown("### Selected Load")
    with clear_cols[1]:
        if st.button("Clear Selection", key="clear_dispatch_board_selection", use_container_width=True):
            st.session_state.pop("dispatch_board_selected_row_id", None)
            st.rerun()
    render_dispatch_workspace(selected_df.iloc[0])


def render_active_status_view(df: pd.DataFrame) -> None:
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
    work_df["TYPE"] = work_df.get("TYPE", pd.Series("", index=work_df.index)).astype(str).str.strip()
    readiness_details = work_df.apply(lambda row: _load_readiness_details(row, include_documents=False), axis=1)
    work_df["Readiness %"] = readiness_details.apply(lambda details: int(details.get("score", 0)))
    work_df["Next Action"] = readiness_details.apply(lambda details: details.get("next_action", ""))
    work_df["Exceptions"] = readiness_details.apply(lambda details: ", ".join(details.get("exceptions", [])))
    work_df["Department Queue"] = work_df.apply(_load_department_queue, axis=1)
    active_statuses = [status for status in LOAD_STATUS_FLOW if status not in CLOSED_STATUSES]

    filter_cols = st.columns([1, 1, 1, 1.2, 2])
    with filter_cols[0]:
        type_filter = st.selectbox(
            "Load Type",
            ["All Types"] + LOAD_TYPE_TABS,
            index=0,
            key="active_status_type_filter",
        )
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
    if type_filter != "All Types":
        work_df = work_df[work_df["TYPE"].eq(type_filter)].copy()
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
    render_dispatch_workspace(selected_load)


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
            current_order_status = _safe_str(selected_load.get("Status", "New"))
            order_status_options = list(ORDER_MANAGEMENT_STATUSES)
            if current_order_status and current_order_status not in order_status_options:
                order_status_options.insert(0, current_order_status)
            status = st.selectbox(
                "Status",
                order_status_options,
                index=order_status_options.index(current_order_status)
                if current_order_status in order_status_options else 0,
                format_func=lambda value: ORDER_MANAGEMENT_STATUS_LABELS.get(value, value),
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
        if st.button("Mark Booking Verified", key=f"quick_booking_verified_{safe_context}_{selected_row_id}", use_container_width=True):
            DispatchDatabaseClient().update_row_fields(
                selected_row_id,
                {
                    "Status": "Booking Verified",
                    "Dispatcher Notes": notes.strip() or "Order reviewed and booking verified. Next action: verify booking with Port Houston.",
                },
            )
            st.session_state.pop("orders_management_selected_row_id", None)
            st.session_state.pop("orders_management_selected_context", None)
            refresh_data()
            st.success("Order marked Booking Verified.")
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
    st.caption("Review newly created orders, resolve missing information, mark bookings verified, or cancel bad orders before dispatch work begins.")

    work_df = df.copy()

    new_df = work_df[work_df["Status"].eq("New")].copy()
    missing_info_df = work_df[work_df["Status"].eq("Hold/Need Info")].copy()
    verified_df = work_df[work_df["Status"].eq("Booking Verified")].copy()
    cancelled_df = work_df[work_df["Status"].eq("Cancelled")].copy()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("New", len(new_df))
    k2.metric("Missing Info", len(missing_info_df))
    k3.metric("Booking Verified", len(verified_df))
    k4.metric("Cancel", len(cancelled_df))

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
        styled_type_df = sorted_type_df[display_cols].style.apply(_status_row_style, axis=1)

        event = st.dataframe(
            styled_type_df,
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
        "New",
        "Missing Info",
        "Booking Verified",
        "Cancel",
    ]
    queue_map = {
        "New": new_df,
        "Missing Info": missing_info_df,
        "Booking Verified": verified_df,
        "Cancel": cancelled_df,
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


def _update_load_columns_if_present(load_id: int, updates: dict) -> list[str]:
    existing_columns = _existing_load_columns()
    safe_updates = {
        column: value
        for column, value in (updates or {}).items()
        if column in existing_columns and column not in {"id", "created_at", "updated_at"}
    }
    if not safe_updates:
        return []
    set_clause = ", ".join([f"{column} = :{column}" for column in safe_updates])
    params = dict(safe_updates)
    params["load_id"] = int(load_id)
    execute(
        f"""
        update loads
        set {set_clause},
            updated_at = now()
        where id = :load_id
        """,
        params,
    )
    return list(safe_updates.keys())


def _port_houston_core_updates_from_records(load_row: dict, unit_record: dict | None, booking_record: dict | None) -> tuple[dict, dict]:
    core_updates: dict = {}
    extra_updates: dict = {}
    notes = _safe_str(load_row.get("Dispatcher Notes", ""))

    if unit_record:
        summary = summarize_unit(unit_record)
        if _safe_str(summary.get("Container", "")) and not _safe_str(load_row.get("Container Number", "")):
            core_updates["Container Number"] = summary["Container"]
        if _safe_str(summary.get("Size", "")) and not _safe_str(load_row.get("Size", "")):
            core_updates["Size"] = summary["Size"]
        if _safe_str(summary.get("Facility", "")) and not _safe_str(load_row.get("Port", "")):
            core_updates["Port"] = summary["Facility"]
        if _safe_str(summary.get("Line", "")):
            extra_updates["steamship_line"] = summary["Line"]
        if _safe_str(summary.get("Facility", "")):
            extra_updates["terminal"] = summary["Facility"]
        if _safe_str(summary.get("Return Location", "")):
            extra_updates["empty_return_location"] = summary["Return Location"]
        if _safe_str(summary.get("Position", "")):
            extra_updates["current_location"] = summary["Position"]
        notes = _append_port_houston_notes(notes, summary)

    if booking_record:
        booking = _safe_str(booking_record.get("nbr", ""))
        if booking and not _safe_str(load_row.get("Booking Number", "")):
            core_updates["Booking Number"] = booking
        client_ref = _safe_str(booking_record.get("clientRefNo", ""))
        if client_ref and not _safe_str(load_row.get("Reference Number", "")):
            core_updates["Reference Number"] = client_ref
        if _safe_str(booking_record.get("destination", "")) and not _safe_str(load_row.get("Warehouse", "")):
            core_updates["Warehouse"] = _safe_str(booking_record.get("destination", ""))

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
            core_updates["Size"] = size
        if _safe_str(booking_record.get("lineId", "")):
            extra_updates["steamship_line"] = _safe_str(booking_record.get("lineId", ""))
        if _safe_str(get_nested(booking_record, "visit.visitId")):
            extra_updates["vessel_name"] = _safe_str(get_nested(booking_record, "visit.visitId"))
        if _safe_str(booking_record.get("latestDate", "")) and not _safe_str(load_row.get("Document Cutoff", "")):
            core_updates["Document Cutoff"] = _safe_str(booking_record.get("latestDate", ""))

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
        notes = _append_port_houston_notes(notes, summary)

    if unit_record or booking_record:
        core_updates["Dispatcher Notes"] = notes
        if _safe_str(load_row.get("Status", "")) in {"Booking Verified", "Awaiting Appointment"}:
            core_updates["Status"] = "Port Verified"

    return core_updates, extra_updates


def _render_load_port_houston_panel(selected_load, readiness: dict) -> None:
    load_id = int(selected_load["_row_id"])
    default_container = _safe_str(selected_load.get("Container Number", ""))
    default_booking = _safe_str(selected_load.get("Booking Number", ""))

    st.markdown("### Port Sync")
    st.caption("Sync Port Houston data after the order is created and before dispatch. This keeps terminal, availability, LFD/return notes, and appointment context attached to the load.")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Port Verified", "Yes" if readiness.get("port_verified") else "No")
    p2.metric("Terminal", _first_present(selected_load, ["terminal", "Port"], "-"))
    p3.metric("PIN / Appt", _load_pin_display(selected_load))
    p4.metric("Next Action", readiness.get("next_action", "-"))

    c1, c2 = st.columns(2)
    container_value = c1.text_input("Container", value=default_container, key=f"load_port_sync_container_{load_id}")
    booking_value = c2.text_input("Booking", value=default_booking, key=f"load_port_sync_booking_{load_id}")

    if st.button("Sync Port Data", key=f"load_port_sync_{load_id}", use_container_width=True):
        if not container_value.strip() and not booking_value.strip():
            st.error("Container or booking is required for Port Houston sync.")
        else:
            client = _get_port_houston_client_or_none()
            if client:
                unit_record = None
                booking_record = None
                errors = []
                if container_value.strip():
                    try:
                        unit_data = client.get_inventory_units(container=container_value)
                        unit_records = content_records(unit_data)
                        unit_record = unit_records[0] if unit_records else None
                        _store_port_houston_result(f"load_port_unit_result_{load_id}", unit_data, "Container / Unit", container_value, load_id)
                    except Exception as exc:
                        errors.append(f"Container lookup failed: {exc}")
                        _log_port_houston_event(action_type="lookup", lookup_type="Container / Unit", request_reference=container_value, load_id=load_id, status="failed", error_message=str(exc))
                if booking_value.strip():
                    try:
                        booking_data = client.get_bookings(booking=booking_value)
                        booking_records = content_records(booking_data)
                        booking_record = booking_records[0] if booking_records else None
                        _store_port_houston_result(f"load_port_booking_result_{load_id}", booking_data, "Booking", booking_value, load_id)
                    except Exception as exc:
                        errors.append(f"Booking lookup failed: {exc}")
                        _log_port_houston_event(action_type="lookup", lookup_type="Booking", request_reference=booking_value, load_id=load_id, status="failed", error_message=str(exc))

                core_updates, extra_updates = _port_houston_core_updates_from_records(selected_load, unit_record, booking_record)
                updated_fields = []
                if core_updates:
                    DispatchDatabaseClient().update_row_fields(load_id, core_updates)
                    updated_fields.extend(core_updates.keys())
                updated_fields.extend(_update_load_columns_if_present(load_id, extra_updates))

                _log_port_houston_event(
                    action_type="load_port_sync",
                    lookup_type="Container / Booking",
                    request_reference=container_value or booking_value,
                    load_id=load_id,
                    status="failed" if errors and not updated_fields else "success",
                    error_message="; ".join(errors),
                    response_summary={"updated_fields": updated_fields, "unit_found": bool(unit_record), "booking_found": bool(booking_record)},
                )
                if errors:
                    st.warning("; ".join(errors))
                if updated_fields:
                    refresh_data()
                    st.success("Port data synced. Updated: " + ", ".join(updated_fields))
                    st.rerun()
                elif not errors:
                    st.info("Port Houston returned no matching container or booking records.")

    unit_records = _render_port_houston_result(f"load_port_unit_result_{load_id}", mode="unit")
    booking_records = _render_port_houston_result(f"load_port_booking_result_{load_id}")

    st.divider()
    st.markdown("### Appointment / PIN")
    pin_requirements = []
    if not _status_at_or_after(_safe_str(selected_load.get("Status", "")), "Booking Verified"):
        pin_requirements.append("booking verified")
    if not readiness.get("port_verified"):
        pin_requirements.append("port verified")
    if not _load_has_driver(selected_load):
        pin_requirements.append("driver assigned")
    if not _load_has_truck(selected_load):
        pin_requirements.append("truck assigned")
    if not _first_present(selected_load, ["Port", "terminal"], ""):
        pin_requirements.append("terminal confirmed")
    if not _first_present(selected_load, ["Delivery Need Date", "delivery_need_date"], ""):
        pin_requirements.append("pickup/delivery date")
    if pin_requirements:
        st.warning("Before requesting PIN/appointment: " + ", ".join(pin_requirements))
    else:
        st.success("Ready for Port PIN / appointment request.")

    pin_c1, pin_c2, pin_c3 = st.columns(3)
    pin_driver = pin_c1.text_input("Driver", value=_safe_str(selected_load.get("Driver Name", "")), key=f"load_pin_driver_{load_id}")
    pin_truck = pin_c2.text_input("Truck License / Truck #", value=_safe_str(selected_load.get("Truck Assigned", "")), key=f"load_pin_truck_{load_id}")
    pin_chassis = pin_c3.text_input("Chassis", value=_safe_str(selected_load.get("Chassis", "")), key=f"load_pin_chassis_{load_id}")

    pin_d1, pin_d2, pin_d3 = st.columns(3)
    pin_tran_label = pin_d1.selectbox("Transaction Type", list(PORT_HOUSTON_APPOINTMENT_TRAN_TYPES.keys()), key=f"load_pin_tran_type_{load_id}")
    pin_date = pin_d2.date_input("Requested Date", value=date.today(), key=f"load_pin_date_{load_id}")
    pin_time = pin_d3.selectbox("Requested Time", ["06:00:00", "07:00:00", "08:00:00", "09:00:00", "10:00:00", "11:00:00", "12:00:00", "13:00:00", "14:00:00", "15:00:00", "16:00:00", "17:00:00"], key=f"load_pin_time_{load_id}")

    pin_g1, pin_g2, pin_g3 = st.columns(3)
    pin_gate = pin_g1.selectbox("Gate", ["BPT MAIN", "BCT MAIN"], key=f"load_pin_gate_{load_id}")
    pin_scac = pin_g2.text_input("Trucking Company / SCAC", value=_get_app_setting("PORT_HOUSTON_OPERATOR", "POHA"), key=f"load_pin_scac_{load_id}")
    pin_confirmation = pin_g3.text_input("PIN / Appointment #", value="", key=f"load_pin_confirmation_{load_id}")
    pin_equipment_type = st.text_input("Equipment Type", value=_safe_str(selected_load.get("Size", "")) or "40HC", key=f"load_pin_equipment_{load_id}")

    pin_payload = _build_port_houston_appointment_payload(
        action="Create",
        appointment_nbr=pin_confirmation,
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
    with st.expander("Review PIN / Appointment Payload", expanded=False):
        st.text_area("Payload", value=pin_payload, height=240, key=f"load_pin_payload_{load_id}")

    pin_save_requirements = []
    if not _status_at_or_after(_safe_str(selected_load.get("Status", "")), "Booking Verified"):
        pin_save_requirements.append("booking verified")
    if not readiness.get("port_verified"):
        pin_save_requirements.append("port verified")
    if not pin_driver.strip():
        pin_save_requirements.append("driver")
    if not pin_truck.strip():
        pin_save_requirements.append("truck")
    if not _first_present(selected_load, ["Port", "terminal"], ""):
        pin_save_requirements.append("terminal")
    if not _first_present(selected_load, ["Delivery Need Date", "delivery_need_date"], ""):
        pin_save_requirements.append("pickup/delivery date")

    if st.button("Save PIN / Appointment To Load", key=f"load_save_pin_{load_id}", use_container_width=True):
        if pin_save_requirements:
            st.error("Cannot save PIN / appointment until these items are complete: " + ", ".join(pin_save_requirements))
        elif not booking_value and not container_value:
            st.error("Booking or container is required.")
        elif not pin_truck.strip():
            st.error("Truck license / truck number is required.")
        else:
            target_status = "PIN Received" if pin_confirmation.strip() else "Ready for Appointment / PIN"
            appointment_value = pd.Timestamp.combine(pin_date, pd.to_datetime(pin_time).time()).to_pydatetime()
            note = (
                "\n\nPort Houston PIN / Appointment:"
                f"\nTransaction Type: {pin_tran_label}"
                f"\nDate/Time: {pin_date} {pin_time}"
                f"\nGate: {pin_gate}"
                f"\nPIN / Appointment #: {pin_confirmation.strip() or 'Pending'}"
                f"\nBooking: {booking_value}"
                f"\nContainer: {container_value}"
                f"\nTruck: {pin_truck}"
                f"\nDriver: {pin_driver}"
                f"\nChassis: {pin_chassis}"
            )
            DispatchDatabaseClient().update_row_fields(
                load_id,
                {
                    "Status": target_status,
                    "Driver Name": pin_driver.strip(),
                    "Truck Assigned": pin_truck.strip(),
                    "Chassis": pin_chassis.strip(),
                    "Dispatcher Notes": (_safe_str(selected_load.get("Dispatcher Notes", "")) + note).strip(),
                },
            )
            updated_extra = _update_load_columns_if_present(
                load_id,
                {
                    "pickup_reference": pin_confirmation.strip() or None,
                    "pickup_appointment": appointment_value,
                },
            )
            _log_port_houston_event(
                action_type="pin_appointment_saved",
                lookup_type="Express Pass / PIN",
                request_reference=booking_value or container_value,
                load_id=load_id,
                response_summary={
                    "status": target_status,
                    "transaction_type": pin_tran_label,
                    "date": str(pin_date),
                    "time": pin_time,
                    "gate": pin_gate,
                    "pin": pin_confirmation.strip(),
                    "payload": pin_payload,
                    "updated_extra": updated_extra,
                },
            )
            refresh_data()
            st.success("PIN / appointment details saved to the load.")
            st.rerun()


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
        if section in STATUS_LEGEND_SECTIONS:
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
    elif section == "Active Status":
        render_active_status_view(df)
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
